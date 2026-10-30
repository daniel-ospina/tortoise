#!/usr/bin/env python3
"""runner_autoscale.py — make self-hosted CI runner capacity CORRECT and AUTOMATIC on one box.

WHY THIS EXISTS (measured 2026-10-22, 19:55Z–20:30Z)
  Every runner in the `daniel-ospina/tortoise` pool was added by an ad-hoc lane
  (`runnercap2/3/4/5`, `runnerrecon`). Nothing watched capacity and nothing healed a dead
  runner, so the pool was whatever the last brief happened to ask for: cell-4 at 1 runner
  against a target of 3, listeners that die and reconnect forever, and no guard at all between
  "an 8-core box" and "how many shards it can actually hold". This file is the missing half: a
  per-box predicate that (a) DERIVES the ceiling from cores and MEASURED free memory,
  (b) converges the box to it, and (c) REFUSES to exceed it, printing the reading it used.

THE AUTHORITY IT ENCODES — owner ruling, agent-infra#2344 comment 6347825624
  "take the direction of option 3 — fill every machine to the capacity it can actually
   sustain, so merging is not the scarce resource" … "the sustainable counts are 3 on cell-1,
   3 on cell-4, 2 on cell-3" … "The runner build must refuse to add a runner when free memory
   is below a stated floor, and must report the reading it used — 'manage capacity well' has
   to be a predicate the machine evaluates, not a wish in a brief."

  ⛔ CONTRADICTION TEST. `scripts/ci_runner_gate.sh:12` records a deliberate ruling that this
  box's hardware serves CI as a FALLBACK only, because jobs here compete with the agent fleet.
  That ruling is **REOPENED by the owner** in the comment quoted above — the ruling itself says
  capacity is now a goal in its own right. So this file adds capacity openly instead of dressing
  it up as a fallback. It does NOT reverse the parts the owner did not reopen: the fallback
  LABEL stays gate-following (see `resolve_labels`), and nothing here relabels, removes, or
  re-ranks an existing runner -- with ONE exception, a version-pinned hosted label
  (`VERSION_PINNED_HOSTED_LABEL`), which re-registration DROPS, so a pin attached out of band cannot
  survive the next RE-REGISTRATION (a repair that only restarts a healthy runner never re-registers
  it). ⛔ THAT STILL HOLDS NOW THAT THE POOL MAY LEGITIMATELY CARRY THE LABEL. The install-side
  writer (`tools/install-ci-runner-x64.sh`, `CI_RUNNER_OS_LABEL=on|auto`, default `off`) turns
  `ubuntu-24.04` into OPERATOR STATE instead of an out-of-band accident, and `resolve_labels`
  carries it when a runner is re-registered from NOTHING (`ORPHANED` -- the one case with no labels
  to keep); but a runner the pool still knows goes through `_preserve_labels`, which keeps the
  class dropped, and nothing in this file re-attaches it afterwards. So the pin survives
  `--repair-labels` (which KEEPS it only where the flag's RESOLUTION claims it — `on`, or `auto` on
  a host reporting the pinned release; an `auto` that declines claims nothing, #10275) and does NOT
  survive a
  re-registration. The invariant stays OBSERVED, not ASSERTED: `read_pool()` reads the live label
  set on every pass and `_preserve_labels` drops a pin from it, but no test and no gate compares
  the live set to the invariant (`docs/ops/ci-runner-x64.md:92-96`), so re-measure it after any runner
  is registered -- `tools/install-ci-runner-x64.sh --status`' `label-coverage` block prints `N of M`
  and exits 5 exactly when the pool is ASKED for the label and answers with none (#10275).

THE CEILING IS A PREDICATE, NOT A NUMBER
    ceiling = min(core_ceiling, runners_live + headroom_by_memory)

  core_ceiling — the owner's table, from CORES (never a flat 3–5):
      >= 8 cores -> 3      4..7 cores -> 2      1..3 cores -> 1
  headroom_by_memory = max(0, (mem_available_mb - MEM_RESERVE_MB) // MEM_PER_RUNNER_MB)
  and `runners_live` is how many LISTENERS are actually alive on THIS BOX (a measured process
  count — `len(_listener_dirs())` — not an install count, and not narrowed to the installs
  `discover()` happens to name): `MemAvailable` has already paid for their memory, so the memory
  arm must be additive over them — otherwise the ceiling would shrink as runners come up, and a
  healthy box at its target would read "over ceiling". The same measured count is the fill
  operand, so a listener the install inventory does not name (`/opt/actions-runner`, or a `--root`
  that is not the install root) still counts instead of letting the box be filled past its
  ceiling.

  Measured per-shard cost (3 live shards, whole `Runner.Worker` tree, 2026-10-22):
  1431 / 1378 / 870 MB + a 2-container FalkorDB sidecar pair at 55–140 MB ⇒ 1–1.5 GB, so
  MEM_PER_RUNNER_MB=1500 and MEM_RESERVE_MB=2048. Sanity check against the owner's own readings:
  cell-3 4 cores / 5.9 GB → min(2, 2+2)=2 ✓ (the ruling's 2); cell-4 8 cores / 7.5 GB with 1
  live → min(3, 1+3)=3 ✓ (the ruling's 3); a box at 3 live with 5.5 GB free → min(3, 3+2)=3, so
  a fourth is refused by CORES with the reading printed (this is the live case measured below).

  ⛔ THIS CEILING IS NOT THE ONLY PREDICATE, AND THIS FILE DOES NOT PRETEND IT IS. The one
  sanctioned registration path — `tools/install-ci-runner-x64.sh`, in this tree since #9639 — runs
  its OWN guard before it creates anything: `COUNT*CORES_PER_RUNNER + CORES_RESERVED_FOR_AGENT_LANES
  <= ncpu` (2 + 2) and `COUNT*2 + 4 <= MemAvailable` GB. That CPU arm caps a 4-core cell at ONE
  runner while the owner's table above targets 2, so on such a box this file computes a target the
  callee will refuse, and no memory reading can change it. This file neither silently lowers the
  ceiling to the callee's model (that would abandon the owner's ruling on its own authority) nor
  averages the two (that would hide the disagreement): the pass reports
  `verdict=fill-refused-by-provisioner` and exits 5, so the under-filled box is VISIBLE instead of
  green, and which predicate binds is the open decision at #9672. ⛔ ON A CANONICAL CELL THAT IS
  UNDER TARGET, EXPECT THAT STATE TO BE STANDING: the installer's memory arm
  (`COUNT*2 + 4 <= MemAvailable` GB) needs 6 GB for a single runner, which cell-3 (5.9 GB total)
  never has, and 8 GB for two, which cell-4 (7.5 GB total) never has — so while such a cell is
  below its ceiling FILL is refused every pass until #9672 lands, and the unit reads `failed`
  continuously. That is the honest reading of a real undecided defect. ⛔ THE CONVERSE IS ALSO
  TRUE AND MUST NOT BE FORGOTTEN: FILL is only attempted while `live < target`, so a cell that is
  AT its ceiling never calls the provisioner and exits 0 (`at-ceiling`) — the disagreement is
  invisible there, and that green is correct, because nothing is being refused.

WHAT IT DOES, IN ORDER (every step idempotent; safe to run every few minutes)
  1. MEASURE   cores, MemAvailable. Unreadable ⇒ exit 2 and NO action — "cannot verify" is
               never a pass and never a guess.
  2. DISCOVER  local runner installs (`<root>/gh-runner*` holding a `.runner` config) and their
               agent names: the box's own inventory, attributable to a name and a repo.
  3. READ      the repo's pool (`gh api …/actions/runners`): name, status, busy, labels.
  4. CLASSIFY  each local install — the ORDER of these checks is the safety property
               OTHER-REPO  its `.runner` names a DIFFERENT repo. Checked FIRST: this repo's pool
                         cannot see that registration at all, so the absence below would
                         otherwise read as ORPHANED and re-register it HERE with `--replace`,
                         deleting another repo's registration and cancelling its job. Reported,
                         never touched.
               BUSY      in the pool and busy, OR a live `Runner.Worker` in its tree (checked
                         before the pool read, so a runner the pool does not list is still
                         recognised as working)
               SERVING   in the pool, online, not busy
               DEAD      in the pool, offline
               ORPHANED  in this repo's pool nowhere at all, and provably this repo's — a
                         listener whose registration is gone; it reconnects forever, takes no
                         work, and holds memory
  5. REPAIR    DEAD → restart its service unit, then WAIT for the pool to report it online; only
               if it is demonstrably still offline is it re-registered (`--replace`), and an
               UNREADABLE pool is never treated as offline. The restart primitive follows the
               HOST's service manager — systemd on Linux, the runner's own `svc.sh` (launchd) on
               macOS — and a host with NEITHER is `unverified`/`no-restart-primitive`, never
               "offline": re-registering on the strength of an attempt that could not run is
               the one destructive action this file promises not to take blindly.
               ORPHANED → re-register under its own
               name with `--replace` (adopts the stale registration; never a duplicate), keeping
               the labels it already carries. A BUSY, SERVING or OTHER-REPO runner is never
               restarted, relabelled or removed.
  6. FILL      while live < target: add ONE runner, printing `mem_available_mb` before and after,
               and stop if the next addition would leave < MEM_RESERVE_MB. A NEW install is
               created by the FLEET's installer, `install-ci-runner-x64.sh` (the one registration
               path — it downloads and sha256-checks the runner), which ships IN THIS REPO at
               `tools/install-ci-runner-x64.sh` (landed by #9639), resolved from the candidates
               `provision()` lists. When no candidate resolves on this box the step REFUSES —
               `verdict=fill-blocked`, exit 0 — rather than improvising a second registration
               path. ⛔ When the installer's OWN guard refuses the count this tool asked for, the
               step reports `verdict=fill-refused-by-provisioner` and exits 5, NOT 0: the box is
               under target and the one sanctioned path will not fill it, so a timer that read
               that as success reported a permanently-under-filled box as green. Measurement,
               repair (restart / re-register), `--want`, `--status` and `--dry-run` are
               unaffected by either.
  7. REFUSE    beyond the ceiling nothing is added and the reading is printed. `--want N` is the
               explicit-request form of the same guard; `--status` is the read-only form.

⛔ THE THREE THINGS IT WILL NOT DO
  * exceed the measured ceiling to chase throughput — a memory-starved shard fails for reasons
    unrelated to the code, which costs more than the queue does;
  * restart, relabel or remove a runner that is serving (or may be serving) a job;
  * register with a label set it has not derived: `self-hosted`/`Linux`/`<ARCH>` are derived by
    GitHub from the host and cannot be set, `ubuntu-<version>` must stay claimed by NO
    self-hosted runner, and the OTHER architecture's marker is never claimed (that would let an
    x86 box serve an arm64 job — a false PASS, not a delay).

USAGE
  tools/runner_autoscale.py                     # converge this box to its measured ceiling
  tools/runner_autoscale.py --status            # measure + classify, change nothing
  tools/runner_autoscale.py --dry-run           # print the plan, change nothing
  tools/runner_autoscale.py --want 3            # explicit request; REFUSED above the ceiling
  tools/runner_autoscale.py --json              # one machine-readable record (fleet view)
  tools/runner_autoscale.py --repo owner/name   # pool to serve (default daniel-ospina/tortoise)

  Seams (all optional; the suite drives every one, and each defaults fail-closed):
    RUNNER_AUTOSCALE_RUNNER_ROOT   default /opt   (installs are `<root>/gh-runner*`)
    RUNNER_AUTOSCALE_MEMINFO       meminfo-shaped file (default /proc/meminfo, else `vm_stat`)
    RUNNER_AUTOSCALE_VMSTAT        `vm_stat` binary for the Darwin memory reading
    RUNNER_AUTOSCALE_NPROC         cores override (test seam; 0/garbage is REFUSED, never 0)
    RUNNER_AUTOSCALE_MACHINE       `uname -m` override for the label gate (test seam)
    RUNNER_AUTOSCALE_ALIVE_DIRS    newline-separated install dirs with a live listener (test seam
                                   for the `running` operand of the memory arm)
    RUNNER_AUTOSCALE_BUSY_DIRS     newline-separated install dirs with a live Runner.Worker (test
                                   seam for the local busy check)
    RUNNER_AUTOSCALE_STATE_DIR     default $HOME/.pi/agent/state (holds the run log)
    RUNNER_AUTOSCALE_GH            `gh` binary (default: gh on PATH)
    RUNNER_AUTOSCALE_SYSTEMCTL     `systemctl` binary (default: systemctl on PATH)
    RUNNER_AUTOSCALE_SERVICE       force the service manager: `systemd` | `launchd` | `none`
                                   (default: systemd when a systemctl resolves, else launchd on
                                   Darwin — the seam exists because the suite runs on BOTH)
    RUNNER_AUTOSCALE_CONFIG_SH      config.sh for a pre-staged install (tests)
    RUNNER_AUTOSCALE_PROVISIONER    install-ci-runner-x64.sh for a NEW install
    RUNNER_AUTOSCALE_NO_SUDO        "1" → do not prefix with sudo (tests, rootless)
    RUNNER_AUTOSCALE_ONLINE_WAIT_S  seconds to wait for a restarted runner to report online (60)
    RUNNER_AUTOSCALE_POLL_S         poll interval while waiting (5)

  ⛔ THE SEAMS ARE MEASUREMENT INPUTS, NOT CEILING OVERRIDES. There is deliberately no
  `--ceiling` / `--max-runners` flag: a box that can be told to exceed its own measurement is a
  box with no guard, which is the defect this file exists to close.

EXIT  0 converged / at-ceiling / over-ceiling / nothing to do / a REFUSAL (an answer, not a
        failure): `fill-blocked` (no provisioner resolves on this box — the installer ships in
        this repo at `tools/install-ci-runner-x64.sh`, so an absence means it is at none of the
        candidate paths here; FILL cannot run and nothing else is affected), and
        `under-target` reached WITHOUT an attempted growth — a `--status`/`--dry-run` plan (it
        changes nothing by design, so it cannot be a failed pass) or a stop at this box's own
        memory reserve (the capacity counterpart of `fill-blocked`). `over-ceiling` is here too,
        deliberately: a live count above the measured ceiling removes nothing (a runner that may
        be serving is never removed), so that pass has nothing it could have failed at.
      1 usage, or `--want N` refused (N above the measured ceiling — nothing was changed)
      2 a measurement or pool read was UNREADABLE — fail-closed. ⛔ NOT always "nothing was
        changed": a pass that already restarted/registered a runner and THEN could not
        re-measure reports exit 2 with `verdict=unmeasurable`, and its JSON record lists what it
        already did ("the actions listed above were already performed") — read the record, not
        this line, for whether the box was touched
      3 an ACTION failed (restart / registration) — the record says which
      4 a REAL pass ended `under-target` because a runner was INSTALLED and NO listener came up —
        the guard tried to grow the pool and the pool did not grow (`problem_kind=no-listener`).
        ⛔ ONE VERDICT, THREE CAUSES, and only this one is a failure of the guard — so this code
        keys on the CAUSE, never on `under-target` alone. A `--status`/`--dry-run` plan and a
        stop at the memory reserve both end `under-target` by design and exit 0 (above); mapping
        the verdict itself to a non-zero code would red the timer every interval on a healthy
        box that is simply below target, and would make the installer's install-time `--status`
        probe report FAILED on those same boxes.
      5 `fill-refused-by-provisioner` — `install-ci-runner-x64.sh`'s OWN guard refused this box
        (the count it asked for, or one of the callee's other arms — architecture, forks, DISK, an
        unreadable measurement, gate state; `die()` defaults to 2 for every one of them). ⛔ Read
        the pass note, not this line, for WHICH arm: only `cpu headroom` / `memory headroom` are
        the sizing disagreement below, and the others must be ACTIONED, never waited out. ⛔ THIS
        ONE IS NOT EXIT 0, and the difference is what the refusal MEANS: the box is under target
        and the one sanctioned registration path will not fill it, so exit 0 here is a green timer
        on an under-filled box (the false-PASS class this file exists to prevent). ⛔ When the arm
        IS the sizing rule, the tool's ceiling is NOT the binding predicate — the installer sizes
        a runner at `CORES_PER_RUNNER=2` + `CORES_RESERVED_FOR_AGENT_LANES=2`, so a 4-core box
        caps at ONE through that path while the owner's table targets 2, and no memory reading can
        change the CPU arm. Reconciling the two models is a DECISION, not an average, and it is
        open at #9672; this exit code is what makes the disagreement visible meanwhile. ⛔ AND
        THAT MEANS PERMA-AMBER IS INTENDED FOR THAT ARM, not an oversight: while a canonical cell
        is below its target, the installer's `2 GB/runner + 4 GB` reserve is stricter than that
        cell's free memory, so the refusal repeats every pass and the unit reads `failed`
        continuously until #9672 decides which predicate binds. ⛔ IT IS NOT
        ALWAYS AMBER: FILL is only attempted while `live < target`, so a cell AT its ceiling never
        calls the provisioner and exits 0 (`at-ceiling`) — the disagreement is invisible there,
        and that is correct, because nothing was refused. The amber state is deliberate and it
        is the opposite of the "installed but cries failure" case above: there the pass was
        healthy and the message was about a missing path; here the box genuinely cannot reach its
        target and a green timer would hide it. The install-time `--status` probe cannot see it
        either (it runs the tool in its no-change mode, which never attempts a provision), so the
        unit is the only signal — and the journal carries the callee's own refusal words.
"""

from __future__ import annotations

import sys

# #5128 shape: refuse a <3.12 interpreter BEFORE the imports below. A module-level 3.12-only name
# would otherwise fail first, with an unattributed traceback instead of this message — and a lane
# reading "the tool is broken" eschews the guard and hand-rolls a runner count. The gate that
# enforces this shape is tests/test_entry_point_python_guard.py, not a style rule.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/runner_autoscale.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"uv run python tools/runner_autoscale.py",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── the sizing predicate (owner ruling, agent-infra#2344 comment 6347825624) ────────────────────
MEM_PER_RUNNER_MB = 1500
MEM_RESERVE_MB = 2048
CORE_TABLE = ((8, 3), (4, 2), (1, 1))

# The pool's own labels. `FALLBACK_LABEL` is attached only while the fallback is engaged —
# baking it in would make `ci_runner_gate.sh`'s `current_mode` read `local` forever.
FALLBACK_LABEL = "ubuntu-latest"
ARM_POOL_LABEL = "self-hosted-arm64"
ARCH_LABEL = {
    "x86_64": "self-hosted-x64",
    "amd64": "self-hosted-x64",
    "aarch64": "self-hosted-arm64",
    "arm64": "self-hosted-arm64",
}

#: `ubuntu-24.04`-shaped labels — a VERSION-pinned GitHub-hosted label. The pool's DEFAULT label set
#: is `ubuntu-latest` alone (`docs/ops/ci-runner-x64.md:25-26`) — though since the 2026-10-29 #6897
#: mitigation the installer CAN attach it (`CI_RUNNER_OS_LABEL=on|auto`, #10275).
#:
#: The class is bounded by what GitHub's hosted images ANSWER TO, not by a spelling: the same label
#: table publishes the arm64 variants `ubuntu-24.04-arm` / `ubuntu-22.04-arm` / `ubuntu-26.04-arm`
#: (`github/docs` `data/reusables/actions/supported-github-runners.md`, checked 2026-10-27), and a
#: job pinned to one of those is escaping this pool exactly as a bare version pin does. The suffixed
#: shape is in the class as defence in depth over the WHOLE hosted version family, NOT because it is
#: the likeliest one to be attached here: this runbook is the x64 pool
#: (`docs/ops/ci-runner-x64.md:13`), and the ARM64 VMs register their own markers (`:52-53`), so an
#: `-arm` pin on this box is the LEAST likely — and it is dropped anyway. A custom label that merely
#: starts `ubuntu-<digits>` (e.g. `ubuntu-2204-tools`) is NOT in the class and must still be
#: preserved.
VERSION_PINNED_HOSTED_LABEL = re.compile(r"ubuntu-\d+(?:\.\d+)*(?:-arm)?", re.IGNORECASE)

DEFAULT_REPO = "daniel-ospina/tortoise"


class Unmeasurable(Exception):
    """A measurement or pool read could not be taken. Never read as a value."""


class ActionFailed(Exception):
    """An action (restart / registration / provision) failed. Retried on the next pass."""


class ProvisionerRefused(ActionFailed):
    """The provisioner's OWN guard refused (exit 2). A refusal — but an UNDER-TARGET one.

    ⛔ NOT AN ANSWER THAT EXITS 0 ANY MORE. A refusal here means the box is below its target and
    the one sanctioned registration path will not fill it, so the pass reports
    `verdict=fill-refused-by-provisioner` and exits 5 instead of a green timer over a box that
    cannot grow — the false PASS that a "a refusal is an answer, exit 0" reading created.
    ⛔ TWO KINDS OF REFUSAL ARRIVE HERE, AND THEY ARE NOT THE SAME FACT: `die()` in
    `tools/install-ci-runner-x64.sh:234` exits 2 for EVERY guard arm, so this is either the
    sizing disagreement (`cpu headroom` / `memory headroom` — the tool computed a count the
    callee's `COUNT*2 + 2 <= ncpu` / `COUNT*2 + 4 <= MemAvailable` guard rejects, open at #9672)
    or one of the callee's other arms (architecture, forks, disk, an unreadable measurement, gate
    state). The note the fill loop writes is keyed on the callee's OWN words so the second kind is
    never dressed up as the first; only the first is a waiting game.
    """


class ProvisionerAbsent(ActionFailed):
    """No provisioner resolves on this box — a DEPENDENCY/MISCONFIGURATION, not this pass.

    ⛔ A SIBLING OF `ProvisionerRefused`, NOT A SUBCLASS — and that is the exit contract, not a
    style choice. This exception is about WHERE THE INSTALLER IS; `ProvisionerRefused` is about
    what it will ACCEPT. Only the second one means "the box is under target and cannot be filled"
    (exit 5). `install-ci-runner-x64.sh` ships in THIS repo (`tools/install-ci-runner-x64.sh`,
    landed by #9639), so an absence means no candidate path here holds it — the installed run copy
    lives at `~/.pi/agent/scripts/`, a timer's cwd is not a shell's, and
    `install-runner-autoscale-schedule.sh` pins the path it finds into the unit. Reporting that as
    `ActionFailed` made every scheduled pass on a BELOW-CEILING box exit 3 — a red unit every
    interval, forever, over a path setting — so it is a REFUSAL with a named cause
    (`fill-blocked`), exit 0, whose message names the file, its home in this repo, and the env var
    that fixes it. Subclassing `ProvisionerRefused` instead (as this class did while both exited
    0) would hand it exit 5 the moment a handler stopped listing it first: a perma-red unit over a
    path setting. The next pass re-evaluates (idempotent), and every other capability —
    measurement, repair, `--want`, `--status`, `--dry-run` — still works.
    """


# ── measurement ────────────────────────────────────────────────────────────────────────────────
def cores(nproc: str | None = None) -> int:
    """Cores on this box. Unreadable/garbage is an ERROR, never 0 — 0 would silence every gate."""
    raw = nproc if nproc is not None else os.environ.get("RUNNER_AUTOSCALE_NPROC") or os.cpu_count()
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise Unmeasurable(f"cores unreadable ({raw!r})") from exc
    if n < 1:
        raise Unmeasurable(f"cores not positive ({n})")
    return n


def mem_available_mb(meminfo: str | None = None) -> int:
    """MemAvailable in MiB — from a meminfo-shaped file, /proc/meminfo, or `vm_stat` (Darwin).

    ⛔ MemAvailable, not MemFree: MemFree excludes reclaimable page cache, so a healthy 15 GB box
    with a warm cache reads ~0.5 GB "free" and would refuse every addition forever.
    ⛔ A missing/garbled value raises — an absent reading is never 0 MB and never "plenty".
    ⛔ BOTH platforms have a real reading. macOS has no /proc, so the launchd leg the installer
    arms would otherwise fire, measure nothing and exit 2 UNMEASURABLE forever — the "installed
    but inert" class this repo keeps rediscovering, one platform over from the one fixed in
    round 2. `vm_stat` carries its own page size, so one command is the whole reading.
    """
    seam = meminfo or os.environ.get("RUNNER_AUTOSCALE_MEMINFO")
    if seam:
        return _meminfo_available_mb(Path(seam))
    proc_meminfo = _proc_meminfo_path()
    if proc_meminfo.exists():
        return _meminfo_available_mb(proc_meminfo)
    if sys.platform == "darwin":
        return _darwin_available_mb()
    raise Unmeasurable(
        f"no memory reading on this platform ({sys.platform}): /proc/meminfo is absent and only "
        "the linux/darwin arms exist — set RUNNER_AUTOSCALE_MEMINFO to a meminfo-shaped file"
    )


def _proc_meminfo_path() -> Path:
    """The Linux reading's path. A function so a test can point it at a non-existent file."""
    return Path("/proc/meminfo")


def _meminfo_available_mb(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise Unmeasurable(f"cannot read {path} ({exc.strerror or exc})") from exc
    m = re.search(r"^MemAvailable:\s+(\d+)\s+kB", text, re.MULTILINE)
    if not m:
        raise Unmeasurable(f"no MemAvailable line in {path}")
    return int(m.group(1)) // 1024


def _darwin_available_mb() -> int:
    """MemAvailable in MiB from `vm_stat` — free + inactive + speculative pages.

    That is the macOS counterpart of MemAvailable: those three classes are what the kernel can
    hand out without paging anything out (wired and active are not). The page size comes from the
    SAME output's header, so a second binary cannot disagree with the page counts. Anything
    unparseable RAISES — a half-read `vm_stat` is not a number, and never "plenty of memory".
    """
    binary = os.environ.get("RUNNER_AUTOSCALE_VMSTAT") or "vm_stat"
    try:
        proc = subprocess.run([binary], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unmeasurable(f"vm_stat could not run ({binary}: {exc})") from exc
    if proc.returncode != 0:
        raise Unmeasurable(f"vm_stat exited {proc.returncode}: {(proc.stderr or '').strip()[:200]}")
    text = proc.stdout or ""
    size = re.search(r"page size of (\d+) bytes", text)
    if not size:
        raise Unmeasurable("vm_stat carried no page size — cannot convert its page counts")
    page = int(size.group(1))
    if page <= 0:
        raise Unmeasurable(f"vm_stat reported a non-positive page size ({page})")
    pages = 0
    for label in ("Pages free", "Pages inactive", "Pages speculative"):
        m = re.search(rf"^{label}:\s+(\d+)", text, re.MULTILINE)
        if not m:
            raise Unmeasurable(f"vm_stat carried no '{label}' line")
        pages += int(m.group(1))
    return (pages * page) // (1024 * 1024)


def core_ceiling(ncpu: int) -> int:
    for floor, ceil in CORE_TABLE:
        if ncpu >= floor:
            return ceil
    return 1


def ceiling(ncpu: int, mem_mb: int, running: int) -> tuple[int, str, str]:
    """(ceiling, bound, reading). `running` = listeners ALREADY alive on this box."""
    by_core = core_ceiling(ncpu)
    headroom = max(0, (mem_mb - MEM_RESERVE_MB) // MEM_PER_RUNNER_MB)
    by_mem = max(0, running) + headroom
    value = min(by_core, by_mem)
    bound = "cores" if by_core <= by_mem else "memory"
    reading = (
        f"ncpu={ncpu} mem_available_mb={mem_mb} running={max(0, running)} "
        f"per_runner_mb={MEM_PER_RUNNER_MB} reserve_mb={MEM_RESERVE_MB} "
        f"by_cores={by_core} by_memory={by_mem} bound={bound} ceiling={value}"
    )
    return value, bound, reading


# ── local inventory ────────────────────────────────────────────────────────────────────────────
@dataclass
class LocalRunner:
    """One runner INSTALL on this box, as the box sees it (not as the pool reports it)."""

    directory: Path
    name: str
    repo: str
    alive: bool = False

    @property
    def unit(self) -> str:
        slug = self.repo.rstrip("/").removeprefix("https://github.com/")
        return f"actions.runner.{slug.replace('/', '-')}.{self.name}.service"


def _repo_slug(value: str) -> str:
    """`https://github.com/o/n`, `github.com/o/n`, `o/n`, trailing slash → `o/n`, lowercased.

    ⛔ The comparison decides whether an install may be RE-REGISTERED, so it must not be fooled
    by spelling: `.runner` writes a URL with no trailing slash today and a trailing slash in some
    runner versions, and a case difference must not flip the answer in the UNSAFE direction
    ("another repo" read as "same" would hijack it; the reverse only declines to act).
    """
    v = (value or "").strip().rstrip("/").removesuffix(".git")
    for prefix in ("https://", "http://", "ssh://git@", "git@"):
        if v.startswith(prefix):
            v = v[len(prefix) :]
    v = v.removeprefix("github.com/").removeprefix("github.com:")
    return v.lower()


def _read_runner_config(directory: Path) -> tuple[str, str]:
    """(agentName, gitHubUrl) from `<dir>/.runner`. Raises when it is not readable.

    ⛔ A directory is not an install. The ONLY accept condition is the `.runner` config the
    runner itself wrote at registration — that is what makes an install attributable to a name
    and a repo. (`utf-8-sig`: some runner versions write a BOM, so a line regex would miss it.)
    """
    cfg = directory / ".runner"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError) as exc:
        raise Unmeasurable(f"{cfg} is not a readable runner config ({exc})") from exc
    name = str(data.get("agentName") or "").strip()
    repo = str(data.get("gitHubUrl") or "").strip()
    if not name or not repo:
        raise Unmeasurable(f"{cfg} has no agentName/gitHubUrl")
    return name, repo


def _listener_dirs() -> set[str]:
    """Install directories that currently have a live `Runner.Listener`.

    ⛔ This is the `running` operand of the memory arm, so it is MEASURED, never inferred from
    "an install exists": a registered-but-dead install occupies no memory, and counting it would
    inflate the ceiling by one runner on exactly the box that just lost one.
    """
    seam = os.environ.get("RUNNER_AUTOSCALE_ALIVE_DIRS")
    if seam:
        try:
            return {
                ln.strip()
                for ln in Path(seam).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            }
        except OSError as exc:
            raise Unmeasurable(f"alive-dirs seam unreadable ({seam}: {exc})") from exc
    try:
        proc = subprocess.run(
            [os.environ.get("RUNNER_AUTOSCALE_PS") or "ps", "-eo", "args"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # ⛔ FAIL CLOSED. This is the ONLY arm that used to degrade to a value: a broken `ps`
        # returned the empty set, so `running` read 0 and the guard added a runner to a box that
        # was already at its ceiling (round 7, finding 1) — while the record printed `running=0`
        # as if it were a reading. Every other measurement raises, and so must this one.
        raise Unmeasurable(f"process list unreadable ({exc})") from exc
    if proc.returncode != 0:
        raise Unmeasurable(
            f"process list unreadable (ps exited {proc.returncode}: {(proc.stderr or '').strip()[:200]})"
        )
    dirs: set[str] = set()
    for line in proc.stdout.splitlines():
        if "Runner.Listener" not in line:
            continue
        for token in line.split():
            if "/bin/Runner.Listener" in token or token.endswith("bin/Runner.Listener"):
                dirs.add(str(Path(token).parent.parent))
    return dirs


def discover(root: str | Path, alive: set[str] | None = None) -> list[LocalRunner]:
    """Every runner install under `root/gh-runner*`, sorted by directory name.

    A sibling directory with no `.runner` is SKIPPED (a download that never registered is a fact
    about the box, not a reason to abort the pass). A `.runner` that exists and cannot be parsed
    RAISES: silently dropping it would make the box think it has one fewer runner than it has,
    and it would then register a duplicate under a new name.

    ⛔ `alive` is the caller's MEASURED listener set, not a second reading taken here: this
    function answers "which installs does the inventory name", and the live count the guard acts
    on is `len(_listener_dirs())` over the whole BOX. Passing the measurement in keeps one pass on
    one reading, and keeps an install directory that does not match `gh-runner*` out of the count
    only when it genuinely has no listener.
    """
    base = Path(root)
    if not base.is_dir():
        return []
    if alive is None:
        alive = _listener_dirs()
    found: list[LocalRunner] = []
    for entry in sorted(base.glob("gh-runner*")):
        if not entry.is_dir() or not (entry / ".runner").exists():
            continue
        name, repo = _read_runner_config(entry)
        found.append(LocalRunner(directory=entry, name=name, repo=repo, alive=str(entry) in alive))
    return found


# ── the pool ──────────────────────────────────────────────────────────────────────────────────
@dataclass
class PoolEntry:
    name: str
    status: str
    busy: bool
    labels: list[str] = field(default_factory=list)

    @property
    def online(self) -> bool:
        return self.status == "online"


def gh_bin() -> str:
    return os.environ.get("RUNNER_AUTOSCALE_GH") or (shutil.which("gh") or "")


def read_pool(repo: str) -> dict[str, PoolEntry]:
    """The repo's runner pool by name. Unreadable raises; an EMPTY pool is a legitimate answer.

    ⛔ `--paginate` is mandatory: `gh api` returns 30 runners per page, so a bigger pool read
    without it makes existing runners invisible — and "not in the pool" is the ORPHANED
    classification, which RE-REGISTERS. A truncated read would double-register the tail.
    ⛔ A failed read is not an empty pool: conflating them is how a network blip becomes three
    new runners.
    """
    gh = gh_bin()
    if not gh:
        raise Unmeasurable("gh is not on PATH (set RUNNER_AUTOSCALE_GH)")
    cmd = [
        gh,
        "api",
        "--paginate",
        f"repos/{repo}/actions/runners",
        "--jq",
        '.runners[] | [.name, .status, (.busy|tostring), ([.labels[].name]|join(","))] | @tsv',
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Unmeasurable(f"gh api failed to run ({exc})") from exc
    if proc.returncode != 0:
        raise Unmeasurable(
            f"gh api repos/{repo}/actions/runners exited {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    pool: dict[str, PoolEntry] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            raise Unmeasurable(f"runner row has no readable name/status: {line[:160]!r}")
        name, status, busy = parts[0], parts[1], parts[2]
        if not name or status not in ("online", "offline"):
            raise Unmeasurable(f"runner row has no readable name/status: {line[:160]!r}")
        pool[name] = PoolEntry(
            name=name,
            status=status,
            busy=(busy == "true"),
            labels=[x for x in parts[3].split(",") if x] if len(parts) > 3 else [],
        )
    return pool


def fallback_engaged(pool: dict[str, PoolEntry]) -> bool:
    """Is the ARM64 pool carrying the fallback label RIGHT NOW (the gate's own condition)?"""
    return any(ARM_POOL_LABEL in e.labels and FALLBACK_LABEL in e.labels for e in pool.values())


OS_LABEL = "ubuntu-24.04"
"""`ubuntu-24.04` — the version-pinned HOSTED label the installer attaches under
`CI_RUNNER_OS_LABEL=on|auto` (see `_os_labels`)."""


def _host_is_the_os_release() -> bool:
    """Is this host the release `OS_LABEL` names? The installer's `auto` read, same seams.

    `CI_RUNNER_OS_NAME` / `CI_RUNNER_OS_RELEASE` are the INSTALLER's own seams, honoured here for
    the same reason it honours them: the box reading this code is not always the box being labelled,
    and a test that cannot drive the read cannot pin it.

    ⛔ BOTH READ THE `:-` SPELLING, so a SET-BUT-EMPTY export means the DEFAULT, not the empty
    string: `os.environ.get(k) or default`, never `os.environ.get(k, default)` — the latter applies
    its default only when the key is ABSENT, which made `CI_RUNNER_OS_RELEASE=` (an empty export)
    resolve to `open("")` here while the installer read `/etc/os-release`. Pinned by
    `tests/test_runner_autoscale.py::test_a_set_but_empty_os_release_seam_reads_the_default_file_like_the_installer`.
    """
    if (os.environ.get("CI_RUNNER_OS_NAME") or platform.system()) != "Linux":
        return False
    try:
        release = os.environ.get("CI_RUNNER_OS_RELEASE") or "/etc/os-release"
        with open(release, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("VERSION_ID="):
                    found = line.split("=", 1)[1].strip().strip('"')
                    return found == OS_LABEL.removeprefix("ubuntu-")
    except OSError:
        return False
    return False


def _os_labels(machine: str) -> str:
    """`""` or `,ubuntu-24.04` — the rule of `install-ci-runner-x64.sh::resolve_os_label`.

    ⛔ IT IS A MIRROR, SO IT IS PINNED TO THE THING IT MIRRORS: `tests/test_runner_autoscale.py::
    test_resolve_labels_matches_the_installer_on_every_os_label_arm` runs the real installer's
    `--print-labels` beside this function across on|off|auto × host-release × arch. Without that the
    claim in `resolve_labels`' docstring was false the moment the installer grew an OS arm, and
    nothing failed — which is the review finding this closes.

    The environment IS the sanctioned channel: `install-runner-autoscale-schedule.sh` renders
    `CI_RUNNER_OS_LABEL` into the timer's own env, so a scheduled pass reads the operator's opt-in
    without a second config surface. An unknown value REFUSES instead of defaulting to `off` — the
    installer refuses it too, and a silent `off` would drop the label while looking like restraint.

    ⛔ A SET-BUT-EMPTY VALUE IS `off`, NOT A REFUSAL. Both surfaces this claims to mirror read the
    empty value as UNSET: `install-ci-runner-x64.sh` uses `case "${CI_RUNNER_OS_LABEL:-off}"` and
    `install-runner-autoscale-schedule.sh` uses `OS_LABEL_MODE="${CI_RUNNER_OS_LABEL:-off}"`. This
    function used `os.environ.get(..., "off")`, where the default applies only when the KEY IS
    ABSENT — so `CI_RUNNER_OS_LABEL=` (an empty export, which is what `export X=$SOME_UNSET_VAR`
    produces) made the installer proceed as `off` while this raised `Unmeasurable`, on the
    unattended path where nothing else says why. `or "off"` is the `:-off` spelling.
    """
    mode = os.environ.get("CI_RUNNER_OS_LABEL") or "off"
    if mode == "off":
        return ""
    if mode not in ("on", "auto"):
        raise Unmeasurable(
            f"CI_RUNNER_OS_LABEL must be off|on|auto (got {mode!r}): refusing to guess whether this "
            f"runner claims {OS_LABEL}"
        )
    # ⛔ ONLY THE ARCH THAT HOSTS THAT IMAGE MAY CLAIM IT. `ubuntu-24.04` names the x64 hosted
    # image, so an arm64 box claiming it is a false architecture claim — and it is exactly the class
    # `_preserve_labels`/`VERSION_PINNED_HOSTED_LABEL` drops again on re-registration, which would
    # make this derivation's answer vanish one pass later. The installer never meets this case (it
    # refuses a non-x86_64 install); this derivation runs on a box of either arch.
    if ARCH_LABEL.get(machine.lower()) != ARCH_LABEL["x86_64"]:
        return ""
    if mode == "on":
        return f",{OS_LABEL}"
    return f",{OS_LABEL}" if _host_is_the_os_release() else ""


def resolve_labels(pool: dict[str, PoolEntry], machine: str) -> str:
    """The label set a re-registered runner gets — arch marker, the fallback label only if the
    fallback is engaged right now, and the OS fact whenever the operator opted into it.

    This mirrors the label rule of `install-ci-runner-x64.sh::resolve_labels`
    (`tools/install-ci-runner-x64.sh:338`, in this tree since #9639) instead of inventing a
    second rule, and it is `auto`: the gate's state is the source of truth, so a runner never
    acquires a permanent claim on the pool. The reader CAN open it, which is why this file
    re-derives NOTHING it cannot check: the arch marker is this box's own `uname -m`, and the
    fallback label comes from `ci_runner_gate.sh`'s condition read off the live pool (see
    `fallback_engaged`). An unknown architecture RAISES —
    `self-hosted`/`Linux`/`<ARCH>` are host-derived and may not be set, so there is no safe
    default to fall back to.

    ⛔ THE MIRROR IS TESTED AS ONE, because it had already drifted: this function returned
    `f"{arch},{FALLBACK_LABEL}"` or `arch` and never the OS label, so re-registering a runner that
    was ABSENT from the pool (ORPHANED — the one case with no labels to preserve) silently dropped
    the `ubuntu-24.04` mitigation the repair exists to keep, with no error and no log line. That is
    the same erasure this file's own header describes, one door over. `_os_labels` now applies the
    installer's rule, and `tests/test_runner_autoscale.py::
    test_resolve_labels_matches_the_installer_on_every_os_label_arm` runs the REAL installer beside
    it over the whole matrix so the two cannot drift again in silence.
    """
    arch = ARCH_LABEL.get(machine.lower())
    if not arch:
        raise Unmeasurable(
            f"unknown architecture {machine!r}: cannot derive a label set "
            "(self-hosted/Linux/<ARCH> are host-derived and may not be set)"
        )
    os_labels = _os_labels(machine)
    if fallback_engaged(pool):
        return f"{arch},{FALLBACK_LABEL}{os_labels}"
    return f"{arch}{os_labels}"


# ── actions ───────────────────────────────────────────────────────────────────────────────────
def _run(cmd: list[str], timeout: int = 300) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


# Every way `sudo` says NO, because a refusal that is NOT recognised here is classified `failed`,
# which sends the caller to `register(..., replace=True)` — re-registering a runner that was never
# restarted, and invalidating the credentials of one that may be reconnecting. ⛔ The CANONICAL
# denial was missing from the first version: sudo's actual words are "Sorry, user X is not allowed
# to execute '…' as root on host" (a sudoers rule scoped to `/bin/systemctl` while `which` resolves
# `/usr/bin/systemctl`, a rule for `start` but not `restart`, or a `nosuid` mount), and
# "command not allowed" — the wording that WAS listed — is `doas`, not sudo.
_SUDO_REFUSED = re.compile(
    r"sudo:.*(?:password is required|no askpass program specified|a terminal is required|"
    r"no tty present)"
    r"|Sorry, user .* is not allowed to execute"
    r"|is not in the sudoers file"
    r"|must be owned by uid .* and have the setuid bit set"
    r"|effective uid is not",
    re.IGNORECASE,
)


def _sudo_prefix() -> list[str]:
    return [] if os.environ.get("RUNNER_AUTOSCALE_NO_SUDO") == "1" else ["sudo", "-n"]


def _systemctl(args: list[str]) -> list[str]:
    binary = os.environ.get("RUNNER_AUTOSCALE_SYSTEMCTL") or (
        shutil.which("systemctl") or "systemctl"
    )
    return [*_sudo_prefix(), binary, *args]


def service_manager() -> str:
    """Which service manager this HOST really has: `systemd`, `launchd`, or `` (neither).

    ⛔ ASKED, NEVER ASSUMED FROM THE PLATFORM. The restarter used to be `systemctl` unconditionally,
    and on macOS that binary does not exist: `_run` returned rc=127, the DEAD branch read the
    failure as "restart attempted, still offline" and re-registered the runner with `--replace`
    — inverting this file's own documented order (restart first, replace only if it is
    demonstrably still offline) on the one platform where nothing had been restarted at all.

    A resolvable `systemctl` (PATH or `RUNNER_AUTOSCALE_SYSTEMCTL`) wins, so the Linux lanes and
    the suite keep the systemd path on every host; otherwise Darwin (or a host carrying
    `launchctl`) is `launchd`; otherwise `` — and an empty answer is reported as UNKNOWN, never
    as "the restart failed". `RUNNER_AUTOSCALE_SERVICE` forces one branch (test seam, and the
    escape hatch for a host that resolves a systemctl it must not use).
    """
    forced = (os.environ.get("RUNNER_AUTOSCALE_SERVICE") or "").strip().lower()
    if forced:
        return "" if forced in ("none", "no", "unsupported") else forced
    if os.environ.get("RUNNER_AUTOSCALE_SYSTEMCTL") or shutil.which("systemctl"):
        return "systemd"
    if sys.platform == "darwin" or shutil.which("launchctl"):
        return "launchd"
    return ""


def restart_runner(runner: LocalRunner) -> tuple[str, str]:
    """Restart a runner's service. Returns `(ok | failed | unsupported, detail)`.

    NEVER called on a BUSY runner (the caller decides). ⛔ THREE STATES, like `wait_online`: a host
    with no service manager has not told us the runner is offline, so `unsupported` must not be
    folded into `failed` — the caller would then re-register (`--replace`) on the strength of an
    attempt that could not run. macOS restarts through the runner's OWN `svc.sh` (its launchd
    primitive: `svc.sh stop` + `svc.sh start`), which is the only supported way to bounce a
    launchd-run agent.
    """
    manager = service_manager()
    if manager == "systemd":
        rc, out = _run(_systemctl(["restart", runner.unit]), timeout=180)
        # ⛔ COULD NOT BE INVOKED IS NOT "STILL OFFLINE". 127 is `_run`'s own code for "the command
        # did not run" (missing binary), and a REFUSED ESCALATION is a different event with a
        # DIFFERENT rc: `sudo -n` exits 1 having run nothing ("sudo: a password is required" —
        # measured on the dev box, and recorded in the launchd branch below). The shipped unit runs
        # as `User=$(id -un)` and does NOT set RUNNER_AUTOSCALE_NO_SUDO, so that is the default; read
        # as `failed` the caller skips `wait_online` and re-registers with `--replace`, invalidating
        # the credentials of a runner that may be reconnecting.
        #
        # ⛔ CLASSIFIED ON SUDO'S OWN OUTPUT — never with a `sudo -n true` pre-probe. A sudoers rule
        # is commonly COMMAND-SCOPED (`NOPASSWD: /usr/bin/systemctl`, or the restart line alone):
        # that denies `true` while allowing exactly the restart we need, so a pre-probe refuses
        # forever a repair the code performed correctly, on every fire, and deletes a working
        # capability to guard against a case the output already names unambiguously.
        sudo_refused = rc != 0 and _SUDO_REFUSED.search(out) is not None
        status = "ok" if rc == 0 else ("unsupported" if (rc == 127 or sudo_refused) else "failed")
        return status, f"systemctl restart {runner.unit} rc={rc} {out[:200]}"
    if manager == "launchd":
        svc = runner.directory / "svc.sh"
        if not svc.exists():
            return (
                "unsupported",
                f"no restart primitive for {runner.name}: launchd host and {svc} is absent "
                "(a launchd agent is restarted by the runner's own svc.sh) — NOT treated as "
                "offline, so nothing is re-registered",
            )
        # ⛔ NO `sudo` HERE. A launchd agent lives in the USER's gui domain, and `svc.sh` drives
        # launchctl for that user: escalation is wrong twice over — root cannot address
        # `gui/<uid>`, and on a box without passwordless sudo `sudo -n` fails outright (rc 1),
        # which the caller would then read as a failed restart and answer with `--replace`
        # (measured on the dev box: "sudo: a password is required", rc=1, on the shipped default).
        rc, out = _run([str(svc), "stop"], timeout=180)
        if rc == 127:
            return "unsupported", f"svc.sh could not be invoked ({out[:200]})"
        if rc != 0:
            return "failed", f"svc.sh stop {runner.name} rc={rc} {out[:200]}"
        rc, out = _run([str(svc), "start"], timeout=180)
        if rc == 127:
            return "unsupported", f"svc.sh could not be invoked ({out[:200]})"
        return "ok" if rc == 0 else "failed", f"svc.sh restart {runner.name} rc={rc} {out[:200]}"
    return (
        "unsupported",
        f"no service manager on this host (neither systemctl nor launchd), so {runner.name} "
        "cannot be restarted — NOT treated as offline, so nothing is re-registered",
    )


def wait_online(repo: str, name: str, timeout_s: int, poll_s: int) -> str:
    """Poll the pool for `name`. Returns `online` | `offline` | `unverified`.

    ⛔ THREE STATES, NOT A BOOL. A failed pool read is not evidence that the runner did not come
    back: folding it into `False` made the caller say "still offline" about something it never
    looked at, and act on that (re-register with `--replace`). `unverified` is a measurement
    failure and the caller must NOT act on it.
    """
    deadline = time.monotonic() + max(0, timeout_s)
    while True:
        try:
            if read_pool(repo).get(name, PoolEntry(name, "offline", False)).online:
                return "online"
        except Unmeasurable:
            return "unverified"
        if time.monotonic() >= deadline:
            return "offline"
        time.sleep(max(1, poll_s))


def registration_token(repo: str) -> str:
    """A one-hour registration token. Unreadable is `ActionFailed` — never a guess."""
    gh = gh_bin()
    if not gh:
        raise ActionFailed("gh is not on PATH (set RUNNER_AUTOSCALE_GH)")
    rc, out = _run(
        [
            gh,
            "api",
            "-X",
            "POST",
            f"repos/{repo}/actions/runners/registration-token",
            "--jq",
            ".token",
        ]
    )
    token = out.splitlines()[-1].strip() if out else ""
    if rc != 0 or not token:
        raise ActionFailed(f"registration token mint failed rc={rc}: {out[:300]}")
    return token


def register(directory: Path, name: str, repo: str, labels: str, replace: bool) -> str:
    """Register or RE-register an EXISTING install (`<dir>/config.sh`) and enable its unit.

    `--replace` is what makes an orphaned name reusable: the stale registration is adopted
    instead of a duplicate being created. `--unattended` is what makes it safe from a timer.
    """
    cfg = Path(os.environ.get("RUNNER_AUTOSCALE_CONFIG_SH") or (directory / "config.sh"))
    if not cfg.exists():
        raise ActionFailed(f"{cfg} does not exist — {directory} is not a runner install")
    token = registration_token(repo)
    rc, out = _run(
        [
            str(cfg),
            "--unattended",
            "--url",
            f"https://github.com/{repo}",
            "--token",
            token,
            "--name",
            name,
            "--labels",
            labels,
            "--work",
            "_work",
            *(["--replace"] if replace else []),
        ],
        timeout=600,
    )
    if rc != 0:
        raise ActionFailed(f"config.sh for {name} rc={rc}: {out[-400:]}")
    # ⛔ A REGISTERED RUNNER WITH NO ENABLED UNIT IS NOT LIVE. Configuring the unit used to be
    # best-effort, so a `svc.sh install` / `systemctl enable` failure still returned "registered",
    # the caller counted it as live and reported `converged` while no listener would ever start —
    # a false PASS on the tool's own exit-3 contract, and exactly the "runner disappears silently"
    # mode the unit exists to prevent.
    failures = enable_unit(directory, name, repo)
    if failures:
        raise ActionFailed(
            f"{name} registered but its service could not be enabled (it will not stay up): "
            + "; ".join(failures)
        )
    return f"registered {name} labels={labels}"


def enable_unit(directory: Path, name: str, repo: str) -> list[str]:
    """Install/enable the service unit for this runner. Returns the FAILURES (empty = up).

    A registered runner without an enabled unit dies with the shell that started it, which is the
    "runner disappears silently" mode; a caller that ignores this returns `converged` over a
    runner that will never start, so every step's status is checked and reported.

    ⛔ THE ENABLE MECHANISM FOLLOWS THE HOST, like the restart. `systemctl enable --now` does not
    exist on macOS, so the old unconditional call failed AFTER `config.sh` had already succeeded:
    `register()` raised `ActionFailed` for a runner that was in fact registered and up, and the
    pass reported exit 3 over a success (round 10, F1). A host with no service manager at all is
    a FAILURE here, not a silent pass — the unit genuinely cannot be enabled.
    """
    unit = f"actions.runner.{repo.replace('/', '-')}.{name}.service"
    failures: list[str] = []
    svc = directory / "svc.sh"
    manager = service_manager()
    if manager == "launchd":
        if not svc.exists():
            return [f"launchd host and {svc} is absent — the runner cannot be enabled as a service"]
        # NO `sudo` on launchd: the agent is per-USER (see `restart_runner`).
        rc, out = _run([str(svc), "install", os.environ.get("USER") or "root"], timeout=180)
        if rc != 0:
            failures.append(f"svc.sh install rc={rc} {out[-200:]}")
        rc, out = _run([str(svc), "start"], timeout=180)
        if rc != 0:
            failures.append(f"svc.sh start rc={rc} {out[-200:]}")
        return failures
    if manager != "systemd":
        return ["no service manager on this host (neither systemctl nor launchd)"]
    if svc.exists():
        rc, out = _run(
            [*_sudo_prefix(), str(svc), "install", os.environ.get("USER") or "root"], timeout=180
        )
        if rc != 0:
            failures.append(f"svc.sh install rc={rc} {out[-200:]}")
    rc, out = _run(_systemctl(["enable", "--now", unit]), timeout=180)
    if rc != 0:
        failures.append(f"systemctl enable --now {unit} rc={rc} {out[-200:]}")
    return failures


def _next_free_slot(root: str) -> int:
    """The first free slot in the provisioner's own numbering: base, base-2, base-3, ...

    Not `len(installs) + 1`. The numbering is repo-agnostic and this box carries installs that are
    NOT in it (`/opt/gh-runner-cell4-x64`, added by an ad-hoc lane), so a count over-counts and the
    next `--count` fills SEVERAL slots at once — the over-fill the ceiling forbids (round 5,
    finding 1). Walking the numbering for the first free slot makes one call one addition.

    ⛔ "OCCUPIED" MEANS ".runner PRESENT", NOT "the path exists" — that is the provisioner's own
    test (`[ ! -f "$d/.runner" ]`), and only that predicate keeps ONE call = ONE addition. A
    directory left behind by an interrupted download, or by a deregistered install, is invisible to
    `discover()` (which requires `.runner`) and FREE to the installer: calling it occupied skipped
    past it, so the single call filled the gap AND the requested slot — two runners where the
    ceiling allowed one, with the pass then reporting `over-ceiling` for a breach it had just
    created (round 6, finding 1).
    """
    base = Path(os.environ.get("RUNNER_AUTOSCALE_BASE") or (Path(root) / "gh-runner-x64"))
    index = 1
    while True:
        slot = base if index == 1 else Path(f"{base}-{index}")
        if not (slot / ".runner").exists():
            return index
        index += 1


def provision(target_count: int) -> str:
    """Create/extend the install so that runners 1..target_count exist — via the FLEET's installer.

    ⛔ This file never downloads a runner binary and never builds a `config.sh` argv for a NEW
    install: download + sha256 verification + the headroom/arch guards are the provisioner's job
    (`install-ci-runner-x64.sh`, the one registration path, which ships IN THIS REPO at
    `tools/install-ci-runner-x64.sh` — landed by #9639). It is resolved at run time from the
    candidates below — a sibling of this file, `$PWD/tools/`, or `~/.pi/agent/scripts/` — because
    the RUNNING copy of this tool lives in the last one. When none of them resolves, this REFUSES
    — `verdict=fill-blocked`, exit 0, `no provisioner` in the notes — rather than improvising a
    second, divergent registration path. `--want`, `--status`, `--dry-run`, every measurement and
    every restart/re-register repair work without it. Set `RUNNER_AUTOSCALE_PROVISIONER` to the
    installer's real path on a box whose checkout is somewhere else.
    """
    raw = os.environ.get("RUNNER_AUTOSCALE_PROVISIONER")
    # The installer is resolved, never assumed: the RUNNING copy of this tool lives in
    # ~/.pi/agent/scripts (a stable path), so a sibling lookup alone would find nothing there and
    # every add would refuse — an enforcer that can never fire. The candidate list spans the
    # installed copy's neighbours, the working directory (the cell's checkout), and $REPO/tools.
    candidates = (
        [raw]
        if raw
        else [
            str(Path(__file__).resolve().parent / "install-ci-runner-x64.sh"),
            str(Path.cwd() / "tools" / "install-ci-runner-x64.sh"),
            str(Path.home() / ".pi" / "agent" / "scripts" / "install-ci-runner-x64.sh"),
        ]
    )
    provisioner = next((c for c in candidates if c and Path(c).exists()), None)
    if not provisioner:
        # ⛔ NAMES THE FILE, ITS HOME IN THIS REPO, AND THE FIX. An earlier revision said the
        # installer "is not in this tree yet (it lands with #9639)" — false since #9639 landed,
        # and an operator acting on it looked for a dependency instead of setting a path. The
        # absence is about THIS BOX's candidate paths, not about the repository.
        raise ProvisionerAbsent(
            "no provisioner: install-ci-runner-x64.sh was found at none of this tool's candidate "
            "paths, so a NEW runner cannot be created on this box (this script never downloads a "
            "runner binary). The installer ships in this repo at "
            "tools/install-ci-runner-x64.sh; the running copy of this tool lives in "
            "~/.pi/agent/scripts, and a systemd/cron unit's cwd is not a shell's. Measurement, "
            "--want/--status/--dry-run and the restart/re-register repairs still work; set "
            "RUNNER_AUTOSCALE_PROVISIONER to the installer's path to enable FILL."
        )
    rc, out = _run(
        ["bash", provisioner, "--count", str(target_count), "--fallback", "auto"], timeout=1800
    )
    if rc == 2:
        # `install-ci-runner-x64.sh` publishes 2 as "GUARD REFUSED — nothing was attempted", and
        # `die()` uses 2 as the DEFAULT for EVERY arm — the sizing guards (cores/memory) and the
        # others (architecture, forks, disk, unreadable measurements, gate state). All of them mean
        # the box is UNDER target and the one sanctioned path will not fill it, so all of them are
        # a refusal this pass must not report as success (exit 5, `main`). ⛔ WHICH arm it was is
        # NOT decided here: the callee's own words are carried in the exception and the fill loop
        # keys its note on them, so a disk refusal is never described as the two-models
        # disagreement at #9672. Carrying the raw output is what makes that readable at all.
        raise ProvisionerRefused(f"rc=2 {out[-300:]}")
    if rc != 0:
        raise ActionFailed(f"provisioner --count {target_count} rc={rc}: {out[-400:]}")
    return f"provisioned to {target_count}: {out.splitlines()[-1][:200] if out else 'ok'}"


# ── the pass ─────────────────────────────────────────────────────────────────────────────────
def converge(
    repo: str,
    *,
    root: str = "/opt",
    want: int | None = None,
    dry_run: bool = False,
    nproc: str | None = None,
    meminfo: str | None = None,
    machine: str | None = None,
    online_wait_s: int | None = None,
    poll_s: int | None = None,
) -> dict:
    """One convergence pass: decide, then (unless dry_run) act. Raises Unmeasurable."""
    record: dict = {
        "repo": repo,
        "dry_run": dry_run,
        "actions": [],
        "notes": [],
        "verdict": "",
        "problems": [],
    }
    ncpu = cores(nproc)
    mem_mb = mem_available_mb(meminfo)
    # ⛔ ONE MEASUREMENT, TAKEN HERE, OVER THE WHOLE BOX. `discover()` answers "which installs
    # does the inventory name" — it cannot answer "how many listeners are alive", because it only
    # looks under `<root>/gh-runner*`. Counting through it dropped every listener of another
    # shape (`/opt/actions-runner`, the upstream default; any box where `--root` is not the
    # install root) out of BOTH operands, so `running=0 live=0` on a box that already held
    # listeners let the fill loop add the FULL core ceiling on top of them and then report
    # `converged` (round 9, finding 4) — the fail-open direction this guard exists to close.
    alive_dirs = _listener_dirs()
    local = discover(root, alive=alive_dirs)
    pool = read_pool(repo)
    machine = machine or os.environ.get("RUNNER_AUTOSCALE_MACHINE") or os.uname().machine
    wait_s = int(
        os.environ.get(
            "RUNNER_AUTOSCALE_ONLINE_WAIT_S", online_wait_s if online_wait_s is not None else 60
        )
    )
    poll = int(os.environ.get("RUNNER_AUTOSCALE_POLL_S", poll_s if poll_s is not None else 5))

    # 4. CLASSIFY ------------------------------------------------------------------------------
    classified: list[dict] = []
    for r in local:
        entry = pool.get(r.name)
        # ⛔ THE ORDER OF THESE BRANCHES IS THE SAFETY PROPERTY (review rounds 1–2).
        #  1. ANOTHER REPO first: `pool` is ONE repo's pool, so an install whose `.runner` names
        #     a different repo is simply ABSENT from it — indistinguishable from ORPHANED by the
        #     pool read alone. Treating that absence as ORPHANED re-registers it against `--repo`
        #     with `--replace`, which DELETES the other repo's registration and cancels whatever
        #     it is running. It is reported and never touched.
        #  2. A LIVE WORKER next: `_worker_alive` is a LOCAL process check, so it is valid for a
        #     runner the pool does not list at all — a listener that lost its broker connection
        #     can be executing while the API says nothing about it, and `--replace` would kill it.
        #  3. Only then the pool's own view.
        if _repo_slug(r.repo) != _repo_slug(repo):
            state = "OTHER-REPO"
        elif _worker_alive(r) or (entry is not None and entry.busy):
            state = "BUSY"
        elif entry is None:
            state = "ORPHANED"
        elif entry.online:
            state = "SERVING"
        else:
            state = "DEAD"
        classified.append(
            {
                "name": r.name,
                "dir": str(r.directory),
                "state": state,
                "pool_status": entry.status if entry else None,
                "busy": bool(entry and entry.busy),
                "labels": list(entry.labels) if entry else [],
                "repo": r.repo,
                "alive": r.alive,
            }
        )

    running = len(alive_dirs)
    ceil, bound, reading = ceiling(ncpu, mem_mb, running)
    record.update(
        ncpu=ncpu,
        mem_available_mb=mem_mb,
        bound=bound,
        ceiling=ceil,
        reading=reading,
        runners=classified,
        local=[{"dir": str(r.directory), "name": r.name} for r in local],
        pool_total=len(pool),
        pool_online=sum(1 for e in pool.values() if e.online),
        pool_busy=sum(1 for e in pool.values() if e.busy),
    )

    if want is not None and want > ceil:
        # ⛔ THE GUARD THE OWNER ASKED FOR, in its explicit-request form: nothing changes and the
        # reading that produced the refusal is printed.
        record["verdict"] = "refused"
        record["refusal"] = f"REFUSED: want={want} exceeds the measured ceiling {ceil} — {reading}"
        return record

    target = ceil if want is None else want

    # 5. REPAIR (DEAD / ORPHANED) — NEVER a BUSY, SERVING or OTHER-REPO runner ------------------
    # ⛔ `live` IS THE BOX TOTAL, MEASURED — not a count of our own repairs. `ceiling` is also a box
    # total (`running` includes every listener alive on this box, another repo's included, because
    # their memory is really gone from MemAvailable), so the fill decision must compare like with
    # like. Counting only OUR runners here made a box with one foreign listener add ceil−0 new ones
    # — 4 CI listeners where the owner's 8-core ruling allows 3 (round 2, finding 1). A repair does
    # not change the total (restart keeps one listener; re-registration keeps one) unless the
    # listener was actually gone, which is why only that case increments.
    live = sum(1 for c in classified if c["alive"])
    stop_pass = False
    for c in classified:
        if c["state"] in ("SERVING", "BUSY", "OTHER-REPO"):
            # OTHER-REPO is reported and never repaired: it is not this repo's runner, so
            # `register(..., repo)` would move it here (`--replace` deletes the other repo's
            # registration). It still counts in `live` above — it occupies the box.
            continue
        runner = next(r for r in local if r.name == c["name"])
        if dry_run:
            record["actions"].append(f"would repair {c['name']} ({c['state']})")
            continue
        if c["state"] == "DEAD":
            restart_state, detail = restart_runner(runner)
            record["actions"].append(f"restart {c['name']}: {detail}")
            if restart_state == "unsupported":
                # ⛔ NO PRIMITIVE IS NOT "IT IS OFFLINE". Falling through to `--replace` here
                # re-registers on the strength of an attempt that never ran (macOS before the
                # launchd branch existed), and a re-registration invalidates the credentials of a
                # listener that may be reconnecting. Report the unknown and stop the PASS: no fill
                # decision may be taken from a state this pass could not establish.
                record["unverified"] = True
                record["unverified_cause"] = "no-restart-primitive"
                record["notes"].append(
                    f"{c['name']}: {detail} — stopping this pass (no fill decision on an unknown "
                    "box state; the next pass re-measures from scratch)"
                )
                stop_pass = True
                break
            # ⛔ "we could not look" is NOT "it is still offline". An unreadable pool after a
            # restart proves nothing about the runner, and acting on it re-registers (with
            # --replace) a registration that may be reconnecting and picking up a job — the one
            # destructive action this file promises never to take blindly. Stop the PASS: the box
            # state is unknown from here, so no fill decision may be taken on a stale count.
            # ⛔ MEASURE IT, DO NOT ASSUME IT — whichever way the restart returned. `failed` is
            # any non-zero rc that is neither 127 nor a recognised sudo refusal (an AppArmor/
            # SELinux policy denial, a sudo plugin error, a timeout), and it is NOT evidence the
            # runner is still offline: the restart may have taken effect anyway, or the runner may
            # be reconnecting on its own. Folding it to "offline" skipped the pool read entirely
            # while the log below still claimed "still offline after restart" — a measurement this
            # pass never took — and re-registered with `--replace`, invalidating the credentials of
            # a listener that was already back. `wait_online` IS that measurement: one pool read
            # when it is already online, bounded by wait_s (default 60s) when it is not.
            state = wait_online(repo, c["name"], wait_s, poll)
            if state == "online":
                record["actions"].append(f"{c['name']} came back online after restart")
                continue
            if state == "unverified":
                record["unverified"] = True
                record["unverified_cause"] = "pool-read-after-restart"
                record["notes"].append(
                    f"{c['name']}: restart succeeded but the pool could not be READ — refusing to "
                    "re-register on an unread pool, and stopping this pass (no fill decision can "
                    "be taken on an unknown count)"
                )
                stop_pass = True
                break
            record["actions"].append(
                f"{c['name']} still offline after restart — re-registering with --replace"
            )
        try:
            # ⛔ Re-registering KEEPS the labels the runner already carries — EXCEPT a version-pinned
            # hosted label (`VERSION_PINNED_HOSTED_LABEL`; the carve-out is spelled out in
            # `_preserve_labels`). That class is DROPPED, because a job naming `ubuntu-24.04` wants the
            # GitHub-hosted image and not this pool, so keeping the pin is how a label attached once
            # out of band becomes permanent. ⛔ THE COST WHILE THE #6897 MITIGATION IS ENGAGED: the
            # installer now attaches that label (`CI_RUNNER_OS_LABEL=on|auto`, #10275), so a runner
            # re-registered HERE loses a pin it legitimately carried; `--repair-labels` restores it
            # (it keeps `$OS_LABEL`, dropping the rest of the family) and `--status`' `label-coverage` is
            # what makes the gap visible rather than silent.
            # Deriving a whole fresh set is still the
            # failure this preserves against: it drops any custom/tier label (and the fallback
            # label, when it is engaged), so the workflows that target it queue forever — a
            # "repair" that removes capacity. Only a runner absent from the pool (ORPHANED) has no
            # labels to keep, and only that case derives them — and that derivation now carries the
            # OS fact too (`resolve_labels` → `_os_labels`), because it is the SAME erasure one door
            # over: an ORPHANED runner re-registered without `ubuntu-24.04` drops the mitigation
            # silently, with no error and no log line (PR #10275 review, finding 2).
            # ⛔ PER RUNNER, never a value cached from the previous iteration: that
            # leaked one runner's set (including an other-arch marker) onto every later repair in
            # the same pass (round 2, finding 3). Host-derived labels (self-hosted/Linux/X64/ARM64)
            # cannot be set by hand and are filtered out of what is preserved.
            preserved = _preserve_labels(c["labels"], machine) if c["labels"] else ""
            labels = preserved or resolve_labels(pool, machine)
            record["actions"].append(
                register(runner.directory, c["name"], repo, labels, replace=True)
            )
        except (ActionFailed, Unmeasurable) as exc:
            record["problems"].append(f"{c['name']}: {exc}")
            record["actions"].append(f"repair {c['name']} FAILED: {exc}")

    # 6. FILL — one install at a time, at the NEXT FREE SLOT, and measured ---------------------
    # THE PROVISIONER DOES NOT PROMISE "ADD ONE". `install-ci-runner-x64.sh --count N` means
    # "ensure slots 1..N exist", where a slot is `$BASE`, `$BASE-2`, `$BASE-3`, … — REPO-AGNOSTIC.
    # Three consequences, each learned from a real miss:
    #   * the index must be the next FREE SLOT in that numbering, not the install COUNT: this box
    #     carries an ad-hoc `gh-runner-cell4-x64` beside the numbered pair, so a count said 3 while
    #     slots 1-2 were occupied, the call asked for 4, and the installer created 3 AND 4 — two
    #     additions where the ceiling allowed one, i.e. the over-fill this file exists to prevent
    #     (round 5, finding 1). `_next_free_slot` walks the numbering instead;
    #   * what a call ADDED is measured, never assumed, and compared like with like (installs with
    #     installs, listeners with listeners) — mixing populations produced a negative delta whose
    #     note contradicted the action line above it (round 4, finding 1);
    #   * the loop is bounded twice: by `target` iterations and by LISTENER progress. Installs can
    #     appear while no listener stays up (a rejected registration, a unit that exits), and
    #     measuring only the install count made that state loop forever — hundreds of installer
    #     calls in one pass (round 5, finding 2).
    def _found() -> list[LocalRunner]:
        return discover(root)

    def _live() -> int:
        """How many LISTENERS are alive on this box — measured, and never narrowed to `discover()`.

        ⛔ The same population as the `running` operand above, on purpose: `ceiling` is a BOX
        total, so the fill decision must compare like with like. Deriving it from `discover()`
        silently dropped a listener whose install directory does not match `gh-runner*` — the
        box then looked under-filled and was filled past its ceiling (round 9, finding 4).
        """
        return len(_listener_dirs())

    # ⛔ RE-MEASURE AFTER THE REPAIR LOOP, from the PROCESS LIST — never from `classified` (whose
    # `alive` flags predate the repairs) and never by assuming a repair started a listener. Both
    # mistakes were live: the stale recompute reset the count and the fill loop then added one
    # runner too many, and the increment assumed a listener a restart had not actually produced.
    try:
        live = _live()
    except Unmeasurable as exc:
        record["unverified"] = True
        record["unverified_cause"] = "mid-pass-measurement"
        record["notes"].append(
            f"stopped: the box could not be re-measured after the repairs ({exc}) — the actions "
            "above were already performed; nothing further was done"
        )
        stop_pass = True

    for _step in range(max(1, target)):
        if stop_pass or live >= target:
            break
        try:
            before = mem_available_mb(meminfo)
        except Unmeasurable as exc:
            # Already provisioned something this pass: the record must say so, never "nothing changed".
            record["unverified"] = True
            record["unverified_cause"] = "mid-pass-measurement"
            record["notes"].append(
                f"stopped: memory could not be read mid-pass ({exc}) — the actions above were "
                "already performed; nothing further was done"
            )
            break
        if before - MEM_PER_RUNNER_MB < MEM_RESERVE_MB:
            record["notes"].append(
                f"stop: mem_available_mb={before} - per_runner_mb={MEM_PER_RUNNER_MB} "
                f"< reserve_mb={MEM_RESERVE_MB} — one more would starve a shard"
            )
            break
        index = _next_free_slot(root)
        if dry_run:
            # A DRY RUN CANNOT MODEL A CONVERGENCE IT CANNOT MEASURE. The old plan assumed +1 per
            # call and printed N steps ending `converged`, while the real pass on the same box
            # issued ONE call and ended under-target (round 4, finding 4). Say what a plan knows:
            # the call it WOULD make, and that the result is measured by the real pass.
            record["actions"].append(
                f"would ask the provisioner for install slot #{index} (mem_available_mb={before}); "
                "how many listeners that yields is MEASURED by a real pass, not predicted here"
            )
            break
        try:
            note = provision(index)
        except ProvisionerAbsent as exc:
            # ⛔ A MISSING PROVISIONER PATH IS NOT A FAILED PASS. The installer ships in this repo
            # (`tools/install-ci-runner-x64.sh`), so this is about where THIS BOX keeps it: a
            # timer's cwd is not a shell's. `action-failed`/exit 3 made the timer red every
            # interval on exactly the boxes the guard is meant to help. A named refusal records
            # the state, keeps exit 0, and the next pass re-evaluates.
            record["problem_kind"] = "no-provisioner"
            record["notes"].append(
                f"fill-blocked: {exc} — nothing was added. This is a REFUSAL, not a failure: "
                "every measurement and every repair above still ran"
            )
            break
        except ProvisionerRefused as exc:
            record["problem_kind"] = "refusal"
            # ⛔ THE INSTALLER'S `die` EXITS 2 FROM EVERY GUARD, NOT JUST THE SIZING ONE.
            # `tools/install-ci-runner-x64.sh:234` is `die() { say "REFUSED: $1"; exit "${2:-2}"; }`,
            # so the wrong arch, an unreadable fork cap, a FULL DISK, an unreadable core count and
            # an unreadable gate state all arrive here as rc=2 exactly like the cpu/memory arms do.
            # Only `cpu headroom` / `memory headroom` are the two-models disagreement (#9672), and
            # attributing a disk refusal to it would read as "expected until that decision lands"
            # and never be investigated. So the branch is keyed on the CALLEE'S OWN WORDS, which
            # `provision()` captured verbatim in the exception.
            said = str(exc)
            if "cpu headroom" in said or "memory headroom" in said:
                record["notes"].append(
                    f"fill-refused-by-provisioner: slot #{index} — {exc}. ⛔ This box is UNDER "
                    "target and the one sanctioned registration path will not fill it: that guard "
                    "sizes a runner at 2 cores + 2 reserved (so a 4-core box caps at ONE) AND "
                    "2 GB + 4 GB reserve, while this tool's ceiling uses the owner's core table "
                    "and its MEASURED 1.5 GB + 2 GB. The tool's ceiling is therefore not the "
                    "binding predicate, and the two are not averaged away here — which model "
                    "binds is the open decision at #9672. Nothing was added, and this pass does "
                    "NOT report success."
                )
            else:
                record["notes"].append(
                    f"fill-refused-by-provisioner: slot #{index} — {exc}. ⛔ This box is UNDER "
                    "target and the one sanctioned registration path refused BEFORE doing "
                    "anything. The arm it names above is the callee's own reason — architecture, "
                    "forks, disk, or a measurement it could not read — NOT the sizing "
                    "disagreement at #9672, so it must be acted on and not waited out. Nothing "
                    "was added, and this pass does NOT report success."
                )
            break
        except ActionFailed as exc:
            record["problems"].append(str(exc))
            record["actions"].append(f"add runner (slot #{index}) FAILED: {exc}")
            break
        try:
            after = mem_available_mb(meminfo)
            measured = _found()
            live_now = _live()
        except Unmeasurable as exc:
            record["actions"].append(f"{note} (post-step measurement FAILED: {exc})")
            record["unverified"] = True
            record["unverified_cause"] = "mid-pass-measurement"
            record["notes"].append(
                "stopped: the box could not be re-measured after an install — the action above "
                "already happened; nothing further was done"
            )
            break
        previous_live, live = live, live_now
        record["actions"].append(
            f"{note} (mem_available_mb {before} -> {after}; installs {len(measured)}, "
            f"live listeners {live})"
        )
        if live <= previous_live:
            # ⛔ THE ONE `under-target` CAUSE THAT IS A FAILURE, and it is NAMED so the exit code
            # can key on the cause instead of on the verdict (see the EXIT block). Without this
            # the scheduled pass reported exit 0 — "converged" to anything gating on the exit
            # status — on the exact shape this file's own docstring refuses: installed, and inert.
            record["problem_kind"] = "no-listener"
            record["notes"].append(
                f"stop: slot #{index} was installed but no listener came up (live={live}), so "
                "another call would repeat the same result — stopping instead of reporting a "
                "convergence that did not happen"
            )
            break
        if after < MEM_RESERVE_MB:
            record["notes"].append(
                f"stop: mem_available_mb={after} is below reserve_mb={MEM_RESERVE_MB} after this "
                "install — the box is at its memory limit"
            )
            break

    # 7. VERDICT --------------------------------------------------------------------------------
    if record["problems"]:
        record["verdict"] = "action-failed"
    elif record.get("problem_kind") == "no-provisioner":
        # Named apart from the provisioner's own headroom refusal: this one is about WHERE the
        # installer is on this box (it ships in this repo at tools/install-ci-runner-x64.sh), so a
        # reader can tell "the box is full" from "fill cannot run here" without reading the log
        # prose. Exit 0 — the file is missing from this box's candidate paths, not from the repo.
        record["verdict"] = "fill-blocked"
    elif record.get("problem_kind") == "refusal":
        # ⛔ AND THIS ONE IS **NOT** EXIT 0 (see `main`). The callee's guard and this file's
        # ceiling disagree about what the box can hold, and the box stays under target while they
        # do — so a pass that read that as success was a green timer on a permanently-under-filled
        # box. The disagreement itself is a DECISION (which predicate binds, #9672), never an
        # average this file may take on its own.
        record["verdict"] = "fill-refused-by-provisioner"
    elif record.get("unverified"):
        # A measurement could not be taken (after a restart). Reported as unmeasurable rather than
        # "converged so nothing to see", because the box's state is genuinely unknown here.
        record["verdict"] = "unmeasurable"
        # ⛔ NAME THE MEASUREMENT THAT FAILED. One fixed sentence said "the pool could not be read
        # after a restart" for every unverified pass, including the one where nothing was restarted
        # and the MEMORY read failed after an install — a `--json` consumer read the wrong cause
        # (round 6, finding 3). The actions above stay in the record either way.
        if record.get("unverified_cause") == "pool-read-after-restart":
            record["error"] = (
                "the pool could not be read after a restart — the box's runner count is UNKNOWN, "
                "so the pass stopped there. The actions listed above were already performed BEFORE "
                "that point; nothing was added or registered after it."
            )
        elif record.get("unverified_cause") == "no-restart-primitive":
            record["error"] = (
                "a DEAD runner could not be restarted because this host has no service manager "
                "this tool can drive (systemctl or the runner's own svc.sh) — the box's state is "
                "UNKNOWN, so the pass stopped there and NOTHING was re-registered. The actions "
                "listed above were already performed BEFORE that point."
            )
        else:
            record["error"] = (
                "a measurement could not be taken mid-pass (the box could not be re-measured after "
                "an install) — the pass stopped there. The actions listed above were already "
                "performed BEFORE that point; nothing further was done."
            )
    elif live > ceil:
        record["verdict"] = "over-ceiling"
        record["notes"].append(
            f"over-ceiling: {live} live against a measured ceiling of {ceil} — {reading}; nothing "
            "removed (a runner that may be serving is never removed)"
        )
    elif live >= target and record["actions"]:
        record["verdict"] = "converged"
    elif live >= target:
        record["verdict"] = "at-ceiling"
        record["notes"].append(f"at-ceiling: {live}/{ceil} — {reading}")
    elif live < target:
        # The loop ended under target (a provisioner that added nothing, or an action failure that
        # was already recorded). NEVER `converged`: that word is a claim the box reached its
        # ceiling, and this is the state where it did not (round 3, finding 1's under-fill half).
        record["verdict"] = "under-target"
        record["notes"].append(
            f"under-target: {live} live against a target of {target} — {reading}"
        )
    else:
        record["verdict"] = "converged" if record["actions"] else "at-ceiling"
    record["live"] = live
    return record


def _preserve_labels(current: list[str], machine: str) -> str:
    """The labels a RE-registered runner keeps: its custom labels, plus THIS box's arch marker.

    `self-hosted`, the OS token and the arch token are derived by GitHub from the host at
    registration and cannot be set by hand, so they are dropped from the `--labels` argv; every
    other label (a custom tier, a pool tag, `ubuntu-latest` while the fallback is engaged) is
    PRESERVED, because a workflow targeting it would otherwise queue forever.

    ⛔ A VERSION-PINNED HOSTED LABEL (`ubuntu-24.04`-shaped, INCLUDING GitHub's arm64 spelling
    `ubuntu-24.04-arm` — see `VERSION_PINNED_HOSTED_LABEL`) is the ONE exception, and it is
    dropped rather than preserved. The DEFAULT is that the pool claims `ubuntu-latest` and never
    an `ubuntu-<version>`, but `docs/ops/ci-runner-x64.md:26` records the 2026-10-29 #6897
    mitigation: the installer DOES attach `ubuntu-24.04` (`CI_RUNNER_OS_LABEL=on|auto`, #10275), so
    a pin here is legitimate operator state and this drop is a NAMED residual, not the invariant.
    It stays because a pin this path made PERMANENT outlives the mitigation and silently un-pins
    every observer job pinned to it — the 2026-10-24 drift that put `finding-provenance` on
    `pi-runner-vm-*` with `ubuntu-24.04` (`tests/test_runner_tool_contract.py:5-12`) — and NOT
    because a pinned job can get the hosted image: in a hosted outage it cannot, which is exactly
    what the mitigation answers. `ubuntu-latest` itself is NOT dropped: it is the fallback label.

    ⛔ The arch marker is RE-DERIVED from this box, never preserved: a mis-registered runner can
    carry the OTHER arch's marker, and passing it straight back lets an x86 box register with
    `self-hosted-arm64` — a false PASS (an arm64 job built on x86), not a queue delay.
    ⛔ AND THE MARKER TEST IS CASE-INSENSITIVE, DELIBERATELY — `x.lower()`, unlike the host-derived
    compare below. The two classes are NOT the same: the host names are vendor-fixed spellings, but
    `self-hosted-<arch>` is a CUSTOM label whose spelling is whatever created it, and docs.github.com
    "apply labels" says "Labels are case-insensitive" — so `Self-Hosted-Arm64` IS the arm64 marker
    and still answers a lower-case `runs-on:`. An exact-case test here lets a mixed-case stamp
    survive re-registration while the docstring above promises it is re-derived.

    ⛔ THE HOST-DERIVED MATCH IS EXACT-CASE, like the repair's `is_host_label`
    (`tools/install-ci-runner-x64.sh`) — `ONE invariant, two writers` is only true if both compare
    the same way. GitHub returns `Linux`/`X64`/`macOS`/`Windows`/`self-hosted`, so a LOWER-CASED
    compare adds nothing to the class and silently drops an operator's own `linux` or `x64` label on
    re-registration — while `--repair-labels` keeps it. Filtering a label out of this argv IS a
    removal: a label one writer keeps and the other deletes is the divergence this function's
    shared-constant comment forbids.

    ⛔ THAT DROP STANDS WHILE THE #6897 MITIGATION IS ENGAGED, and the residual is NAMED rather than
    hidden: the installer can attach `ubuntu-24.04` (`CI_RUNNER_OS_LABEL=on|auto`, #10275), so a
    runner RE-registered by this path loses a pin it legitimately carried. Nothing here re-attaches
    it — a deliberate `--repair-labels` pass does (where the running resolution CLAIMS `$OS_LABEL`
    — `on`, or `auto` on a matching host — it KEEPS that one label and
    DROPS the rest of the family with a `NOT KEPT:` line, `tools/install-ci-runner-x64.sh`) — and
    `--status`'
    `label-coverage` exit 5 is what makes that gap visible instead of silent.
    """
    host_derived = {"self-hosted", "Linux", "macOS", "Windows", "X64", "ARM64", "ARM"}
    arch = ARCH_LABEL.get(machine.lower())
    keep = [
        x
        for x in current
        if x and x not in host_derived and not x.lower().startswith("self-hosted-")
        and not VERSION_PINNED_HOSTED_LABEL.fullmatch(x)
    ]
    if not arch:
        return ""  # unknown arch: the caller falls back to resolve_labels, which REFUSES
    return ",".join([arch, *keep])


_WORKER_CACHE: dict[str, bool] = {}


def _worker_alive(runner: LocalRunner) -> bool:
    """A live `Runner.Worker` under this install ⇒ a job is being served (belt and braces).

    The AUTHORITATIVE busy flag is the pool's own `busy`; this exists because `busy` is a
    broker-side view, and a listener that lost its broker connection can be executing a job
    while the API says idle — and for a runner the pool does not list at all, this is the ONLY
    signal that it is working. Restarting or re-registering that one would kill a real job.
    """
    seam = os.environ.get("RUNNER_AUTOSCALE_BUSY_DIRS")
    if seam:
        try:
            busy = {
                ln.strip()
                for ln in Path(seam).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            }
        except OSError as exc:
            raise Unmeasurable(f"busy-dirs seam unreadable ({seam}: {exc})") from exc
        return str(runner.directory) in busy
    key = str(runner.directory)
    if key not in _WORKER_CACHE:
        try:
            proc = subprocess.run(
                [os.environ.get("RUNNER_AUTOSCALE_PS") or "ps", "-eo", "args"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # Same fail-closed rule as `_listener_dirs`: swallowing this to `False` silently
            # deletes the one signal that protects a runner the POOL does not list, so an install
            # executing a job could be `--replace`d on the strength of a broken `ps`.
            raise Unmeasurable(f"process list unreadable ({exc})") from exc
        if proc.returncode != 0:
            raise Unmeasurable(f"process list unreadable (ps exited {proc.returncode})")
        _WORKER_CACHE[key] = any(
            "Runner.Worker" in line and f"{runner.directory}/bin" in line
            for line in proc.stdout.splitlines()
        )
    return _WORKER_CACHE[key]


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def _log(record: dict) -> None:
    state_dir = Path(
        os.environ.get("RUNNER_AUTOSCALE_STATE_DIR") or (Path.home() / ".pi" / "agent" / "state")
    )
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        with (state_dir / "runner-autoscale.log").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "at": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        **record,
                    }
                )
                + "\n"
            )
    except OSError:
        pass  # a log that cannot be written must not fail a convergence pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True, description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=os.environ.get("RUNNER_AUTOSCALE_REPO") or DEFAULT_REPO)
    ap.add_argument("--root", default=os.environ.get("RUNNER_AUTOSCALE_RUNNER_ROOT") or "/opt")
    ap.add_argument(
        "--want",
        type=int,
        default=None,
        metavar="N",
        help="runner count to ensure; REFUSED above the measured ceiling",
    )
    ap.add_argument("--status", action="store_true", help="measure + classify; change nothing")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    ap.add_argument("--json", action="store_true", help="one machine-readable record")
    opts = ap.parse_args(argv)

    if opts.want is not None and opts.want < 0:
        print("usage: --want must be >= 0", file=sys.stderr)
        return 1

    try:
        record = converge(
            opts.repo, root=opts.root, want=opts.want, dry_run=opts.status or opts.dry_run
        )
    except Unmeasurable as exc:
        payload = {"repo": opts.repo, "verdict": "unmeasurable", "error": str(exc)}
        print(
            json.dumps(payload) if opts.json else f"UNMEASURABLE (nothing changed): {exc}",
            file=sys.stderr,
        )
        _log(payload)
        return 2

    _log(record)
    if opts.json:
        print(json.dumps(record))
    else:
        print(f"repo={record['repo']} cell={os.uname().nodename} {record['reading']}")
        for c in record.get("runners", []):
            print(f"  {c['state']:<9} {c['name']} dir={c['dir']}")
        for a in record["actions"]:
            print(f"  - {a}")
        for n in record.get("notes", []):
            print(f"  · {n}")
        if record.get("refusal"):
            print(record["refusal"], file=sys.stderr)
        print(f"verdict={record['verdict']}")
    if record["verdict"] == "refused":
        return 1
    if record["verdict"] == "action-failed":
        return 3
    if record["verdict"] == "unmeasurable":
        return 2
    if record["verdict"] == "under-target" and record.get("problem_kind") == "no-listener":
        return 4
    # ⛔ EXIT 5: the installer's OWN guard refused a count this tool computed, so the box is under
    # target and the one sanctioned registration path will not fill it. Exit 0 here (the pre-#9685
    # contract) made the scheduled pass report a permanently-under-filled box as GREEN — the
    # false-PASS class this file exists to prevent. It is keyed on the CAUSE (`problem_kind`), not
    # on `under-target`, so a `--status` plan and a stop at the memory reserve keep exit 0: those
    # are answers, and an install-time probe on such a box must stay PASS.
    if record["verdict"] == "fill-refused-by-provisioner":
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
