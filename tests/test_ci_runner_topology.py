"""The runner-label topology invariant (#10912), pinned against the MEASURED pool.

These tests exist because the defect they catch is invisible to every other
instrument in the repo: a job does not fail when it lands on a mis-labelled
runner, it lands (and is silently mis-served, #6897) or it queues for days.

The fixture `MEASURED_2026_11_07` is the real topology read from
`repos/daniel-ospina/tortoise/actions/runners` at 2026-11-07T16:22-16:30Z, with
the runner names and the label sets as the API returned them. A test that used a
synthetic 2-runner pool would pass on the real one's defect and prove nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "tools" / "ci_runner_topology.py"
sys.path.insert(0, str(TOOL.parent))

import ci_runner_topology as topo  # noqa: E402


def _runner(rid: int, name: str, labels: list[str], *, busy: bool = False, status: str = "online",
            read_only: frozenset[str] | None = None) -> dict:
    read_only = frozenset(topo.GITHUB_OWNED) if read_only is None else read_only
    return {
        "id": rid,
        "name": name,
        "status": status,
        "busy": busy,
        # The API carries `type` on every label; hand-built fixtures mirror that so the
        # read-only/custom split is exercised the same way a real read exercises it.
        "labels": [
            {"name": label, "type": topo.LABEL_TYPE_READ_ONLY if label in read_only else "custom"}
            for label in labels
        ],
    }


def _post_pool(desired: topo.Plan) -> list[topo.Runner]:
    """Apply a plan the way `apply` does: the planned CUSTOM labels, read-only labels kept."""
    out: list[topo.Runner] = []
    for runner in measured():
        planned = desired.changes.get(runner.name)
        custom = planned[2] if planned else sorted(runner.custom_labels)
        out.append(
            topo.parse_runner(
                _runner(
                    runner.id,
                    runner.name,
                    sorted(runner.read_only | set(custom)),
                    busy=runner.busy,
                    status=runner.status,
                    read_only=runner.read_only,
                )
            )
        )
    return out


X64 = ["self-hosted", "Linux", "X64", "self-hosted-x64"]
ARM64_PLAIN = ["self-hosted", "Linux", "ARM64", "self-hosted-arm64"]
ARM64_FALLBACK = ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", "ubuntu-latest"]

#: The measured pool: 16 x64 (15 online+idle, 1 offline, none carrying
#: `ubuntu-latest`), 3 ARM64 carrying it (all busy), 2 ARM64 plain.
#: 21 runners total.
MEASURED_2026_11_07 = {
    "total_count": 21,
    "runners": [
        _runner(1, "cmux", X64),
        _runner(2, "pi-runner-c5-x64-1", X64),
        _runner(3, "pi-runner-c5-x64-2", X64),
        _runner(4, "pi-runner-c5-x64-3", X64),
        _runner(5, "pi-runner-c5-x64-4", X64),
        _runner(6, "pi-runner-cell3-x64-1", X64),
        _runner(7, "pi-runner-vm-2", ARM64_FALLBACK, busy=True),
        _runner(8, "pi-runner-vm-3", ARM64_PLAIN),
        _runner(9, "pi-runner-cmux-x64-5", X64),
        _runner(10, "pi-runner-cmux-x64-6", X64),
        _runner(11, "pi-runner-cmux-x64-7", X64),
        _runner(12, "pi-runner-cmux-x64-8", X64),
        _runner(13, "pi-runner-cell3-x64-9", X64, status="offline"),
        _runner(14, "pi-runner-cell4-x64-1", X64),
        _runner(15, "pi-runner-vm-5", ARM64_FALLBACK, busy=True),
        _runner(16, "pi-runner-vm-6", ARM64_FALLBACK, busy=True),
        _runner(17, "pi-runner-vm-4", ARM64_PLAIN),
        _runner(18, "pi-runner-x64-1", X64),
        _runner(19, "pi-runner-x64-3", X64),
        _runner(20, "pi-runner-x64-4", X64),
        _runner(21, "pi-runner-x64-2", X64),
    ],
}


def measured() -> list[topo.Runner]:
    return topo.parse_runners(MEASURED_2026_11_07)


# --------------------------------------------------------------------- the defect
def test_the_measured_pool_is_a_violation_and_every_arm64_fallback_runner_is_named():
    """RED on the real pool: `ubuntu-latest` sits on 3 ARM64 runners, 0 x64 ones.

    Both arms must fire, and they are different defects with different remedies:
    the label must come OFF the ARM64 VMs (#6897 mis-serve) and ON to the x64
    pool (capacity that 43 of 47 queued jobs could use).
    """
    found = topo.violations(measured())
    kinds: dict[str, list[str]] = {}
    for violation in found:
        kinds.setdefault(violation.kind, []).append(violation.runner)

    assert kinds[topo.VIOLATION_FALLBACK_ON_NON_X64] == [
        "pi-runner-vm-2",
        "pi-runner-vm-5",
        "pi-runner-vm-6",
    ], "every non-x64 runner carrying the hosted fallback label must be named, deterministically ordered"
    assert kinds[topo.VIOLATION_NO_X64_FALLBACK] == ["<pool>"], (
        "the fallback is engaged on 3 runners and none of them is x64 — the demand/supply join of "
        "#10912 must be a named violation, not a silent count"
    )


def test_the_measured_pool_reports_15_idle_x64_runners_serving_a_label_nothing_requests():
    """The capacity half of #10912 — measured, not asserted from the issue text."""
    report = topo.census(measured())
    assert report["runners"] == 21, "the pool has 21 registered runners (the gate's `runners=12` is stale)"
    assert report["by_host"]["X64"]["total"] == 16
    assert report["by_host"]["ARM64"]["serving_fallback"] == 3
    assert len(report["x64_idle"]) == 15, (
        "at least 13 x64 runners are online and idle — the capacity the queue cannot reach"
    )
    assert report["serving_fallback"] == ["pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"]


# ---------------------------------------------------------------- the produced topology
def test_the_planned_topology_puts_the_fallback_on_x64_only():
    """The desired topology: `ubuntu-latest` on the x64 pool ONLY (#10912's "preferred")."""
    desired = topo.plan(measured(), engage=True)
    assert desired.changes, "the measured pool is not at the desired topology — an empty plan would make this vacuous"
    for name, (_rid, _before, after) in desired.changes.items():
        runner = next(r for r in measured() if r.name == name)
        if runner.host == topo.HOST_LABEL_X64:
            assert topo.FALLBACK_LABEL in after, f"{name} is x64 and must be able to serve {topo.FALLBACK_LABEL!r}"
        else:
            assert topo.FALLBACK_LABEL not in after, (
                f"{name} is {runner.host} and must NEVER carry the hosted x86_64 fallback (#6897)"
            )


def test_applying_the_plan_satisfies_the_invariant():
    """Round-trip: the produced topology passes `check` — the fix is a fix, not a mutation."""
    desired = topo.plan(measured(), engage=True)
    post = _post_pool(desired)
    assert topo.violations(post) == []
    assert topo.check(post)["ok"] is True
    assert topo.check(post)["census"]["by_host"]["X64"]["serving_fallback"] == 16


def test_the_plan_is_idempotent():
    """A reconcile that keeps changing things on a correct pool is an outage waiting to happen."""
    post = _post_pool(topo.plan(measured(), engage=True))
    again = topo.plan(post, engage=True)
    assert again.changes == {}
    assert again.unclassified == {}, "the measured pool is all Linux x64/ARM64 — nothing may be unclassified"


def test_a_foreign_label_is_preserved_and_github_owned_labels_are_recomputed():
    """`apply` reconciles; it must not delete a label another tool or a human added."""
    runner = topo.parse_runner(
        _runner(99, "pi-runner-x64-9", [*X64, "gpu", "big-disk"], busy=False)
    )
    desired = topo.desired_label_set(runner, engage=True)
    assert "gpu" in desired and "big-disk" in desired, "a label this tool does not own must survive"
    assert topo.FALLBACK_LABEL in desired


def test_an_arm64_runner_never_gets_the_fallback_even_when_engaged():
    """The #6897 pin, stated directly: engage is about x64 capacity, not about every runner."""
    runner = topo.parse_runner(_runner(98, "pi-runner-vm-7", ARM64_PLAIN))
    assert topo.FALLBACK_LABEL not in topo.desired_label_set(runner, engage=True)
    assert topo.FALLBACK_LABEL not in topo.desired_label_set(runner, engage=False)


# ------------------------------------------------------------------- not a red for design
def test_an_idle_pool_with_a_healthy_hosted_is_not_a_violation():
    """`mode=github` (strip the fallback) is a legitimate topology and must never red.

    The tool reports the absorbing queue as `starvation_suspected` and stays
    exit-0: reding the design decision this tool does not govern would make the
    tripwire noise, and noise is how a tripwire gets deleted.
    """
    stripped = [_runner(row_id, name, [lbl for lbl in labels if lbl != topo.FALLBACK_LABEL])
                for row_id, name, labels in
                ((r["id"], r["name"], [lbl["name"] for lbl in r["labels"]]) for r in MEASURED_2026_11_07["runners"])]
    report = topo.check(topo.parse_runners({"runners": stripped}), queued_oldest_seconds=7372)
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["starvation_suspected"] is True, (
        "an idle pool with a queue older than the floor is the #10912 state and must be REPORTED"
    )


def test_a_queued_run_under_the_floor_is_not_reported_as_starvation():
    stripped = [
        _runner(r["id"], r["name"], [lbl["name"] for lbl in r["labels"] if lbl["name"] != topo.FALLBACK_LABEL])
        for r in MEASURED_2026_11_07["runners"]
    ]
    report = topo.check(topo.parse_runners({"runners": stripped}), queued_oldest_seconds=60)
    assert report["ok"] is True
    assert report["starvation_suspected"] is False


# ------------------------------------------------------------------------------- the CLI
def test_cli_check_exits_1_on_the_measured_pool_and_names_the_runners(tmp_path: Path):
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, f"a violation must be a non-zero exit; got {proc.returncode}: {proc.stdout}"
    for name in ("pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"):
        assert name in proc.stdout, f"{name} carries the x86_64 fallback label on ARM64 and must be named"


def test_cli_check_exits_0_on_a_conforming_pool(tmp_path: Path):
    conforming = {
        "runners": [
            _runner(r["id"], r["name"], [lbl["name"] for lbl in r["labels"]]) for r in MEASURED_2026_11_07["runners"]
        ]
    }
    # Strip the fallback from the ARM64 trio first: that is the "disengaged" state.
    for row in conforming["runners"]:
        row["labels"] = [lbl for lbl in row["labels"] if lbl["name"] != topo.FALLBACK_LABEL]
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(conforming))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"a disengaged pool is not a violation: {proc.stdout}"


def test_cli_apply_dry_run_writes_nothing(tmp_path: Path):
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--engage", "--from-fixture", str(fixture), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert payload["changes"], "the measured pool is not at the desired topology, so a plan must exist"
    assert all(record["applied"] is False for record in payload["changes"]), "a dry run must apply nothing"
    # 3 ARM64 strip + 16 x64 attach = 19 runners change (no x64 runner carries the label yet).
    assert len(payload["changes"]) == 19


# ------------------------------------------------- the direction is never implicit
def test_bare_apply_engages_by_default_and_never_strips_the_only_capacity(tmp_path: Path):
    """REGRESSION (#10912 review P1): a bare `apply` used to strip the fallback off the only
    runners carrying it and attach it nowhere, exiting 0 — a verb named "apply" that quietly
    removed every runner answering `ubuntu-latest`, with output that reads like success.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--from-fixture", str(fixture), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["engage"] is True, "the default direction must be the one that ADDS capacity"
    after = {record["runner"]: record["after"] for record in payload["changes"]}
    assert topo.FALLBACK_LABEL in after["pi-runner-x64-1"], (
        "a bare apply must attach the fallback to the x64 pool, not merely strip it elsewhere"
    )


def test_disengage_is_explicit_and_removes_the_fallback_everywhere(tmp_path: Path):
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--disengage", "--from-fixture", str(fixture), "--json"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["engage"] is False
    for record in payload["changes"]:
        assert topo.FALLBACK_LABEL not in record["after"], (
            f"{record['runner']} must not carry {topo.FALLBACK_LABEL!r} when disengaged"
        )


def test_engage_and_disengage_are_mutually_exclusive(tmp_path: Path):
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--engage", "--disengage",
         "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, f"two opposite directions must be a usage error: {proc.stdout}{proc.stderr}"


# ------------------------------------------- a fixture's ids are not this repo's runners
def test_apply_from_fixture_refuses_to_write(tmp_path: Path):
    """REGRESSION (#10912 review P2): the measured fixture carries the REAL production ids
    (1-21), so `apply --from-fixture` without `--dry-run` would PUT label sets onto whichever
    runners hold those ids. It must refuse, not write.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 2, f"a write from a fixture must be refused: {proc.stdout}{proc.stderr}"
    assert "--dry-run" in proc.stderr, "the refusal must name the flag that makes it safe"


# ------------------------------------------------- read-only labels come from the API
def test_read_only_split_comes_from_the_api_type_not_a_name_list():
    """REGRESSION (#10912 review P2): `desired_label_set` invented `Linux` (a read-only label a
    write may never send), so a non-Linux runner's plan `after` was unreachable — a diff no write
    could close, reported as a change on every run forever.
    """
    payload = {
        "id": 7,
        "name": "pi-runner-mac-1",
        "status": "online",
        "busy": False,
        "labels": [
            {"name": "self-hosted", "type": "read-only"},
            {"name": "macOS", "type": "read-only"},
            {"name": "X64", "type": "read-only"},
            {"name": "xcode-16", "type": "custom"},
        ],
    }
    runner = topo.parse_runner(payload)
    assert runner.read_only == {"self-hosted", "macOS", "X64"}
    assert runner.custom_labels == {"xcode-16"}
    desired = topo.desired_label_set(runner, engage=True)
    assert "Linux" not in desired, "a label this tool does not own and cannot write must never be planned"
    assert topo.FALLBACK_LABEL not in desired, "a macOS runner is not a member of the Linux pool"
    assert desired == ["xcode-16"], "an unmanaged runner's custom set is left exactly as it is"


def test_a_runner_outside_the_pool_is_named_and_left_unchanged():
    mac = topo.parse_runner(
        {
            "id": 101,
            "name": "pi-runner-mac-1",
            "status": "online",
            "busy": False,
            "labels": [
                {"name": "self-hosted", "type": "read-only"},
                {"name": "macOS", "type": "read-only"},
                {"name": "X64", "type": "read-only"},
            ],
        }
    )
    pending = topo.plan([*measured(), mac], engage=True)
    assert "pi-runner-mac-1" not in pending.changes, "a runner outside the pool must never be rewritten"
    assert "pi-runner-mac-1" in pending.unclassified, "it must be REPORTED, not silently dropped"
    assert topo.desired_label_set(mac, engage=True) == []


# ------------------------------------------------------- the write is read back
def test_apply_reports_the_post_state_not_the_pre_state(monkeypatch, capsys):
    """REGRESSION (#10912 review P2): `apply` trusted `rc == 0` and reported the verdict it had
    computed BEFORE the write, so a PUT that the API accepted but that did not take (or that
    `ci_runner_gate.sh` reversed) read as a successful reconcile.
    """
    pre = measured()
    post = _post_pool(topo.plan(pre, engage=True))
    reads = [pre, post]
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: reads.pop(0))
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": name, "id": rid, "before": before, "after": after, "applied": True}
            for name, (rid, before, after) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0, payload
    assert payload["ok_before"] is False, "the pre-state is the measured violation"
    assert payload["post"] is not None, "the run must READ THE POOL BACK after writing"
    assert payload["post"]["verdict"] == "OK", "the reported state must be the POST state"
    assert payload["post"]["census"]["by_host"]["X64"]["serving_fallback"] == 16
    assert payload["ok"] is True


def test_a_failed_write_is_not_a_successful_reconcile(monkeypatch, capsys):
    pre = measured()
    reads = [pre, pre]  # the write did not take: the read-back is still the violation
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: reads.pop(0))
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": name, "id": rid, "before": before, "after": after, "applied": False,
             "error": "422 read-only label"}
            for name, (rid, before, after) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, "a write that did not take must not exit 0"
    assert payload["ok"] is False
    assert payload["post"]["verdict"] == "VIOLATION", "the read-back must show the failure, not hide it"
