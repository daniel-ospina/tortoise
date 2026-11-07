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
  * its own header names this exact failure at `:926`: "a capped disarm would
    strand `ubuntu-latest` on the runners outside the arm".

A LIVE `ci_runner_gate.sh --status` read at 2026-11-07T17:09Z returns
`mode=local ... runners=21 online_idle=4 online_busy=16 offline=1`, so the
recorded STATE is stale rather than the CAPABILITY — which is exactly why this
tool decides from the measured pool and never from the log.

⛔ THE x64-ONLY DESIRED TOPOLOGY IS NOT A REVERSAL OF A RECORDED DECISION. A
reviewer read `:930`'s "ARCH IS DELIBERATELY NOT A FILTER HERE" as forbidding the
ARM64 strip; the gate's own write path falsifies that. `labels_for` (`:975`)
derives each runner's body from ITS OWN host and omits `ubuntu-latest` whenever
the pass is `off`, for EVERY managed runner — arch never enters the disarm —
and `:882` says it directly: "in mode=github the pool is stripped by design"
(`:507` words it "STRIPPED ON PURPOSE"). The `:930` block bounds the ENGAGE pass's cap (`arm_ids`, which runners
a fallback arm may touch), not whether an ARM64 runner may carry the label at
all. `docs/ops/ci-runner-x64.md` records the scope the #2327 decision left open:
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
                        REMEDY: strip the label from the non-x64 runner.

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
`ubuntu-latest` on the x64 pool ONLY — never on an ARM64 runner — and stripped
from every runner that is not x64. That is #10912's own "preferred" remedy.

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
    uv run python tools/ci_runner_topology.py apply --disengage # strip the fallback everywhere
    uv run python tools/ci_runner_topology.py check --queued-floor-seconds 1800

`apply` ENGAGES by default. The strip direction is `--disengage`, stated
explicitly, because a verb whose DEFAULT silently performs the destructive
direction is how a pool ends up with no runner answering a label every queued
job asks for. `apply --from-fixture` is refuse-without-`--dry-run`: a fixture
carries runner ids from another census (the measured one carries this repo's real
ids 1-21), so writing from it relabels runners nobody asked it to touch.

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

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The label `agent-infra/scripts/ci_runner_gate.sh` attaches to the self-hosted
#: pool as a GitHub-hosted fallback (`:178 FALLBACK_LABEL`). It promises an
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
        bucket = by_host.setdefault(runner.host or "UNKNOWN", {"total": 0, "idle": 0, "serving_fallback": 0})
        bucket["total"] += 1
        # ONLINE and not busy. An offline runner is registered capacity that cannot
        # serve anything; counting it as `idle` contradicts `x64_idle` in the same
        # output (measured: the two lines printed 16 and 15 for one pool).
        if runner.online and not runner.busy:
            bucket["idle"] += 1
        if runner.serves_fallback:
            bucket["serving_fallback"] += 1
    return {
        "runners": len(runners),
        "online": sum(1 for r in runners if r.online),
        "by_host": by_host,
        "fallback_label": FALLBACK_LABEL,
        "serving_fallback": sorted(r.name for r in runners if r.serves_fallback),
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
        found.append(
            Violation(
                kind=VIOLATION_FALLBACK_ON_NON_X64,
                runner=runner.name,
                why=(
                    f"carries {FALLBACK_LABEL!r} but {because}; an x64 Ubuntu-expected job "
                    f"routed here is silently mis-served (#6897). "
                    f"REMEDY: strip {FALLBACK_LABEL!r} from this runner."
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


def desired_label_set(runner: Runner, *, engage: bool) -> list[str]:
    """The CUSTOM label set this runner SHOULD carry, under the x64-only invariant.

    Only labels a write may carry: GitHub's read-only ones (`self-hosted`, the platform,
    the arch) are not in the result, because sending them back is a 422 AND because this
    tool does not decide what a host IS — `ci_runner_gate.sh:labels_for` puts it plainly
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
    out = {label for label in runner.custom_labels if label != FALLBACK_LABEL}
    out.add(marker)
    # ⛔ x64 ONLY. An ARM64 runner never carries the hosted x86_64 fallback: that is the
    # whole point of this file, and it is the same disarm `ci_runner_gate.sh` performs on
    # `off` for every managed runner (`:882` — "in mode=github the pool is stripped by
    # design"). Widening this to `if engage` alone would re-open #6897.
    if engage and host == HOST_LABEL_X64:
        out.add(FALLBACK_LABEL)
    return sorted(out)


def unmanaged_reason(runner: Runner) -> str | None:
    """Why this runner is NOT ours to relabel, or None when it is a pool member we reconcile.

    Both gates are taken from `ci_runner_gate.sh`'s own write path rather than invented here:
    `managed_ids` selects only runners carrying a pool marker ("A runner carrying neither marker
    is not ours to relabel, and leaving it untouched is the conservative reading"), and
    `labels_for` refuses a runner whose host arch it cannot classify. A runner excluded by either
    gate is REPORTED and left byte-identical — never rewritten on a guess, and never a diff the
    plan can never close.
    """
    if PLATFORM_LINUX not in runner.labels:
        return (
            "is not a Linux runner, so it is not a member of the Linux self-hosted pool this label "
            "belongs to — left untouched (`ci_runner_gate.sh:managed_ids`)"
        )
    if runner.host is None:
        return (
            "carries neither X64 nor ARM64, so its pool marker cannot be derived — left untouched; "
            "an unclassifiable runner is a named failure, never a guess (`ci_runner_gate.sh:labels_for`)"
        )
    return None


def plan(runners: Sequence[Runner], *, engage: bool) -> Plan:
    """The reconcile. Idempotent: an already-correct pool yields an empty Plan."""
    result = Plan()
    for runner in sorted(runners, key=lambda r: r.name):
        why = unmanaged_reason(runner)
        if why is not None:
            result.unclassified[runner.name] = why
            continue
        result.add(runner, desired_label_set(runner, engage=engage))
    return result


def check(
    runners: Sequence[Runner],
    *,
    queued_oldest_seconds: int | None = None,
    queued_floor_seconds: int = 1800,
    engage: bool = True,
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
            "engage": engage,
        }
    found = violations(runners)
    pending = plan(runners, engage=engage)
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
        "engage": engage,
    }


# --------------------------------------------------------------------------- I/O
REPO = os.environ.get("TORTOISE_REPO", "daniel-ospina/tortoise")


def _gh(args: Sequence[str], *, check_rc: bool = True) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check_rc and proc.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed rc={proc.returncode}: {proc.stderr[:500]}")
    return proc.stdout


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


def fetch_oldest_queued_seconds(repo: str = REPO) -> int | None:
    """Age of the oldest QUEUED run, or None when unreadable. Never guesses."""
    out = _gh(
        [
            "api",
            f"repos/{repo}/actions/runs?status=queued&per_page=1&sort=created&direction=asc",
            "--jq",
            ".workflow_runs[0].created_at // empty",
        ],
        check_rc=False,
    ).strip()
    if not out:
        return None
    import datetime as _dt

    try:
        created = _dt.datetime.strptime(out, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.UTC)
    except ValueError:
        return None
    return max(0, int((_dt.datetime.now(_dt.UTC) - created).total_seconds()))


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
        print(
            f"  {host:<8} total={bucket['total']:<3} idle={bucket['idle']:<3} "
            f"serving_fallback={bucket['serving_fallback']}"
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
        return 2

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
    )

    if args.verb == "check":
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        return 0 if report["ok"] else 1

    pending = plan(runners, engage=engage)
    records = apply_plan(pending, dry_run=args.dry_run, repo=args.repo)

    # ⛔ A 200 ON THE PUT IS NOT THE POOL'S STATE. `rc == 0` says the API accepted a body; it
    # cannot say the label took, that `ci_runner_gate.sh` has not already reversed it, or that
    # the pool now serves anyone. So RE-READ after the write and report the POST verdict — the
    # pre-state verdict is the one thing that is certainly stale once we have written.
    post: dict[str, Any] | None = None
    serving_post: list[Runner] = []
    ok = report["ok"]
    if not args.dry_run:
        # Every write took, AND the pool reads back clean. The PRE-state verdict is deliberately
        # not part of this: after a successful reconcile the pre-state is exactly what is no
        # longer true.
        ok = all(rec["applied"] for rec in records)
        post_runners: list[Runner] = []
        try:
            post_runners = fetch_runners(args.repo)
            post = check(
                post_runners,
                queued_oldest_seconds=fetch_oldest_queued_seconds(args.repo),
                engage=engage,
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
        # ⛔ AN ENGAGE THAT DELIVERED NO *ONLINE X64* SERVICE IS NOT A RECONCILE. `check` cannot
        # judge this: "the fallback is engaged nowhere" is a legitimate steady state (mode=github
        # strips the pool by design), so it must not red there. But `apply --engage` promised
        # CAPACITY, and on a pool whose x64 arm is empty — or entirely offline — the plan strips
        # the label off the only carriers and attaches it where nobody online can serve it. The
        # write lands, the read-back is clean, and the queue still cannot get a runner: the same
        # false PASS the default-direction fix closed, one layer down.
        serving_post = [
            r for r in post_runners if r.online and r.serves_fallback and r.host == HOST_LABEL_X64
            and PLATFORM_LINUX in r.labels
        ]
        if post is not None and engage and not serving_post:
            ok = False
            gate = {
                "kind": VIOLATION_ENGAGE_NO_X64_SERVER,
                "runner": "<pool>",
                "why": (
                    f"engage attached {FALLBACK_LABEL!r} and NOT ONE online x64 runner serves it after "
                    f"the write ({len(post_runners)} runner(s) read back) — the reconcile delivered no "
                    f"capacity, however clean the label state looks."
                ),
            }
            # ⛔ `verdict` IS the field every other reader keys on (`ok` is never printed in the
            # human render), so leaving it "OK" here makes the payload contradict itself. But a
            # READ FAILURE outranks a capacity reading: asserting a measured defect about a pool
            # nobody measured inverts the doctrine this file states — "I could not look must
            # never be the last word". So the gate's kind wins only over a real verdict.
            already_unmeasured = post["verdict"] == VIOLATION_UNMEASURED_EMPTY_POOL
            if already_unmeasured:
                # ⛔ A READ FAILURE IS NOT A CAPACITY READING. Appending the gate's violation (and
                # printing its `❌`) asserted a measured capacity defect about a pool nobody read,
                # while the comment above claimed the opposite. Keep the read failure, keep
                # `ok: false`, and say nothing about capacity.
                print(
                    f"❌ {VIOLATION_UNMEASURED_EMPTY_POOL}: the read-back returned no runners — "
                    f"capacity is UNMEASURED, so this reconcile is not verified.",
                    file=sys.stderr,
                )
            else:
                post = {
                    **post,
                    "ok": False,
                    "verdict": VIOLATION_ENGAGE_NO_X64_SERVER,
                    "violations": [*post["violations"], gate],
                }
                print(
                    f"❌ {VIOLATION_ENGAGE_NO_X64_SERVER}: no online x64 runner serves "
                    f"{FALLBACK_LABEL!r} after the write — engage delivered NO capacity.",
                    file=sys.stderr,
                )

    payload = {
        "ok": ok,
        "ok_before": report["ok"],
        "dry_run": args.dry_run,
        "engage": engage,
        "changes": records,
        "unclassified": dict(pending.unclassified),
        "post": post,
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
            print(
                f"post-state verdict: {post['verdict']} "
                f"(x64 serving={x64_serving}, non-x64 serving={others_serving} — read back after the write)"
            )
    return 0 if args.dry_run or ok else 1


if __name__ == "__main__":
    sys.exit(main())
