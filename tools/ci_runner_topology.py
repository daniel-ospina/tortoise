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
`FALLBACK_LABEL` to the self-hosted pool while GitHub-hosted is unhealthy and
strips it otherwise. Two measured facts make the current state residue rather
than intent:

  * the gate's log stops at 2026-11-01T16:42Z (6 days stale at measurement) and
    its state file still reads `mode=github` — "strip the label" — while three
    runners still carry it;
  * its own header names this exact failure at `:926`: "a capped disarm would
    strand `ubuntu-latest` on the runners outside the arm".

So the gate cannot currently reconcile, and the topology it left behind is the
bad one.

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
                        Nothing carries the fallback while `ubuntu-latest`
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
    uv run python tools/ci_runner_topology.py apply            # reconcile to the desired topology
    uv run python tools/ci_runner_topology.py check --queued-floor-seconds 1800

`apply` needs `administration: write` on the token — `GITHUB_TOKEN` inside
Actions does NOT have it, so the reconcile is operator/hub-run (the same token
class `scripts/ci_runner_gate.sh` uses). The WORKFLOW is check-only on purpose:
a tripwire that goes red, never a mutation nobody reviewed.
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

#: Labels GitHub itself owns on a self-hosted runner; `apply` never rewrites them.
GITHUB_OWNED = frozenset({"self-hosted", "Linux", "macOS", "Windows", "X64", "ARM64", "ARM"})

VIOLATION_FALLBACK_ON_NON_X64 = "FALLBACK_ON_NON_X64"
VIOLATION_NO_X64_FALLBACK = "NO_X64_FALLBACK"

#: "I could not look" must never be the last word (the doctrine `ci-queue-health.yml`
#: states): an empty enumeration is a READ FAILURE, not an absence of violations.
VIOLATION_UNMEASURED_EMPTY_POOL = "UNMEASURED_EMPTY_POOL"


@dataclass(frozen=True)
class Runner:
    """One registered self-hosted runner, as the API returns it."""

    id: int
    name: str
    status: str
    busy: bool
    labels: frozenset[str]

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

    def add(self, runner: Runner, desired: Sequence[str]) -> None:
        before = sorted(runner.labels)
        after = sorted(set(desired))
        if before != after:
            self.changes[runner.name] = (runner.id, before, after)

    def __bool__(self) -> bool:
        return bool(self.changes)


def parse_runner(payload: Mapping[str, Any]) -> Runner:
    """Build a Runner from one element of `.runners` (or a test fixture)."""
    return Runner(
        id=int(payload["id"]),
        name=str(payload["name"]),
        status=str(payload.get("status", "unknown")),
        busy=bool(payload.get("busy", False)),
        labels=frozenset(str(label["name"]) for label in payload.get("labels", [])),
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

    # 1. The #6897 residual: a non-x64 runner carrying an x86_64 image's label.
    for runner in sorted(runners, key=lambda r: r.name):
        if runner.serves_fallback and runner.host != HOST_LABEL_X64:
            found.append(
                Violation(
                    kind=VIOLATION_FALLBACK_ON_NON_X64,
                    runner=runner.name,
                    why=(
                        f"carries {FALLBACK_LABEL!r} but its host label is "
                        f"{runner.host or 'neither X64 nor ARM64'}; an x64-expected job routed "
                        f"here is silently mis-served (#6897). REMEDY: strip {FALLBACK_LABEL!r} "
                        f"from this runner."
                    ),
                )
            )

    # 2. The fallback is engaged somewhere, but never where it can serve x64 work.
    # `online`: an OFFLINE runner that carries the label serves nothing, so counting it
    # here would let `census` (which excludes offline capacity) and this check disagree
    # about the same question — one reporting no usable capacity, the other "engaged".
    serving = [r for r in runners if r.serves_fallback and r.online]
    if serving and not any(r.host == HOST_LABEL_X64 for r in serving):
        found.append(
            Violation(
                kind=VIOLATION_NO_X64_FALLBACK,
                runner="<pool>",
                why=(
                    f"{len(serving)} runner(s) carry {FALLBACK_LABEL!r} and NOT ONE of them is "
                    f"x64, while {sum(1 for r in runners if r.host == HOST_LABEL_X64)} x64 "
                    f"runner(s) are registered. Every `{FALLBACK_LABEL}` job that does not reach "
                    f"GitHub-hosted therefore queues or is mis-served. REMEDY: attach "
                    f"{FALLBACK_LABEL!r} to the x64 pool."
                ),
            )
        )

    return found


def desired_label_set(runner: Runner, *, engage: bool) -> list[str]:
    """The label set this runner SHOULD carry, under the x64-only invariant.

    Preserves every label the tool does not own (a human's or another tool's), so
    `apply` is a reconcile and not a rewrite. GitHub-owned labels are recomputed
    from the host, because the pool marker must match the host it is stamped on
    (`ci_runner_gate.sh`: "the marker is the ARCHITECTURE the runner IS ... the
    `ARM64` / `X64` label GitHub stamps from the host at registration").
    """
    host = runner.host
    marker = POOL_LABEL_X64 if host == HOST_LABEL_X64 else POOL_LABEL_ARM64 if host == HOST_LABEL_ARM64 else None
    out = {label for label in runner.labels if label not in GITHUB_OWNED and label != FALLBACK_LABEL}
    out.update({"self-hosted", "Linux"})
    if host:
        out.add(host)
    if marker:
        out.add(marker)
    # ⛔ x64 ONLY. An ARM64 runner never carries the hosted x86_64 fallback: that
    # is the whole point of this file, and it is what #10912's "preferred" remedy
    # asks for. Widening this to `if engage` alone would re-open #6897.
    if engage and host == HOST_LABEL_X64:
        out.add(FALLBACK_LABEL)
    return sorted(out)


def plan(runners: Sequence[Runner], *, engage: bool) -> Plan:
    """The reconcile. Idempotent: an already-correct pool yields an empty Plan."""
    result = Plan()
    for runner in sorted(runners, key=lambda r: r.name):
        result.add(runner, desired_label_set(runner, engage=engage))
    return result


def check(
    runners: Sequence[Runner],
    *,
    queued_oldest_seconds: int | None = None,
    queued_floor_seconds: int = 1800,
    engage: bool = False,
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
            "engage": engage,
        }
    found = violations(runners)
    pending = plan(runners, engage=engage)
    starving = (
        queued_oldest_seconds is not None
        and queued_oldest_seconds >= queued_floor_seconds
        and not any(r.serves_fallback for r in runners)
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
            # ⛔ The PUT body may carry CUSTOM labels only. `self-hosted`, `Linux`,
            # `X64`/`ARM64` are stamped by GitHub at registration and are READ-ONLY:
            # sending them back is a 422 ("Cannot set labels that duplicate existing
            # read-only labels: Linux, X64, self-hosted") that leaves the runner
            # untouched — which is why the plan above is stated over the FULL set
            # (it must compare the host labels too) while the write is filtered.
            # Same split as `ci_runner_gate.sh`'s own write path.
            body = json.dumps({"labels": [label for label in after if label not in GITHUB_OWNED]})
            proc = subprocess.run(
                ["gh", "api", "-X", "PUT", f"repos/{repo}/actions/runners/{runner_id}/labels", "--input", "-"],
                input=body,
                capture_output=True,
                text=True,
            )
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
        print("  NOTE: demand is queued past the floor while the fallback is ENGAGED NOWHERE — "
              "idle-pool topology with an absorbing queue (#10912). Not a violation.")
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
    parser.add_argument("--engage", action="store_true", help="desired topology attaches the fallback to x64")
    parser.add_argument("--dry-run", action="store_true", help="apply: print the changes, write nothing")
    parser.add_argument("--queued-floor-seconds", type=int, default=1800)
    parser.add_argument("--from-fixture", type=Path, help="read a runners JSON payload instead of the API")
    args = parser.parse_args(argv)

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
        engage=args.engage,
    )

    if args.verb == "check":
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            _print_human(report)
        return 0 if report["ok"] else 1

    pending = plan(runners, engage=args.engage)
    records = apply_plan(pending, dry_run=args.dry_run, repo=args.repo)
    payload = {"ok": report["ok"], "dry_run": args.dry_run, "engage": args.engage, "changes": records}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{'would change' if args.dry_run else 'changed'}: {len(records)} runner(s)")
        for rec in records:
            state = "planned" if args.dry_run else ("applied" if rec["applied"] else f"FAILED {rec.get('error','')}")
            print(f"  {rec['runner']}: {rec['before']} -> {rec['after']}  [{state}]")
        if not records:
            print("  (already at the desired topology — idempotent, nothing to do)")
    if any(not rec["applied"] for rec in records) and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
