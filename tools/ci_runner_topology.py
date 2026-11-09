#!/usr/bin/env python3
"""ci_runner_topology.py — the runner-label topology, and the invariant it must hold (#10912).

WHY THIS EXISTS (MEASURED, not inferred)
---------------------------------------
`unblock-gate` promoted 2 of 7 starved PRs and named the residual: **the
`ubuntu-latest` label topology**. Reproduced 2026-11-07T16:22-16:30Z against
`repos/daniel-ospina/tortoise/actions/runners` (21 runners):

    label set                                                     runners   state
    Linux, X64, self-hosted, self-hosted-x64                        16       15 idle, 1 offline
    Linux, ARM64, self-hosted, self-hosted-arm64, ubuntu-latest       3       all BUSY
    Linux, ARM64, self-hosted, self-hosted-arm64                      2       idle

Demand, sampled from the oldest 20 of 79 queued runs (`actions/runs/<id>/jobs`,
one API read per run, `labels` field): **43 of 47 queued jobs request
`ubuntu-latest`**; the other 4 want `ubuntu-24.04` (hosted-only by design).
`license-surface` (workflow `CI`) had been `queued` on `ubuntu-latest` since
2026-11-05T16:29Z — 2 days, 1 hour — at measurement.

So the issue's join still holds EXACTLY: demand and supply are disjoint sets.
Fifteen online x64 runners capable of serving `ubuntu-latest` work sit idle
advertising a label (`self-hosted-x64`) that zero queued jobs request, while the
only three runners that carry `ubuntu-latest` are saturated.

WHY IT IS A RESIDUE, NOT A DESIGN
---------------------------------
`agent-infra/scripts/ci_runner_gate.sh` owns this label: it attaches
`FALLBACK_LABEL` to the self-hosted pool while GitHub-hosted capacity is
insufficient, and strips it otherwise. That sentence is a statement about which
arm the gate has run — NOT a claim that GitHub-hosted is unhealthy; what was
measured is that not enough x64-labelled capacity was ALLOCATED, and nothing
here measures hosted's health. Two measured facts make the state this lane found
residue rather than intent:

  * the gate's scheduled pass last ran 2026-11-01T16:42Z — its log's last line
    still reports `runners=12` — and its state file still reads `mode=github`
    ("strip the label");
  * its own header names this exact failure, in the paragraph that introduces
    `FALLBACK_ARM_MAX`: "a capped disarm would strand `ubuntu-latest` on the
    runners outside the arm".

⛔ EVERY CITATION TO `ci_runner_gate.sh` IN THIS FILE IS BY SYMBOL, NEVER BY LINE
NUMBER. A line number reads as authority while being the one thing in the
sentence that silently rots: a reviewer found every `:NNN` cite here drifted by
tens-to-hundreds of lines against the current gate, and it also makes the claim
un-refutable — the reader cannot tell "the cite moved" from "the claim was
never true". `tests/test_ci_runner_topology.py` pins each cited symbol's
existence, so a rename reds a test instead of quietly making this prose wrong.

A LIVE `ci_runner_gate.sh --status` read at 2026-11-07T17:09Z returns
`mode=local ... runners=21 online_idle=4 online_busy=16 offline=1`, so the
recorded STATE is stale rather than the CAPABILITY — which is exactly why this
tool decides from the measured pool and never from the log.

⛔ THE x64-ONLY DESIRED TOPOLOGY IS NOT A REVERSAL OF A RECORDED DECISION. A
reviewer read the `arm_ids` comment "⛔ ARCH IS DELIBERATELY NOT A FILTER HERE"
as forbidding the ARM64 strip; the gate's own write path falsifies that.
`ci_runner_gate.sh::labels_for` derives each runner's body from ITS OWN host and
omits `ubuntu-latest` whenever the pass is `off`, for EVERY managed runner —
arch never enters the disarm — and its mode-off branch says it directly: "in
mode=github the pool is stripped by design" (the gate's header words the same
thing "STRIPPED ON PURPOSE"). That `arm_ids` comment bounds the ENGAGE pass's cap
(`arm_ids`, which runners a fallback arm may touch), not whether an ARM64 runner
may carry the label at all. `docs/ops/ci-runner-x64.md` records the scope the #2327 decision left open:
"What changes is only *which architecture* may serve the fallback — which the
decision never specified".

THE INVARIANT (what this tool asserts, and can restore)
------------------------------------------------------
`ubuntu-latest` is a GitHub-HOSTED image name that promises an x86_64 image.
Attaching it to a runner is a scheduling decision; it is NOT an image
(`.github/scripts/runner-tools.sh`, #9018). Two failure modes follow, and this
tool names each separately because they have different remedies:

  FALLBACK_ON_NON_X64   A non-x64 runner carries the fallback label. This is the
                        already-filed #6897 residual: head `5a2ae6aa1`'s
                        `test (i)` CANARY was taken by `pi-runner-vm-5`
                        (ARM64) and killed at 32m12s, and `test-slow` was
                        pinned off `ubuntu-latest` for the same reason
                        (`tests/test_ci_selection.py
                        ::test_slow_legs_are_pinned_off_the_ambiguous_runner_label`).
                        REMEDY: attach it to the x64 pool. Removing it from the
                        non-x64 carrier is `apply --strip-non-x64`, which is
                        owner-gated (#6897/#9878) and therefore never a default.

  NO_X64_FALLBACK       The fallback is engaged (some runner carries it) and NO
                        x64 runner does, so every `ubuntu-latest` job that does
                        not reach GitHub-hosted falls to a non-x64 runner or
                        queues. REMEDY: attach it to the x64 pool.

  UNENGAGED_WITH_STARVATION
                        No ONLINE runner can serve the fallback — either nothing carries it, or
                        what carries it is offline — while `ubuntu-latest`
                        demand is queued past the floor: the pool is idle by
                        design and the queue is absorbing. This is a STATE, not
                        a violation — recorded, exit 0, because "hosted is
                        healthy, the pool is idle" is a legitimate topology and
                        this tool must not red on it. Only the two violations
                        above red.

The DESIRED topology this tool produces (`--engage`):
`ubuntu-latest` ATTACHED to the x64 pool, so queueing `ubuntu-latest` work has a
runner to reach.

⛔ THE ENGAGING ARM IS CAPPED, BECAUSE THE GATE THAT OWNS THE LABEL CAPS ITS OWN.
`agent-infra/scripts/ci_runner_gate.sh` bounds an engaging pass at
`FALLBACK_ARM_MAX` (default 3, `arm_ids` takes `sort -n | awk 'NR<=n'` over the managed ids):
OWNER RULING 2026-11-02, daniel-ospina/tortoise#9878 — "what lands on the pool is bounded by
FALLBACK_ARM_MAX, so queue length alone no longer decides how much contention a fallback
creates". This tool READ that cap and did not apply it, so `apply` armed all 16 x64 runners —
13 more than the containment the ruling records permits an engaging arm to touch (review
finding, round 3). It now reads the SAME variable with the SAME default and ARMS AT MOST that
many, ids ascending in the gate's own order. A carrier already beyond the cap is REPORTED as
`over_cap` and left alone: removing the label is the destructive direction, which `--disengage`
owns. Widening the arm is a recorded-decision change — the #10912 reopen, never a lane edit. The x64-ONLY form — the label stripped from every runner that is
not x64 — is `--engage --strip-non-x64`, and it is NOT the default: removing the
label is the step `#6897/#9878` own (`tools/ci_runner_labels.py` R2), and this
PR's own body records it as not adoptable without a reopen. Under a bare `apply`
a non-x64 carrier is left byte-identical and REPORTED as `OWNER-GATED`.

⛔ WHAT THIS TOOL DOES NOT DO. It does not touch `runs-on:` in any workflow (the
repo-side half of this class is already landed: `test-slow`/`workflow-lint` are
pinned to a version-pinned hosted label). It does not disable, skip or weaken a
required check. And it does not claim GitHub-hosted capacity: engaging the
fallback ADDS the x64 pool to the set of runners that can serve `ubuntu-latest`
work; it does not remove hosted, so no leg loses a runner it had.

USAGE
    uv run python tools/ci_runner_topology.py census [--json]
    uv run python tools/ci_runner_topology.py check            # exit 1 on a violation
    uv run python tools/ci_runner_topology.py apply --dry-run  # what would change
    uv run python tools/ci_runner_topology.py apply            # ENGAGE (default): the desired topology
    uv run python tools/ci_runner_topology.py apply --strip-non-x64  # + the owner-gated x64-only strip
    uv run python tools/ci_runner_topology.py apply --disengage # strip the fallback everywhere
    uv run python tools/ci_runner_topology.py check --queued-floor-seconds 1800

EXIT CODES (a script reads the code, not the prose — `0`/`1` alone encoded "the write failed"
and "the write landed, an owner decision remains" as the same byte):
    0  clean
    1  the write did not take, the read-back failed, a capacity gate fired, or a violation this
       tool should have closed survived the reconcile
    2  usage refusal (a fixture write without `--dry-run`, a disarmed engaging cap)
    3  every planned write landed AND the pool was read back, but an OWNER-GATED residual remains
       (#6897/#9878) — re-run with `--strip-non-x64`, or record the reopen on #10912

`apply` ENGAGES by default. The strip direction is `--disengage`, stated
explicitly, because a verb whose DEFAULT silently performs the destructive
direction is how a pool ends up with no runner answering a label every queued
job asks for. The x64-ONLY half (`--engage --strip-non-x64`) removes the label
from non-x64 carriers and is opt-in, because that removal is the step
`#6897/#9878` reserve; without it an ARM64 carrier is reported as `OWNER-GATED`
and left byte-identical. `apply --from-fixture` is refuse-without-`--dry-run`: a fixture
carries runner ids from another census (the measured one carries this repo's real
ids), so writing from it relabels runners nobody asked it to touch.

`apply` needs `administration: write` on the token — `GITHUB_TOKEN` inside
Actions does NOT have it, so the reconcile is operator/hub-run (the same token
class `scripts/ci_runner_gate.sh` uses).

⛔ THE TRIPWIRE IS NOT SCHEDULED YET, AND THIS FILE DOES NOT PRETEND IT IS. A scheduled
workflow was written for this tool and dropped on review for two measured reasons:
`GITHUB_TOKEN` cannot grant `administration: read`, so listing runners from Actions 403s and
every scheduled run would have reported a VIOLATION (a false alarm that teaches people to
ignore the tripwire); and `tests/test_runner_tool_contract.py` requires a pool-eligible `gh`
job to declare the #9018 tool contract. Today `check` is a verb a human or a hub lane runs;
scheduling it is follow-up work on #10912.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — an environment
# refusal must never wear a VERDICT code (#11342). The real hazard here is
# `datetime.UTC` (3.11+), which this file reads AT CALL TIME (`_dt.UTC` inside
# `fetch_queue_probe` and `_age_seconds`); without this guard an old interpreter
# reaches a call and dies with an unattributable AttributeError instead of naming
# the floor. 4 is free: `EXIT_OK` / `EXIT_FAILED` / `EXIT_USAGE` /
# `EXIT_OWNER_GATED` below are the verdicts a wrapper branches on, and a refusal
# that exited 1 would read to a caller as "the write failed".
#
# ⛔ `--help` IS PART OF THE NAMED INVOCATION ON PURPOSE. `verb` is a REQUIRED
# positional (`census`/`check`/`apply`), so the bare path exits 2 with
# "the following arguments are required: verb" — measured, and it is argparse's
# own usage-error status, not this tool running. A refusal that names a command
# which itself dies is the SECOND unattributed error this guard exists to
# prevent. The census's `_expected_invocation` returns the bare path and its
# assertion is a SUBSTRING test, so `--help` satisfies it while being the one
# form that always resolves and needs no token. Do not trim it back.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/ci_runner_topology.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_runner_topology.py --help`",
        file=sys.stderr,
    )
    raise SystemExit(4)

import argparse
import datetime
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

#: The label `agent-infra/scripts/ci_runner_gate.sh` attaches to the self-hosted
#: pool as a GitHub-hosted fallback (its `FALLBACK_LABEL` declaration). It promises an
#: x86_64 hosted image, which is why a non-x64 runner carrying it is a violation
#: and not merely an oddity.
FALLBACK_LABEL = "ubuntu-latest"

#: The architecture labels GitHub stamps at registration. These are the HOST,
#: not the pool marker (`self-hosted-x64` / `self-hosted-arm64`) — the marker is
#: what a workflow asks for, the host label is what the machine IS.
HOST_LABEL_X64 = "X64"
HOST_LABEL_ARM64 = "ARM64"

#: Pool markers, restated so `apply` can rebuild a runner's label set without
#: deleting a label a human added deliberately (anything else is preserved).
POOL_LABEL_X64 = "self-hosted-x64"
POOL_LABEL_ARM64 = "self-hosted-arm64"

#: BOTH markers: a runner's marker must match the host it is stamped on, so the marker the host
#: does NOT own is dropped before the host's is added — `ci_runner_gate.sh::labels_for` drops both
#: (`"$POOL_LABEL_ARM64"|"$POOL_LABEL_X64") ;;`) and re-derives one from the host, which is why a
#: gate pass and an `apply` pass must produce the same set for the same box.
POOL_MARKERS = frozenset({POOL_LABEL_X64, POOL_LABEL_ARM64})

#: The API's own `type` value for a label GitHub stamps at registration (and which a
#: write may therefore not include). The read-only/custom split is taken from THIS,
#: never inferred from a name list.
LABEL_TYPE_READ_ONLY = "read-only"

#: The platform label of the pool this tool governs. A runner on another platform is not a
#: member of the Linux self-hosted pool the fallback label belongs to.
PLATFORM_LINUX = "Linux"

#: The FALLBACK for a payload that carries no `type` at all (a fixture, a hand-trimmed
#: read). A name list is a per-platform guess — it names labels a runner may not carry
#: (`Windows` on Linux) and misses custom read-only ones — so it is consulted only when
#: the payload itself cannot answer.
GITHUB_OWNED = frozenset({"self-hosted", "Linux", "macOS", "Windows", "X64", "ARM64", "ARM"})

VIOLATION_FALLBACK_ON_NON_X64 = "FALLBACK_ON_NON_X64"
VIOLATION_NO_X64_FALLBACK = "NO_X64_FALLBACK"

#: "I could not look" must never be the last word (the doctrine `ci-queue-health.yml`
#: states): an empty enumeration is a READ FAILURE, not an absence of violations.
VIOLATION_UNMEASURED_EMPTY_POOL = "UNMEASURED_EMPTY_POOL"

#: `apply --engage` promises CAPACITY. A reconcile that leaves no ONLINE x64 runner serving the
#: label has delivered nothing, however clean every other reading is — the same false PASS the
#: default-direction fix was supposed to close, one layer down (an empty or fully-offline x64 arm).
VIOLATION_ENGAGE_NO_X64_SERVER = "ENGAGE_PRODUCED_NO_X64_SERVER"

#: The engaging-arm cap the OWNING gate applies, read from the SAME variable and with the SAME
#: default (`agent-infra/scripts/ci_runner_gate.sh`, the `FALLBACK_ARM_MAX` declaration —
#: `FALLBACK_ARM_MAX="${CI_GATE_FALLBACK_ARM_MAX:-3}"`; OWNER RULING 2026-11-02,
#: daniel-ospina/tortoise#9878). One value, two readers: if this tool
#: kept its own number the two would arm different amounts of the same pool and neither would be
#: wrong by its own reading.
ARM_CAP_ENV = "CI_GATE_FALLBACK_ARM_MAX"
ARM_CAP_DEFAULT = 3

#: A cap that cannot be read is a REFUSAL, not an unbounded arm. `ci_runner_gate.sh` refuses the
#: pass on a non-numeric value (`case "$FALLBACK_ARM_MAX" in ''|*[!0-9]*) return 1`), and the
#: failure mode of guessing here is silent: arming the whole pool under a typo.
VIOLATION_ARM_CAP_UNREADABLE = "ARM_CAP_UNREADABLE"

#: `FALLBACK_ARM_MAX=0` is not "arm nothing quietly" — `ci_runner_gate.sh`'s `FALLBACK_ARM_MAX`
#: block reads it as the
#: fallback being DISARMED, where "the pool is released and the engaging pass REFUSES, rather than
#: reporting a successful engage carrying nothing". A verb named `apply` that printed
#: "0 changes — already at the desired topology" under it would be that false PASS.
ENGAGE_REFUSED_CAP_ZERO = "ENGAGE_REFUSED_CAP_ZERO"

#: EXIT CODES, because a script reads the code and not the prose. `0` and `1` alone encode "the
#: write failed" and "the write landed but an OWNER DECISION remains" as the same byte, so a
#: wrapper cannot tell a broken reconcile from one that did exactly what this tool is allowed to
#: do (review round 3, P2: "on this pool the default verb can never exit 0 — the verdict is right,
#: the encoding is lossy").
EXIT_OK = 0
#: A write did not take, the read-back failed, a capacity gate fired, or a violation this tool
#: SHOULD have closed survived the reconcile.
EXIT_FAILED = 1
#: Reserved: argument/usage refusal (`--from-fixture` without `--dry-run`, a disarmed cap).
EXIT_USAGE = 2
#: Every planned write landed AND the pool was read back, but a residual this tool MUST NOT heal
#: remains (it is owner-gated: `#6897/#9878`). Not a failure — re-run with `--strip-non-x64`.
EXIT_OWNER_GATED = 3
#: The interpreter refused before doing any work (#5128): this file needs >= 3.12. Deliberately
#: distinct from every verdict above, so a wrapper branching on the code cannot read an environment
#: refusal as "the write failed" / "a violation survived" / "an owner decision remains" (#11342).
EXIT_INTERPRETER = 4

#: "read the cap from the environment" — distinct from an explicit `None` (an ILLEGAL cap). Using
#: `None` as the default would make "no cap configured" and "bad cap configured" one value, and the
#: second is a refusal.
_ARM_CAP_FROM_ENV = object()

#: A runner carrying the OTHER architecture's pool marker. `runs-on: [self-hosted, self-hosted-x64]`
#: is an AND, so an ARM64 box carrying `self-hosted-x64` is selected for x64 work — the #6897
#: mis-serve, reached through the marker instead of through `ubuntu-latest`. It is also a FALSE
#: PASS: `violations()` read only `ubuntu-latest`, so this pool reported `verdict: OK`.
VIOLATION_FOREIGN_POOL_MARKER = "FOREIGN_POOL_MARKER"


@dataclass(frozen=True)
class Runner:
    """One registered self-hosted runner, as the API returns it."""

    id: int
    name: str
    status: str
    busy: bool
    labels: frozenset[str]
    read_only: frozenset[str] = frozenset()

    @property
    def custom_labels(self) -> frozenset[str]:
        """The labels a write may carry: everything GitHub does not stamp at registration."""
        return self.labels - self.read_only

    @property
    def host(self) -> str | None:
        """The machine's architecture label, or None when it carries neither."""
        if HOST_LABEL_ARM64 in self.labels:
            return HOST_LABEL_ARM64
        if HOST_LABEL_X64 in self.labels:
            return HOST_LABEL_X64
        return None

    @property
    def serves_fallback(self) -> bool:
        return FALLBACK_LABEL in self.labels

    @property
    def online(self) -> bool:
        return self.status == "online"


@dataclass
class Violation:
    kind: str
    runner: str
    why: str

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "runner": self.runner, "why": self.why}


@dataclass
class Plan:
    """The label sets `apply` would write, keyed by runner name."""

    changes: dict[str, tuple[int, list[str], list[str]]] = field(default_factory=dict)
    unclassified: dict[str, str] = field(default_factory=dict)
    #: A defect this tool MUST NOT heal by default (owner-gated), keyed by runner name.
    owner_gated: dict[str, str] = field(default_factory=dict)
    #: Runners a VIOLATION's stated remedy cannot reach, keyed by runner name -> why: another
    #: platform, no pool marker, or no derivable arch. `apply` skips these by design, so a reader
    #: of `check` must not be told to run a command this tool will refuse.
    out_of_scope: dict[str, str] = field(default_factory=dict)
    #: Runners the engaging-arm cap keeps this run from arming (or keeps carrying the label from a
    #: previous, wider arm), keyed by runner name -> why. Reported, never written.
    over_cap: dict[str, str] = field(default_factory=dict)
    #: The cap this plan was bounded by; None means the configured value was not a legal cap.
    arm_cap: int | None = None
    #: The runners this plan ARMS (the first `arm_cap` candidates, ids ascending). Empty for a
    #: disarm, and empty when the cap is unreadable — nothing was armed then.
    armed: list[str] = field(default_factory=list)

    def add(self, runner: Runner, desired: Sequence[str]) -> None:
        # Both sides are the CUSTOM set, because that is the only set a write can change.
        # Comparing the FULL set instead turns a read-only label (`Linux`) into a diff no
        # write can ever close — reported as a change forever, on every run.
        before = sorted(runner.custom_labels)
        after = sorted(set(desired))
        if before != after:
            self.changes[runner.name] = (runner.id, before, after)

    def __bool__(self) -> bool:
        return bool(self.changes)


def parse_runner(payload: Mapping[str, Any]) -> Runner:
    """Build a Runner from one element of `.runners` (or a test fixture).

    The read-only/custom split comes from the API's own `label["type"]`. `GITHUB_OWNED` is
    only the fallback for a payload that carries no `type`, so a hand-written fixture still
    parses — but a real read never has to guess.
    """
    labels: set[str] = set()
    read_only: set[str] = set()
    for label in payload.get("labels", []):
        name = str(label["name"])
        labels.add(name)
        kind = label.get("type")
        if kind == LABEL_TYPE_READ_ONLY or (kind is None and name in GITHUB_OWNED):
            read_only.add(name)
    return Runner(
        id=int(payload["id"]),
        name=str(payload["name"]),
        status=str(payload.get("status", "unknown")),
        busy=bool(payload.get("busy", False)),
        labels=frozenset(labels),
        read_only=frozenset(read_only),
    )


def parse_runners(payload: Mapping[str, Any]) -> list[Runner]:
    return [parse_runner(row) for row in payload.get("runners", [])]


def census(runners: Sequence[Runner]) -> dict[str, Any]:
    """Counts by host and by fallback service. Pure; no I/O; no judgement."""
    by_host: dict[str, dict[str, int]] = {}
    for runner in runners:
        bucket = by_host.setdefault(runner.host or "UNKNOWN", {"total": 0, "idle": 0, "serving_fallback": 0, "serving_offline": 0})
        bucket["total"] += 1
        # ONLINE and not busy. An offline runner is registered capacity that cannot
        # serve anything; counting it as `idle` contradicts `x64_idle` in the same
        # output (measured: the two lines printed 16 and 15 for one pool).
        if runner.online and not runner.busy:
            bucket["idle"] += 1
        if runner.serves_fallback:
            bucket["serving_fallback"] += 1
            # A carrier that is OFFLINE serves nothing. Kept as its OWN key so the raw census
            # stays raw (the starvation note reads it deliberately: "ARMED on N runner(s) but not
            # one ONLINE can serve it") while a render can print what a human is actually asking
            # for — "how much capacity have I got?".
            if not runner.online:
                bucket["serving_offline"] += 1
    return {
        "runners": len(runners),
        "online": sum(1 for r in runners if r.online),
        "by_host": by_host,
        "fallback_label": FALLBACK_LABEL,
        "serving_fallback": sorted(r.name for r in runners if r.serves_fallback),
        #: ONLINE carriers only: the capacity reading, beside the raw census above.
        "serving_fallback_online": sorted(
            r.name for r in runners if r.serves_fallback and r.online
        ),
        # ONLINE and idle: an offline runner is registered capacity that cannot serve anything,
        # and counting it is how a census reports capacity the queue cannot reach.
        "x64_idle": sorted(
            r.name for r in runners if r.host == HOST_LABEL_X64 and r.online and not r.busy
        ),
    }


def violations(runners: Sequence[Runner]) -> list[Violation]:
    """The two defects. Each is named with the remedy, never merged with the other.

    Ordering is deterministic (by runner name) so a CI log diff is readable.
    """
    found: list[Violation] = []

    # 1. The #6897 residual: a runner that cannot serve an x86_64 UBUNTU image carrying that
    # image's label. TWO ways to be unable — a host that is not x64, and a host on a platform
    # that is not Linux. Reading only the arch let a Windows/X64 box carrying `ubuntu-latest`
    # pass as fine: the same mis-serve, invisible to the reading.
    for runner in sorted(runners, key=lambda r: r.name):
        if not runner.serves_fallback:
            continue
        wrong_arch = runner.host != HOST_LABEL_X64
        wrong_platform = PLATFORM_LINUX not in runner.labels
        if not (wrong_arch or wrong_platform):
            continue
        because = (
            f"its host label is {runner.host or 'neither X64 nor ARM64'}"
            if wrong_arch
            else "its platform is not Linux"
        )
        # ⛔ A REMEDY THIS TOOL CANNOT PERFORM IS NOT A REMEDY. `apply --strip-non-x64` skips a
        # runner `unmanaged_reason` excludes (another platform, no pool marker, no derivable arch),
        # so a bare `strip it` promised a diff no run of this tool could ever close — the reader
        # concludes the tool is broken instead of out of scope (review round 3, P2).
        gate = unmanaged_reason(runner)
        remedy = (
            f"REMEDY: strip {FALLBACK_LABEL!r} from this runner."
            if gate is None
            else (
                f"REMEDY: strip {FALLBACK_LABEL!r} from this runner MANUALLY — it {gate}, so "
                f"this tool will not relabel it and `--strip-non-x64` skips it."
            )
        )
        found.append(
            Violation(
                kind=VIOLATION_FALLBACK_ON_NON_X64,
                runner=runner.name,
                why=(
                    f"carries {FALLBACK_LABEL!r} but {because}; an x64 Ubuntu-expected job "
                    f"routed here is silently mis-served (#6897). "
                    f"{remedy}"
                ),
            )
        )

    # 1b. The same mis-serve through the POOL MARKER. `runs-on: [self-hosted, self-hosted-x64]` is
    # an AND, so a box carrying a marker its host does not own is selected for work it cannot run —
    # and this reading was missing entirely, so such a pool reported `verdict: OK`.
    # ⛔ THE SKIP IS FOR DERIVING THE MARKER, NOT FOR READING THE VIOLATION. This used to
    # `continue` on `runner.host is None or PLATFORM_LINUX not in runner.labels`, which made the
    # rule blind on exactly the boxes it should catch: a `Windows, ARM64, self-hosted-arm64,
    # self-hosted-x64` runner and a `Linux, self-hosted-x64` runner (no arch label) BOTH reported
    # `verdict: OK` while `runs-on: [self-hosted, self-hosted-x64]` still selects them for x64
    # Linux work (measured). A foreign marker is a foreign marker whatever the host is; only the
    # OWN-marker derivation needs an arch.
    for runner in sorted(runners, key=lambda r: r.name):
        own = None
        if runner.host is not None and PLATFORM_LINUX in runner.labels:
            own = POOL_LABEL_X64 if runner.host == HOST_LABEL_X64 else POOL_LABEL_ARM64
        if own is None:
            # No derivable own marker: EVERY pool marker it carries is foreign to a host we could
            # not classify — reported, never skipped, because the silence was the false PASS.
            carried = sorted(runner.custom_labels & POOL_MARKERS)
            if carried:
                found.append(
                    Violation(
                        kind=VIOLATION_FOREIGN_POOL_MARKER,
                        runner=runner.name,
                        why=(
                            f"carries pool marker(s) {carried} but its own arch/platform cannot be "
                            f"derived ({'no X64/ARM64 label' if runner.host is None else 'not a Linux runner'}), "
                            f"so no pool it belongs to owns them. `runs-on: [self-hosted, "
                            f"self-hosted-x64]` is an AND, so an x64 Linux job is selected onto this "
                            f"box and silently mis-served (#6897). "
                            f"REMEDY: remove {carried[0]!r} from this runner."
                        ),
                    )
                )
            continue
        foreign = POOL_MARKERS - {own}
        carried = sorted(runner.custom_labels & foreign)
        if carried:
            found.append(
                Violation(
                    kind=VIOLATION_FOREIGN_POOL_MARKER,
                    runner=runner.name,
                    why=(
                        f"host is {runner.host} but it carries {', '.join(repr(c) for c in carried)}, "
                        f"a marker for another architecture's pool; `runs-on` matches an AND, so work "
                        f"pinned to that marker lands here and is mis-served (#6897). "
                        f"REMEDY: the marker is re-derived from the host."
                    ),
                )
            )

    # 2. The fallback is engaged somewhere, but never where it can serve x64 work.
    # `online`: an OFFLINE runner that carries the label serves nothing, so counting it
    # here would let `census` (which excludes offline capacity) and this check disagree
    # about the same question — one reporting no usable capacity, the other "engaged".
    # ⛔ A CARRIER THAT CANNOT SERVE IS NOT A CARRIER — the same predicate `apply`'s engage gate
    # uses (online AND x64 AND Linux). Before this, a Windows/X64 online carrier satisfied
    # "some x64 serves it", so a pool whose ONLY carrier was Windows never got rule 2's remedy
    # (attach the label to the x64 pool) and only ever got rule 1's (strip it) — leaving it armed
    # nowhere and the pool still red for the wrong reason.
    serving = [
        r for r in runners if r.serves_fallback and r.online and r.host == HOST_LABEL_X64
        and PLATFORM_LINUX in r.labels
    ]
    carriers = [r for r in runners if r.serves_fallback]
    if carriers and not serving:
        # ⛔ COUNT THE CARRIERS, NOT THE SERVERS. Printing `len(serving)` (which is 0 by
        # construction here) said "0 runner(s) carry 'ubuntu-latest'" next to rule 1 naming the
        # runner that carries it — and said it while claiming none is x64, when the carrier's own
        # host label was X64 (a Windows or offline x64 box). Two counts, two different questions.
        found.append(
            Violation(
                kind=VIOLATION_NO_X64_FALLBACK,
                runner="<pool>",
                why=(
                    f"{len(carriers)} runner(s) carry {FALLBACK_LABEL!r} and NOT ONE of them can "
                    f"serve it (none is an ONLINE x64 Linux runner), while "
                    f"{sum(1 for r in runners if r.host == HOST_LABEL_X64)} x64 runner(s) are "
                    f"registered. Every `{FALLBACK_LABEL}` job that does not reach GitHub-hosted "
                    f"therefore queues or is mis-served. REMEDY: attach {FALLBACK_LABEL!r} to the "
                    f"pool's ONLINE x64 Linux runners, and strip it from any carrier that "
                    f"cannot serve it."
                ),
            )
        )

    return found


def fallback_arm_cap() -> int | None:
    """The gate's engaging-arm cap, or None when the configured value is not a legal cap.

    Read from `ARM_CAP_ENV` with the gate's own default, so the two readers of one ruling cannot
    drift. An unset (or empty) value is the default; an unreadable one is None, which `plan`
    treats as zero armable slots and `check` reports as `ARM_CAP_UNREADABLE` — the same
    fail-closed direction the gate takes on the same input.
    """
    raw = os.environ.get(ARM_CAP_ENV)
    if raw is None or raw == "":
        return ARM_CAP_DEFAULT
    if not raw.isdigit():
        return None
    return int(raw)


def arm_order(runners: Sequence[Runner]) -> list[Runner]:
    """The runners an engaging arm may touch, in the gate's own order: ids ASCENDING.

    Mirrors `ci_runner_gate.sh::arm_ids` (`sort -n | awk -v n="$FALLBACK_ARM_MAX" 'NR<=n'` over
    the pool's managed ids) — which is why the order is by numeric id and not by name: the two
    orders disagree on a pool whose names carry no id (github-runner-10 sorts before
    github-runner-2 lexically).

    An OFFLINE runner is a registered slot that cannot serve a job, so it is not a candidate to
    arm; `serving_x64` applies the same reading to the post-state.
    """
    return sorted(
        (
            runner for runner in runners
            if unmanaged_reason(runner) is None and runner.online and runner.host == HOST_LABEL_X64
        ),
        key=lambda runner: runner.id,
    )


def desired_label_set(
    runner: Runner, *, engage: bool, strip_non_x64: bool = False, arm: bool = True
) -> list[str]:
    """The CUSTOM label set this runner SHOULD carry, under the x64-only invariant.

    Only labels a write may carry: GitHub's read-only ones (`self-hosted`, the platform,
    the arch) are not in the result, because sending them back is a 422 AND because this
    tool does not decide what a host IS — `ci_runner_gate.sh::labels_for` puts it plainly
    ("the architecture is not an opinion the caller supplies: it is the `ARM64` / `X64`
    label GitHub stamps from the host at registration"). Inventing `Linux` for a macOS
    runner made the plan's `after` unreachable for it — a diff no write could close.

    Preserves every CUSTOM label the tool does not own (a human's or another tool's), so
    `apply` is a reconcile and not a rewrite. The pool marker is derived from the host,
    because a marker that contradicts the host is how an ARM64 box came to advertise an
    x86_64 image.

    ⛔ An UNCLASSIFIABLE runner (neither `X64` nor `ARM64`) keeps its custom set UNCHANGED:
    `labels_for` refuses such a runner ("an unclassifiable runner is a named failure the
    caller counts, never a guess"), and a guess here would strip `ubuntu-latest` off a box
    nobody measured. `plan` reports it as unclassified instead.
    """
    host = runner.host
    if unmanaged_reason(runner) is not None:
        # Not a member of this pool (another platform) or unclassifiable: its custom set is
        # left byte-identical. Both gates are the gate script's own (`managed_ids` / `labels_for`).
        return sorted(runner.custom_labels)
    marker = POOL_LABEL_X64 if host == HOST_LABEL_X64 else POOL_LABEL_ARM64
    # ⛔ DROP BOTH MARKERS, THEN ADD THIS HOST'S. Filtering only the fallback left a runner
    # advertising the OTHER arch's pool (`self-hosted-x64` on an ARM64 box), which `runs-on`'s AND
    # semantics turns into an x64 job landing on ARM64 — and it made `apply` and
    # `ci_runner_gate.sh` disagree about the same runner's label set.
    out = {label for label in runner.custom_labels if label not in POOL_MARKERS}
    out.add(marker)
    # ⛔ THE DIRECTION IS EXPLICIT, AND THE OWNER-GATED HALF IS NEVER THE DEFAULT.
    if not engage:
        # `--disengage`: the explicit disarm, every managed runner (`ci_runner_gate.sh::labels_for`
        # with the pass `off` — "in mode=github the pool is stripped by design").
        out.discard(FALLBACK_LABEL)
    elif host == HOST_LABEL_X64 and arm:
        # The ADDITIVE half — the reason this PR exists. It contradicts nothing.
        out.add(FALLBACK_LABEL)
    elif host == HOST_LABEL_X64:
        # ⛔ A CAPPED-OUT x64 RUNNER IS NOT ARMED, AND NOT STRIPPED EITHER. Its fallback state is
        # left exactly as read: a carrier beyond the cap keeps the label (removing it is the
        # destructive direction `--disengage` owns), and a non-carrier does not get it (that is
        # the cap). `plan` reports both cases under `over_cap`.
        pass
    elif strip_non_x64:
        # ⛔ The x64-ONLY half, and only on request. Stripping the label off a non-x64
        # runner is the step #6897/#9878 reserve (see `tools/ci_runner_labels.py` R2), so it
        # is `--strip-non-x64`, never a default: an `apply` an operator runs to ADD capacity
        # must not silently remove the label from the only boxes a job can reach.
        out.discard(FALLBACK_LABEL)
    # else: a non-x64 carrier KEEPS the label — `plan` reports it as owner-gated.
    return sorted(out)


def unmanaged_reason(runner: Runner) -> str | None:
    """Why this runner is NOT ours to relabel, or None when it is a pool member we reconcile.

    Three gates, and each one is named for the thing that actually implements it:

    * the PLATFORM gate — this file's own, because a macOS runner cannot join the Linux pool;
    * `ci_runner_gate.sh::managed_ids` — the POOL-MARKER gate: "A runner carrying neither marker
      is not ours to relabel, and leaving it untouched is the conservative reading";
    * `ci_runner_gate.sh::labels_for` — the ARCH gate: a runner whose host arch it cannot
      classify is a named failure, never a guess.

    A runner excluded by any gate is REPORTED and left byte-identical — never rewritten on a
    guess, and never a diff the plan can never close.
    """
    if PLATFORM_LINUX not in runner.labels:
        return (
            "is not a Linux runner, so it is not a member of the Linux self-hosted pool this label "
            "belongs to — left untouched"
        )
    # ⛔ THE MARKER GATE IS NOT DECORATION. Without it `apply` attached `self-hosted-x64` AND
    # `ubuntu-latest` to any Linux/x64 box — an unprovisioned machine the operator never enrolled
    # would start receiving both `ubuntu-latest` and `[self-hosted, self-hosted-x64]` jobs, while
    # this function's own docstring promised such a runner was left alone (measured probe: a
    # Linux/X64 runner with custom labels `['gpu']` planned -> `['gpu','self-hosted-x64',
    # 'ubuntu-latest']`). Latent on the measured pool — every runner there carries a marker — but
    # the contract and the code disagreed, and the code was the one that would run.
    if not POOL_MARKERS & runner.custom_labels:
        return (
            "carries neither pool marker ("
            + " / ".join(sorted(POOL_MARKERS))
            + "), so it is not ours to relabel — left untouched (`ci_runner_gate.sh::managed_ids`)"
        )
    if runner.host is None:
        return (
            "carries neither X64 nor ARM64, so its pool marker cannot be derived — left untouched; "
            "an unclassifiable runner is a named failure, never a guess (`ci_runner_gate.sh::labels_for`)"
        )
    return None


def plan(
    runners: Sequence[Runner],
    *,
    engage: bool,
    strip_non_x64: bool = False,
    arm_cap: int | object | None = _ARM_CAP_FROM_ENV,
) -> Plan:
    """The reconcile. Idempotent: an already-correct pool yields an empty Plan.

    ⛔ THE ENGAGING ARM IS CAPPED AT THE GATE'S OWN BOUND (see the module docstring). `arm_cap`
    defaults to the value read from `CI_GATE_FALLBACK_ARM_MAX`, which is the same variable
    `agent-infra/scripts/ci_runner_gate.sh` reads; tests pass it explicitly so a test can never
    depend on the ambient environment of the box it runs on.
    """
    cap = fallback_arm_cap() if arm_cap is _ARM_CAP_FROM_ENV else arm_cap
    result = Plan(arm_cap=cap)
    # The arm's candidates, ids ascending — `arm_ids`' order. `engage` only: a disarm is bounded
    # by nothing in the gate (`labels_for` strips every managed runner when the pass is off).
    armable = arm_order(runners) if engage else []
    armed = {runner.name for runner in armable[: cap or 0]}
    result.armed = sorted(armed)
    for runner in sorted(runners, key=lambda r: r.name):
        why = unmanaged_reason(runner)
        if why is not None:
            result.unclassified[runner.name] = why
            # ⛔ AND IF IT IS A CARRIER, `check` PRINTS A REMEDY THIS RUN WILL NOT PERFORM. Recorded
            # on the WRITE's own payload so `apply` says it too: a silent skip is how a reader
            # concludes the tool is broken rather than out of scope.
            if runner.serves_fallback:
                result.out_of_scope[runner.name] = (
                    f"carries {FALLBACK_LABEL!r}, so `check` names it as a violation, but it "
                    f"{why} — the strip it prints is MANUAL; no run of this tool performs it"
                )
            continue
        in_arm = runner.name in armed
        result.add(
            runner,
            desired_label_set(runner, engage=engage, strip_non_x64=strip_non_x64, arm=in_arm),
        )
        if engage and cap is None and not unmanaged_reason(runner):
            result.over_cap[runner.name] = (
                f"not armed: {ARM_CAP_ENV}={os.environ.get(ARM_CAP_ENV)!r} is not a legal cap, and "
                f"an unreadable cap is a refusal, not an unbounded arm (`ci_runner_gate.sh` refuses "
                f"the pass on the same value)"
            )
        elif engage and runner in armable and not in_arm:
            # ⛔ THE CAP IS REPORTED, NOT SILENTLY APPLIED. A runner left unarmed because lower ids
            # hold the slots is the ruling working; a runner still carrying the label from an
            # earlier, wider arm is capacity somebody already committed. Both are named, so the
            # residual cannot read as a clean pool — and widening it is the #10912 reopen, not
            # something this verb may decide.
            result.over_cap[runner.name] = (
                f"is beyond the engaging-arm cap ({cap}) the gate that owns "
                f"{FALLBACK_LABEL!r} applies ({ARM_CAP_ENV}, OWNER RULING 2026-11-02 — "
                f"daniel-ospina/tortoise#9878); it "
                + ("keeps the label it already carries — removing it is `--disengage`"
                   if runner.serves_fallback
                   else "is not armed; widening the arm is the #10912 reopen, never a lane edit")
            )
        if engage and not strip_non_x64 and runner.host == HOST_LABEL_ARM64 and runner.serves_fallback:
            # ⛔ THE STRIP IS NOT OURS TO PERFORM BY DEFAULT. Removing `ubuntu-latest` from a
            # non-x64 runner is the step #6897/#9878 reserve (`tools/ci_runner_labels.py` R2:
            # "removing the label is the step that can strand a job, and #6897/#9878 own that
            # decision"), and this PR's own body records it as "not adoptable without a reopen".
            # So it is REPORTED as owner-gated — visible, never silent — and performed only when
            # the operator names it (`--strip-non-x64`). A silent skip would be the "diff no write
            # can close" failure the `unclassified` channel exists to prevent.
            result.owner_gated[runner.name] = (
                f"is an {HOST_LABEL_ARM64} runner carrying {FALLBACK_LABEL!r}; stripping it is "
                f"owner-gated (#6897/#9878) — re-run with `--strip-non-x64` to perform it"
            )
    return result


def check(
    runners: Sequence[Runner],
    *,
    queued_oldest_seconds: int | None = None,
    queued_floor_seconds: int = 1800,
    engage: bool = True,
    strip_non_x64: bool = False,
) -> dict[str, Any]:
    """The verdict. `ok` is False ONLY for a named violation — never for "idle pool".

    An idle pool with GitHub-hosted healthy is a legitimate topology, so it is
    REPORTED (`starvation_suspected`) and never reds: this tool must not red main
    for a design decision it does not govern.
    """
    if not runners:
        # Zero rows is a read failure (wrong scope, an empty page, a paginated read that
        # yielded nothing), never a clean pool. Named, so it cannot read as green.
        return {
            "verdict": VIOLATION_UNMEASURED_EMPTY_POOL,
            "ok": False,
            "violations": [
                {
                    "kind": VIOLATION_UNMEASURED_EMPTY_POOL,
                    "runner": "<pool>",
                    "why": "the runner enumeration returned ZERO runners — nothing was measured; "
                    "this is a read failure, not an absence of violations.",
                }
            ],
            "census": census(runners),
            "queued_oldest_seconds": queued_oldest_seconds,
            "queued_floor_seconds": queued_floor_seconds,
            "starvation_suspected": False,
            "planned_changes": {},
            "unclassified": {},
            "owner_gated": {},
            "over_cap": {},
            "out_of_scope": {},
            "arm_cap": None,
            "arm": [],
            "engage": engage,
        }
    found = violations(runners)
    pending = plan(runners, engage=engage, strip_non_x64=strip_non_x64)
    if engage and pending.arm_cap is None:
        found.append(
            Violation(
                kind=VIOLATION_ARM_CAP_UNREADABLE,
                runner="<pool>",
                why=(
                    f"{ARM_CAP_ENV}={os.environ.get(ARM_CAP_ENV)!r} is not a legal engaging-arm "
                    f"cap, so NOTHING was armed — an unreadable cap is a refusal, not an unbounded "
                    f"arm (`ci_runner_gate.sh` refuses the pass on the same value)."
                ),
            )
        )
    # ⛔ "ENGAGED" IS NOT "USABLE". Counting an OFFLINE carrier as engagement made the exact state
    # this tool exists to surface — label armed, nothing ONLINE can serve it, queue absorbing —
    # emit zero signal: verdict OK, starvation_suspected False, an absorbing queue and no note.
    starving = (
        queued_oldest_seconds is not None
        and queued_oldest_seconds >= queued_floor_seconds
        and not any(r.serves_fallback and r.online for r in runners)
    )
    verdict = "OK" if not found else "VIOLATION"
    return {
        "verdict": verdict,
        "ok": not found,
        "violations": [v.as_dict() for v in found],
        "census": census(runners),
        "queued_oldest_seconds": queued_oldest_seconds,
        "queued_floor_seconds": queued_floor_seconds,
        "starvation_suspected": starving,
        "planned_changes": {
            name: {"before": before, "after": after} for name, (_id, before, after) in pending.changes.items()
        },
        "unclassified": dict(pending.unclassified),
        "owner_gated": dict(pending.owner_gated),
        "over_cap": dict(pending.over_cap),
        "out_of_scope": dict(pending.out_of_scope),
        "arm_cap": pending.arm_cap,
        "arm": list(pending.armed),
        "engage": engage,
    }


# --------------------------------------------------------------------------- I/O
REPO = os.environ.get("TORTOISE_REPO", "daniel-ospina/tortoise")


def fetch_runners(repo: str = REPO) -> list[Runner]:
    """All registered self-hosted runners, following pagination."""
    proc = subprocess.run(
        ["gh", "api", "--paginate", f"repos/{repo}/actions/runners", "--jq", ".runners[]"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"cannot enumerate runners for {repo}: {proc.stderr[:500]}")
    # `--paginate` emits one JSON object PER PAGE concatenated, so parse with a
    # stream decoder rather than `json.loads` on the whole blob.
    decoder = json.JSONDecoder()
    text, index, rows = proc.stdout, 0, []
    while index < len(text):
        while index < len(text) and text[index] in " \t\r\n":
            index += 1
        if index >= len(text):
            break
        obj, index = decoder.raw_decode(text, index)
        rows.append(obj)
    return [parse_runner(row) for row in rows]


#: The queue-head scan bound the OWNING gate applies, read from the SAME variable and with the SAME
#: default (`ci_runner_gate.sh`, the `QUEUE_HEAD_SCAN_MAX` declaration:
#: `QUEUE_HEAD_SCAN_MAX="${CI_GATE_QUEUE_HEAD_SCAN_MAX:-5}"`). One value, two readers.
QUEUE_HEAD_SCAN_MAX_ENV = "CI_GATE_QUEUE_HEAD_SCAN_MAX"
QUEUE_HEAD_SCAN_MAX_DEFAULT = 5


@dataclass(frozen=True)
class QueueProbe:
    """What the queue read actually established. `oldest_seconds is None` NEVER means "no queue".

    `oldest_seconds`   age of the HEAD OF REAL DEMAND — the oldest run that HAS a job in
                       `queued` status — or None when there is no such run (a readable zero) or
                       nothing could be measured (`note` says which).
    `ghosts`           QUEUED runs in front of the head with ZERO queued jobs, excluded from the
                       age. GitHub leaves a run whose jobs were never created in `queued`
                       permanently (measured on daniel-ospina/agent-infra: three runs still
                       `queued` 30 days later with no jobs, so the age read 2,612,687 s of "queue
                       wait" on a repo whose real demand was hours old).
    `head_run`         the run the age was taken from, so a reader can check the measurement.
    `note`             why there is no age, or why the head could not be proved.
    """

    oldest_seconds: int | None = None
    ghosts: int = 0
    head_run: int | None = None
    note: str | None = None


def queue_head_scan_max() -> int | None:
    """The gate's queue-head scan bound, or None when the configured value is not a legal bound.

    Same fail-closed contract as `fallback_arm_cap`: `ci_runner_gate.sh` refuses the pass on a
    non-numeric bound (and on `0`, which would disarm the leg rather than bound it), so a bad
    value here measures NOTHING rather than scanning an arbitrary number of runs.
    """
    raw = os.environ.get(QUEUE_HEAD_SCAN_MAX_ENV)
    if raw is None or raw == "":
        return QUEUE_HEAD_SCAN_MAX_DEFAULT
    if not raw.isdigit() or int(raw) == 0:
        return None
    return int(raw)


def _queued_job_count(repo: str, run_id: int) -> int | None:
    """How many jobs of this run are in `queued` status; None when the read could not be made.

    A run is DEMAND FOR A RUNNER only if it has a job in `queued` status — nothing else can be
    scheduled, and nothing else is evidence a runner is missing. `None` is "could not look",
    which is a different fact from `0` ("looked, and this run is a ghost") and must never be
    collapsed into it: discounting real demand on a failed call is the fail-open direction.
    """
    proc = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100",
            "--jq",
            '[.jobs[] | select(.status == "queued") | .id] | length',
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    # `--paginate` applies `--jq` PER PAGE, so the counts arrive one line per page and must be
    # summed: reading line 1 alone undercounts a run whose queued jobs sit on a later page, which
    # is the GHOST shape on exactly the runs this scan exists to judge.
    total = 0
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.isdigit():
            return None
        total += int(line)
    return total


def fetch_queue_probe(repo: str = REPO) -> QueueProbe:
    """The queue's HEAD OF REAL DEMAND, measured the way the gate that owns this signal does.

    ⛔ THE DEFINITION IS THE GATE'S, NOT `min(created_at)` OVER EVERY QUEUED RUN (review round 3,
    P2). `ci_runner_gate.sh`'s head scan walks the page oldest-first and stops at the first run
    that HAS a queued job, because a run GitHub calls `queued` with nothing in a schedulable state
    is a GHOST: its age is unbounded and means nothing, and it is not demand any runner could
    serve. `min(created_at)` over every queued run reports a ghost's age as "queue wait", which is
    how a two-day-old phantom became this tool's starvation signal.

    ⛔ EVERY EXIT IS THE GATE'S OWN DOCTRINE: a ghost is discounted only on POSITIVE evidence (a
    readable jobs read showing zero queued jobs), an UNREADABLE jobs read is not evidence of
    anything and keeps the run as the head, and a scan that runs out of window without finding
    demand measures nothing (it does not fall back to the oldest run).
    """
    import datetime as _dt

    proc = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs?status=queued&per_page=100",
            "--jq",
            r'.workflow_runs[] | "\(.id)\t\(.created_at)"',
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return QueueProbe(note="the queued-run read failed, so the queue age is UNMEASURED")

    candidates: list[tuple[int, _dt.datetime]] = []
    for line in proc.stdout.splitlines():
        row = line.strip()
        if not row:
            continue
        run_id, _, stamp = row.partition("\t")
        if not run_id.isdigit() or not stamp:
            # A row we cannot identify is not a candidate: it cannot be scanned, and inventing a
            # timestamp for it would age the queue from something nobody read.
            continue
        try:
            created = _dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.UTC)
        except ValueError:
            continue
        candidates.append((int(run_id), created))

    if not candidates:
        # A successful read with no readable queued run is a READABLE ZERO (an idle queue) when the
        # payload carried no rows at all; a payload that carried rows we could not parse measured
        # nothing. `proc.stdout.strip()` is what separates the two.
        if proc.stdout.strip():
            return QueueProbe(note="no queued-run row in the read carried an id and a timestamp")
        return QueueProbe(note="no run is queued")

    cap = queue_head_scan_max()
    if cap is None:
        return QueueProbe(
            note=(
                f"{QUEUE_HEAD_SCAN_MAX_ENV}={os.environ.get(QUEUE_HEAD_SCAN_MAX_ENV)!r} is not a "
                f"legal scan bound, so the head of demand was NOT measured (`ci_runner_gate.sh` "
                f"refuses the pass on the same value)"
            )
        )

    candidates.sort(key=lambda row: row[1])  # oldest first — the gate's own walk order
    scanned = 0
    ghosts = 0
    for run_id, created in candidates:
        if scanned >= cap:
            return QueueProbe(
                ghosts=ghosts,
                note=(
                    f"the {scanned} oldest of {len(candidates)} queued run(s) report ZERO jobs in "
                    f"'queued' status and the head of real demand is beyond the "
                    f"{QUEUE_HEAD_SCAN_MAX_ENV}-run scan window — UNMEASURED"
                ),
            )
        scanned += 1
        queued_jobs = _queued_job_count(repo, run_id)
        if queued_jobs is None:
            # Could not look: this is NOT a ghost. Keep it as the head and say so — discounting
            # real demand on a failed read is how a stalled pool reads healthy.
            return QueueProbe(
                oldest_seconds=_age_seconds(created),
                ghosts=ghosts,
                head_run=run_id,
                note="the jobs read FAILED for a candidate run, so it is kept as the queue head (no discount)",
            )
        if queued_jobs == 0:
            ghosts += 1
            continue
        return QueueProbe(
            oldest_seconds=_age_seconds(created),
            ghosts=ghosts,
            head_run=run_id,
        )

    # Every queued run in the read was scanned and every one of them has zero queued jobs: a
    # COMPLETE measurement whose answer is "nothing is waiting for a runner". A readable zero, not
    # a failed read.
    return QueueProbe(
        ghosts=ghosts,
        note=f"all {ghosts} queued run(s) report ZERO jobs in 'queued' status — nothing is waiting for a runner",
    )


def _age_seconds(created: datetime.datetime) -> int | None:
    """Seconds since `created`, or None for a timestamp in the FUTURE (a clock skew no reading can
    resolve — the gate leaves the age empty for the same input rather than rounding it to 0 and
    reporting a fresher queue than anyone measured).
    """
    import datetime as _dt

    age = int((_dt.datetime.now(_dt.UTC) - created).total_seconds())
    return max(0, age) if age >= 0 else None


def fetch_oldest_queued_seconds(repo: str = REPO) -> int | None:
    """Age of the queue's HEAD OF REAL DEMAND, or None when there is none / it is unmeasured.

    ⛔ `List workflow runs` takes **no** `sort`/`direction` parameter — an earlier version sent
    `per_page=1&sort=created&direction=asc` and got the NEWEST queued run (measured
    2026-11-07T17:51Z for a queue whose oldest entry was 2026-11-05T16:29Z), so
    `queued_oldest_seconds >= floor` never held and `starvation_suspected` — the one signal this
    verb exists to emit — was unreachable.

    Taking `min(created_at)` over every run GitHub calls `queued` fixed that and introduced the
    gate's own known failure: a GHOST run (nothing in a schedulable state) ages without bound.
    So the answer is the head of REAL demand — see `fetch_queue_probe` — and this is the thin
    reader `check` uses. Ghosts are counted there, not silently dropped.
    """
    return fetch_queue_probe(repo).oldest_seconds


def project(runners: Sequence[Runner], pending: Plan) -> list[Runner]:
    """The pool AS IT WOULD READ after `pending` is written. Pure; performs no I/O.

    This is what makes `--dry-run` a PRE-FLIGHT rather than a printout: the documented
    operator sequence is "dry-run first, then apply", so the dry run must be judged by the
    same gates the write will be, on the state the write will produce.
    """
    out: list[Runner] = []
    for runner in runners:
        change = pending.changes.get(runner.name)
        if change is None:
            out.append(runner)
            continue
        _id, _before, after = change
        # The read-only labels cannot be written, so they are carried over unchanged.
        out.append(replace(runner, labels=frozenset((runner.labels - runner.custom_labels) | set(after))))
    return out


def serving_x64(post_runners: Sequence[Runner]) -> list[Runner]:
    """ONLINE x64 Linux runners that can actually serve the fallback label."""
    return [
        r for r in post_runners
        if r.online and r.serves_fallback and r.host == HOST_LABEL_X64 and PLATFORM_LINUX in r.labels
    ]


def engage_gate(post: Mapping[str, Any] | None, post_runners: Sequence[Runner], *, engage: bool) -> dict[str, str] | None:
    """⛔ AN ENGAGE THAT DELIVERED NO *ONLINE X64* SERVICE IS NOT A RECONCILE.

    `check` cannot judge this: "the fallback is engaged nowhere" is a legitimate steady state
    (mode=github strips the pool by design), so it must not red there. But `apply --engage`
    promised CAPACITY, and on a pool whose x64 arm is empty — or entirely offline — the write
    lands, the read-back is clean, and the queue still cannot get a runner: the same false
    PASS the default-direction fix closed, one layer down. A READ FAILURE outranks a capacity
    reading — "I could not look" must never be the last word — so the gate is suppressed when
    the pool is UNMEASURED.
    """
    if post is None or not engage or serving_x64(post_runners):
        return None
    if post.get("verdict") == VIOLATION_UNMEASURED_EMPTY_POOL:
        return None
    return {
        "kind": VIOLATION_ENGAGE_NO_X64_SERVER,
        "runner": "<pool>",
        "why": (
            f"engage attaches {FALLBACK_LABEL!r} and NOT ONE online x64 runner serves it after "
            f"the write ({len(post_runners)} runner(s) read back) — the reconcile delivers no "
            f"capacity, however clean the label state looks."
        ),
    }


def apply_plan(pending: Plan, *, dry_run: bool, repo: str = REPO) -> list[dict[str, Any]]:
    """Write the label sets. Returns one record per runner, applied or planned."""
    records: list[dict[str, Any]] = []
    for name, (runner_id, before, after) in sorted(pending.changes.items()):
        record = {"runner": name, "id": runner_id, "before": before, "after": after, "applied": False}
        if not dry_run:
            # ⛔ The PUT body may carry CUSTOM labels only; `after` already is. The
            # `GITHUB_OWNED` filter is a SECOND net for a payload whose labels carried no
            # `type` (a fixture), not the classification itself.
            # `self-hosted`, `Linux`, `X64`/`ARM64` are stamped by GitHub at registration and are
            # READ-ONLY: sending them back is a 422 ("Cannot set labels that duplicate existing
            # read-only labels: Linux, X64, self-hosted") that leaves the runner untouched.
            body = json.dumps({"labels": [label for label in after if label not in GITHUB_OWNED]})
            try:
                proc = subprocess.run(
                    ["gh", "api", "-X", "PUT", f"repos/{repo}/actions/runners/{runner_id}/labels", "--input", "-"],
                    input=body,
                    capture_output=True,
                    text=True,
                )
            except OSError as exc:
                # ⛔ A TRANSPORT FAILURE ON ONE RUNNER MUST NOT ABORT THE RECONCILE. An unguarded
                # `subprocess.run` (gh missing, a signal, a fork failure) raised out of the loop
                # AFTER earlier writes had landed, losing every record of what was written.
                record["error"] = f"transport: {exc}"
                records.append(record)
                continue
            record["applied"] = proc.returncode == 0
            if proc.returncode != 0:
                record["error"] = proc.stderr[:300]
        records.append(record)
    return records


def _print_human(report: Mapping[str, Any]) -> None:
    c = report["census"]
    print(f"runners={c['runners']} online={c['online']} fallback={c['fallback_label']!r}")
    for host, bucket in sorted(c["by_host"].items()):
        # ⛔ THE RENDER COUNTS WHAT THE QUEUE CAN REACH. `census`'s `serving_fallback` is deliberately
        # raw (the starvation note needs "armed", not "usable"), so printing it here reported
        # capacity that does not exist: measured, `X64 serving_fallback=16` while two of those
        # sixteen were OFFLINE (`pi-runner-cell3-x64-9`, `pi-runner-x64-1`) — the line an operator
        # reads to answer "have we got capacity?" overstated it by exactly the dead slots. The
        # offline carriers are still COUNTED, named as such: hiding them would lose the fact that
        # the labels are armed on a dead box.
        offline = bucket.get("serving_offline", 0)
        print(
            f"  {host:<8} total={bucket['total']:<3} idle={bucket['idle']:<3} "
            f"serving_fallback={bucket['serving_fallback'] - offline}"
            + (f" (+{offline} offline, cannot serve)" if offline else "")
        )
    print(f"  x64 idle (unused capacity): {len(c['x64_idle'])} -> {', '.join(c['x64_idle'][:6])}"
          f"{' …' if len(c['x64_idle']) > 6 else ''}")
    if report.get("queued_oldest_seconds") is not None:
        print(
            f"  oldest queued run: {report['queued_oldest_seconds']}s "
            f"(floor {report['queued_floor_seconds']}s)"
        )
    if report.get("starvation_suspected"):
        # ⛔ THE NOTE MUST SAY WHAT WAS MEASURED. Printing "ENGAGED NOWHERE" directly under
        # `serving_fallback=1` pointed a human at a DISARMED gate when the arm was armed-but-dead —
        # a different fault with a different remedy (an offline carrier, not a disengaged one).
        armed = report["census"]["serving_fallback"]
        state = (
            f"the label is ARMED on {len(armed)} runner(s) but NOT ONE ONLINE runner can serve it "
            f"(an offline/unusable arm)"
            if armed
            else "the fallback is ENGAGED NOWHERE — idle-pool topology"
        )
        print(f"  NOTE: demand is queued past the floor while {state} — absorbing queue (#10912). "
              f"Not a violation.")
    # ⛔ A SKIPPED RUNNER IS NAMED HERE TOO. `apply` printed it and `check` — the form the
    # docstring calls the tripwire — did not, so a human render could read "verdict: OK" while a
    # runner the tool never measured was still accepting `ubuntu-latest` work.
    for name, why in sorted(report.get("unclassified", {}).items()):
        print(f"  ⚠️  UNRECONCILED {name}: {why}")
    # ⛔ THE SAME CHANNEL, ON THE TRIPWIRE'S OWN RENDER. `check --json` carried `owner_gated` and
    # the human render did not, so the form the docstring calls the tripwire printed
    # `REMEDY: strip 'ubuntu-latest' from this runner` with no note that `apply` will refuse to do
    # it without `--strip-non-x64`. Asymmetry between the two renders is how a reader concludes the
    # tool is broken rather than owner-gated.
    for name, why in sorted(report.get("owner_gated", {}).items()):
        print(f"  ⚠️  OWNER-GATED {name}: {why}")
    # ⛔ THE CAP IS ON THE VERDICT'S OWN RENDER. A residual the plan declined to write must be
    # readable from `check`, not only from an `apply` run: the reviewer's finding was that this tool
    # armed 5.3× the containment a recorded ruling permits, and a silent cap would hide the same
    # class in the other direction.
    for name, why in sorted(report.get("over_cap", {}).items()):
        print(f"  ⚠️  OVER-CAP {name}: {why}")
    # ⛔ THE SAME CHANNEL AS `unclassified`, FOR THE ONE CASE WHERE IT MATTERS: a runner whose
    # stated REMEDY this tool cannot execute. Printing it as plain `UNRECONCILED` right beside
    # `REMEDY: strip it` is what made the pair read as a bug rather than as a scope boundary.
    for name, why in sorted(report.get("out_of_scope", {}).items()):
        print(f"  ⚠️  OUT-OF-SCOPE {name}: {why}")
    if not report["violations"]:
        print(f"verdict: {report['verdict']}")
    else:
        print(f"verdict: {report['verdict']} ({len(report['violations'])} violation(s))")
        for v in report["violations"]:
            print(f"  ❌ {v['kind']} {v['runner']}: {v['why']}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "verb",
        choices=("census", "check", "apply"),
        help="census = counts; check = the invariant (exit 1 on a violation); apply = reconcile",
    )
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    direction = parser.add_mutually_exclusive_group()
    direction.add_argument(
        "--engage",
        action="store_true",
        help="desired topology ATTACHES the fallback to the x64 pool (the DEFAULT for apply)",
    )
    direction.add_argument(
        "--disengage",
        action="store_true",
        help="desired topology STRIPS the fallback from every managed runner — explicit, never the default",
    )
    # ⛔ `--strip-non-x64` deliberately is NOT in this group. It QUALIFIES the direction
    # (`--engage --strip-non-x64` is the x64-only topology), so putting it here rejected the one
    # combination the help text describes — argparse answered `not allowed with argument
    # '--engage'` to the operator who asked for exactly the documented thing.
    parser.add_argument(
        "--strip-non-x64",
        action="store_true",
        help=(
            "apply: additionally REMOVE the fallback from non-x64 runners (the x64-only "
            "topology). Owner-gated (#6897/#9878 own that decision), so it is never performed "
            "by a bare `apply`; without it such a carrier is reported as `owner_gated`"
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="apply: print the changes, write nothing")
    parser.add_argument("--queued-floor-seconds", type=int, default=1800)
    parser.add_argument("--from-fixture", type=Path, help="read a runners JSON payload instead of the API")
    args = parser.parse_args(argv)

    # ⛔ THE DESTRUCTIVE DIRECTION IS NEVER THE DEFAULT. A bare `apply` used to perform the
    # strip-and-attach-nowhere state: a verb named "apply" that quietly removed the only
    # runners answering `ubuntu-latest` is a footgun whose output looks like success. Engage
    # is the direction #10912 needs (the pool is the capacity), so it is the default; the
    # strip is `--disengage`.
    engage = not args.disengage
    # ⛔ THE OWNER-GATED HALF IS OPT-IN, AND `--disengage` IMPLIES IT. `--disengage` already IS
    # the explicit strip direction, so asking for it and not stripping non-x64 runners would make
    # the flag mean something other than its name.
    strip_non_x64 = bool(args.strip_non_x64) or not engage

    # ⛔ A FIXTURE'S IDS ARE NOT THIS REPO'S RUNNERS. The measured fixture carries the real
    # production ids 1-21, so `apply --from-fixture` without `--dry-run` would PUT label sets
    # onto whichever runners happen to hold those ids — relabelling boxes nobody asked it to
    # touch. Refuse rather than write.
    if args.verb == "apply" and args.from_fixture and not args.dry_run:
        print(
            "refusing: `apply --from-fixture` requires `--dry-run` — a fixture carries runner ids "
            "from another census (the measured one carries this repo's real ids), so a write "
            "from it would relabel runners nobody asked it to touch.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # ⛔ A CAP OF ZERO IS A DISARMED GATE, NOT A QUIET NO-OP. `ci_runner_gate.sh`'s
    # `FALLBACK_ARM_MAX` block:
    # "A value of 0 ... means the fallback is DISARMED: the pool is released and the engaging
    # pass REFUSES, rather than reporting a successful engage carrying nothing." This tool must
    # not answer a disarmed gate with "0 changes — already at the desired topology" and exit 0:
    # that is a report of a successful engage which delivered nothing, and an operator reads it
    # as "the arm is in place". Refused for `--dry-run` too — the pre-flight is judged by the
    # gates the write will be.
    if args.verb == "apply" and engage and fallback_arm_cap() == 0:
        print(
            f"refusing: {ARM_CAP_ENV}=0 means the fallback is DISARMED — `ci_runner_gate.sh` "
            f"releases the pool and REFUSES its engaging pass on that value, rather than "
            f"reporting a successful engage carrying nothing. Nothing was written.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if args.from_fixture:
        runners = parse_runners(json.loads(args.from_fixture.read_text()))
        oldest = None
    else:
        runners = fetch_runners(args.repo)
        oldest = fetch_oldest_queued_seconds(args.repo) if args.verb != "apply" else None

    if args.verb == "census":
        report = census(runners)
        print(json.dumps(report, indent=2, sort_keys=True) if args.json else report)
        return 0

    report = check(
        runners,
        queued_oldest_seconds=oldest,
        queued_floor_seconds=args.queued_floor_seconds,
        engage=engage,
        strip_non_x64=strip_non_x64,
    )

    if args.verb == "check":
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        return 0 if report["ok"] else 1

    pending = plan(runners, engage=engage, strip_non_x64=strip_non_x64)
    records = apply_plan(pending, dry_run=args.dry_run, repo=args.repo)

    # ⛔ A 200 ON THE PUT IS NOT THE POOL'S STATE. `rc == 0` says the API accepted a body; it
    # cannot say the label took, that `ci_runner_gate.sh` has not already reversed it, or that
    # the pool now serves anyone. So RE-READ after the write and report the POST verdict — the
    # pre-state verdict is the one thing that is certainly stale once we have written.
    post: dict[str, Any] | None = None
    post_runners: list[Runner] = []
    serving_post: list[Runner] = []
    ok = report["ok"]
    if args.dry_run:
        # ⛔ A DRY RUN IS A PRE-FLIGHT, NOT A PRINTOUT. The USAGE block tells an operator to run
        # `apply --dry-run` FIRST, so the dry run has to be judged by the SAME gates the write
        # will be, on the state the write will produce. It used to `return 0` unconditionally
        # (`0 if args.dry_run or ok else 1`), which green-lit exactly the destructive write it
        # exists to screen: measured on an ARM-only pool, a dry run exited 0 and printed
        # "already at the desired topology" while the real apply then reported
        # ENGAGE_PRODUCED_NO_X64_SERVER *after* the writes.
        post_runners = project(runners, pending)
        post = check(
            post_runners,
            queued_oldest_seconds=oldest,
            queued_floor_seconds=args.queued_floor_seconds,
            engage=engage,
            strip_non_x64=strip_non_x64,
        )
        serving_post = serving_x64(post_runners)
        ok = post["ok"]
        gate = engage_gate(post, post_runners, engage=engage)
        if gate is not None:
            ok = False
            post = {**post, "ok": False, "verdict": gate["kind"], "violations": [*post["violations"], gate]}
            print(
                f"❌ {gate['kind']} (projected): not one online x64 runner would serve "
                f"{FALLBACK_LABEL!r} — this apply would deliver NO capacity.",
                file=sys.stderr,
            )
    else:
        # Every write took, AND the pool reads back clean. The PRE-state verdict is deliberately
        # not part of this: after a successful reconcile the pre-state is exactly what is no
        # longer true.
        ok = all(rec["applied"] for rec in records)
        post_runners = []
        try:
            post_runners = fetch_runners(args.repo)
            post = check(
                post_runners,
                queued_oldest_seconds=fetch_oldest_queued_seconds(args.repo),
                engage=engage,
                strip_non_x64=strip_non_x64,
            )
        except (SystemExit, OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            # `--paginate` can also yield a truncated stream (JSONDecodeError) or a malformed page
            # (KeyError/ValueError); catching only SystemExit let those abort AFTER the writes with
            # a traceback, losing the one record that matters: what was written, and that it is
            # unverified.
            print(
                f"WARNING: labels were written but the read-back failed ({exc}) — the post state is "
                f"UNVERIFIED, and this run must not be reported as a verified reconcile.",
                file=sys.stderr,
            )
            post = None
        # A WRITE WE COULD NOT READ BACK IS NOT A SUCCESS: fail closed, the same direction
        # `ci_runner_gate.sh` fails when its enumeration is unreadable.
        ok = ok and post is not None and post["ok"]
        serving_post = serving_x64(post_runners)
        gate = engage_gate(post, post_runners, engage=engage)
        if gate is not None:
            ok = False
            # ⛔ NO `already_unmeasured` BRANCH HERE. It was UNREACHABLE: `engage_gate` returns
            # None on `VIOLATION_UNMEASURED_EMPTY_POOL` (a read failure must outrank a capacity
            # reading), so `gate is not None` already implies the pool WAS measured. Forcing the
            # dead branch's condition to False left every test green — a guard that could not
            # fail, which is the class this file keeps closing. The precedence lives in
            # `engage_gate`, where it is reachable and where the unmeasured read is still the
            # printed fault.
            if post is not None:
                post = {**post, "ok": False, "verdict": gate["kind"], "violations": [*post["violations"], gate]}
                print(
                    f"❌ {gate['kind']}: no online x64 runner serves "
                    f"{FALLBACK_LABEL!r} after the write — engage delivered NO capacity.",
                    file=sys.stderr,
                )

    # ⛔ THE EXIT CODE MUST SEPARATE "THE WRITE FAILED" FROM "THE WRITE LANDED, AN OWNER DECISION
    # REMAINS". Both used to be `1`, so a wrapper could not tell a broken reconcile from one that
    # did exactly what this tool is permitted to do: on the measured pool a bare `apply` can never
    # reach `0` (three ARM64 carriers keep the label until `--strip-non-x64`, which is #6897/#9878's
    # decision), and calling that "failure" is how a correct run gets retried forever. A residual is
    # owner-gated ONLY when every planned write applied, the pool was read back, and every surviving
    # violation is one the operator can heal by naming the flag.
    owner_gated_only = False
    if not ok and post is not None and not args.dry_run and all(rec["applied"] for rec in records):
        remaining = post.get("violations", [])
        owner_gated_only = bool(remaining) and all(
            v["kind"] == VIOLATION_FALLBACK_ON_NON_X64 and v["runner"] in pending.owner_gated
            for v in remaining
        )
    if ok:
        exit_code = EXIT_OK
    elif owner_gated_only:
        exit_code = EXIT_OWNER_GATED
    else:
        exit_code = EXIT_FAILED

    payload = {
        "ok": ok,
        "ok_before": report["ok"],
        "dry_run": args.dry_run,
        # ⛔ NAME THE PROVENANCE. On a dry run `post` is the PROJECTION of the plan onto the
        # pre-state, not a read-back; a reader that cannot tell the two apart will treat an
        # unperformed write as verified.
        "post_is_projection": args.dry_run,
        "engage": engage,
        "strip_non_x64": strip_non_x64,
        "changes": records,
        "unclassified": dict(pending.unclassified),
        "owner_gated": dict(pending.owner_gated),
        "over_cap": dict(pending.over_cap),
        "out_of_scope": dict(pending.out_of_scope),
        "arm_cap": pending.arm_cap,
        "arm": list(pending.armed),
        "post": post,
        "owner_gated_only": owner_gated_only,
        "exit": exit_code,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{'would change' if args.dry_run else 'changed'}: {len(records)} runner(s)")
        for rec in records:
            state = "planned" if args.dry_run else ("applied" if rec["applied"] else f"FAILED {rec.get('error','')}")
            print(f"  {rec['runner']}: {rec['before']} -> {rec['after']}  [{state}]")
        if not records and ok:
            # ⛔ ONLY WHEN IT IS TRUE. An engage that changed nothing because the pool has no x64
            # arm — or only an unmanaged carrier — also produces zero records, and that run FAILS.
            print("  (already at the desired topology — idempotent, nothing to do)")
        for name, why in sorted(pending.unclassified.items()):
            print(f"  ⚠️  {name}: left untouched — {why}")
        for name, why in sorted(pending.owner_gated.items()):
            # ⛔ "LEFT UNTOUCHED" WAS FALSE FOR A RUNNER THE SAME RUN WRITES TO. An ARM64 carrier
            # that also carries the foreign `self-hosted-x64` marker IS reconciled (the marker is
            # dropped — that is this PR's round-6 fix), so printing "left untouched" beside
            # `pi-runner-vm-9: [...] -> [...] [planned]` made the render contradict itself. Only
            # the STRIP is skipped on such a runner, and the line now says exactly that.
            if name in pending.changes:
                print(f"  ⚠️  {name}: strip skipped (owner-gated) — its other owned labels are reconciled: {why}")
            else:
                print(f"  ⚠️  {name}: left untouched — {why}")
        # ⛔ WHY THE WRITE STOPPED SHORT, ON THE WRITE'S OWN RENDER. Without this an operator sees
        # `changed: 3 runner(s)` on a 16-member pool and reads the arm as complete — the cap is a
        # DECISION, so the runners it withheld are named, with the route that would widen it.
        for name, why in sorted(pending.over_cap.items()):
            print(f"  ⚠️  {name}: {why}")
        # ⛔ WHY THE REMEDY THE OPERATOR JUST READ IS NOT THIS TOOL'S TO PERFORM. `check` tells them
        # to strip a marker-less / foreign-platform carrier; `apply` skips it by design
        # (`managed_ids` / `labels_for` conservatism). Without this line the run looks like it
        # silently dropped the work it had just named.
        for name, why in sorted(pending.out_of_scope.items()):
            print(f"  ⚠️  OUT-OF-SCOPE {name}: {why}")
        if pending.armed:
            print(
                f"  arm: {len(pending.armed)} runner(s) at most (cap {pending.arm_cap} — "
                f"{ARM_CAP_ENV}) -> {', '.join(pending.armed[:6])}"
                f"{' …' if len(pending.armed) > 6 else ''}"
            )
        if post is not None:
            # ⛔ THE COUNT MUST USE THE GATE'S DEFINITION. `census`'s `serving_fallback` has no
            # `online` filter (deliberately: it is a raw census), so printing it here produced
            # "x64 serving=1" directly below "❌ no online x64 runner serves it". The render is what
            # a human acts on, so it counts what the gate counts.
            x64_serving = len(serving_post)
            # ⛔ THE SAME DEFINITION ON BOTH HALVES OF ONE LINE. `census`'s per-host
            # `serving_fallback` has no `online` filter, so summing it printed `non-x64 serving=1`
            # for an OFFLINE ARM64 carrier while the x64 half said 0 — two definitions, one line.
            others_serving = len(
                [r for r in post_runners if r.online and r.serves_fallback and r.host != HOST_LABEL_X64]
            )
            where = "projected (nothing written)" if args.dry_run else "read back after the write"
            print(
                f"post-state verdict: {post['verdict']} "
                f"(x64 serving={x64_serving}, non-x64 serving={others_serving} — {where})"
            )
        if owner_gated_only:
            print(
                f"  ⚠️ exit {EXIT_OWNER_GATED}: every planned write landed and the pool was read "
                f"back, but {len(post['violations'])} owner-gated residual(s) remain "
                f"(#6897/#9878) — re-run with `--strip-non-x64` to perform them"
            )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
