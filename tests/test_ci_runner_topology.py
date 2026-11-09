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

import datetime
import json
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

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


@contextmanager
def tempfile_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


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
    """Round-trip: the produced topology passes `check` — the fix is a fix, not a mutation.

    ⛔ `strip_non_x64=True` is REQUIRED for the round trip to close, and that is the point: the
    ARM64 half of the topology is owner-gated (#6897/#9878), so a bare `engage` plan cannot
    produce a clean pool and does not pretend to. The remaining violation is visible, not
    silent — see the companion test below.
    """
    desired = topo.plan(measured(), engage=True, strip_non_x64=True)
    post = _post_pool(desired)
    assert topo.violations(post) == []
    assert topo.check(post, strip_non_x64=True)["ok"] is True
    # ⛔ THE ARM IS CAPPED, SO "THE POOL SERVES" MEANS THE ARM, NOT ALL 16. `FALLBACK_ARM_MAX=3`
    # (OWNER RULING 2026-11-02, #9878) bounds an engaging pass; this tool now reads that cap
    # instead of arming the whole pool. The widened arm is expressible, and it is a DECISION —
    # `arm_cap=16` here is the explicit form, not a default.
    assert topo.check(post, strip_non_x64=True)["census"]["by_host"]["X64"]["serving_fallback"] == topo.ARM_CAP_DEFAULT
    # 15, not 16: `pi-runner-cell3-x64-9` (id 13) is OFFLINE in the measured fixture, and an
    # offline runner is not an arm slot.
    wide = topo.plan(measured(), engage=True, strip_non_x64=True, arm_cap=16)
    assert len(wide.armed) == 15, "an explicit wider cap must be honoured — the bound is read, not hardcoded"
    assert topo.check(_post_pool(wide), strip_non_x64=True)["census"]["by_host"]["X64"]["serving_fallback"] == 15


def test_a_bare_engage_never_strips_a_non_x64_carrier_and_says_so():
    """REGRESSION (#10912 review P1): the owner-gated ARCH strip was the DEFAULT.

    Removing `ubuntu-latest` from a non-x64 runner is the step #6897/#9878 reserve
    (`tools/ci_runner_labels.py` R2: "removing the label is the step that can strand a job, and
    #6897/#9878 own that decision"), and this PR's own body records it as "not adoptable
    without a reopen". A bare `apply` performed it anyway. Now it is reported as owner-gated and
    performed only when the operator names it.
    """
    desired = topo.plan(measured(), engage=True)
    post = _post_pool(desired)
    for name in ("pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"):
        assert name not in desired.changes, f"{name} carries the fallback on ARM64 and must be left alone"
        assert name in desired.owner_gated, f"the skipped strip for {name} must be REPORTED, never silent"
        assert topo.FALLBACK_LABEL in next(r for r in post if r.name == name).labels
    # The additive half still happens, and the un-healed defect is still a named violation.
    assert desired.armed == ["cmux", "pi-runner-c5-x64-1", "pi-runner-c5-x64-2"], (
        "the engaging arm is the first FALLBACK_ARM_MAX managed ids — the gate's own order"
    )
    assert set(desired.changes) == set(desired.armed), "a bare engage attaches exactly the arm"
    assert {v.kind for v in topo.violations(post)} == {topo.VIOLATION_FALLBACK_ON_NON_X64}
    # …and the explicit flag closes it, so the owner-gated half is reachable, not dropped.
    stripped = topo.plan(measured(), engage=True, strip_non_x64=True)
    assert set(stripped.changes) == set(desired.changes) | {"pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"}
    assert stripped.owner_gated == {}


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
    assert proc.returncode == 1, (
        "a bare apply CANNOT reach a clean pool: the ARM64 strip is owner-gated, so the remaining "
        "FALLBACK_ON_NON_X64 is reported and the exit code says so rather than reading as success"
    )
    payload = json.loads(proc.stdout)
    assert payload["dry_run"] is True
    assert payload["changes"], "the measured pool is not at the desired topology, so a plan must exist"
    assert all(record["applied"] is False for record in payload["changes"]), "a dry run must apply nothing"
    # The arm attaches FALLBACK_ARM_MAX x64 runners ONLY: beyond it the runners are `over_cap`
    # (reported, never written), and the 3 ARM64 carriers are owner-gated and left byte-identical.
    assert len(payload["changes"]) == topo.ARM_CAP_DEFAULT
    assert payload["arm"] == [record["runner"] for record in payload["changes"]]
    assert len(payload["over_cap"]) == 12, "the x64 runners the cap withheld are REPORTED, not silent"
    assert set(payload["owner_gated"]) == {"pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"}
    # ⛔ THE PRE-FLIGHT MUST SPEAK BEFORE THE WRITE DOES. The projection is judged by the same
    # gates, so the dry run reports the violation the apply would leave behind.
    assert payload["post_is_projection"] is True
    assert payload["post"]["verdict"] == topo.VIOLATION_FALLBACK_ON_NON_X64 or payload["post"]["ok"] is False


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
    payload = json.loads(proc.stdout)
    assert proc.returncode == 1, (
        "the owner-gated ARM64 carrier is still a named violation after a bare engage, so a "
        "success exit code would be a false PASS"
    )
    assert payload["engage"] is True, "the default direction must be the one that ADDS capacity"
    after = {record["runner"]: record["after"] for record in payload["changes"]}
    assert payload["arm"], "a bare apply must ARM the x64 pool — an empty arm adds no capacity"
    assert all(topo.FALLBACK_LABEL in after[name] for name in payload["arm"]), (
        "a bare apply must attach the fallback to every runner it arms, not merely strip it elsewhere"
    )
    # ⛔ AND IT MUST NOT STRIP THE ARM64 CARRIERS. Their absence from `changes` is the fix; the
    # `owner_gated` entry is what keeps that skip VISIBLE instead of silent.
    assert set(payload["owner_gated"]) == {"pi-runner-vm-2", "pi-runner-vm-5", "pi-runner-vm-6"}


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
    # ⛔ DISCRIMINATING: exit 2 alone proves nothing — with `--disengage` absent entirely (the fix
    # reverted) argparse also exits 2, on "unrecognized arguments". Only a MUTUALLY EXCLUSIVE group
    # produces this wording.
    assert "not allowed with argument" in proc.stderr, (
        f"the two directions must be a mutually exclusive group, not merely rejected: {proc.stderr}"
    )


# ------------------------------------------- a fixture's ids are not this repo's runners
def test_apply_from_fixture_refuses_to_write(tmp_path: Path, monkeypatch, capsys):
    """REGRESSION (#10912 review P2): the measured fixture carries the REAL production ids
    (1-21), so `apply --from-fixture` without `--dry-run` would PUT label sets onto whichever
    runners hold those ids. It must refuse, not write.

    ⛔ RUN IN-PROCESS WITH `apply_plan` TRIPPED, NEVER AS A SUBPROCESS. A subprocess test of this
    guard is fail-DANGEROUS: if the guard regresses, the test does not merely go red — it executes
    19 real `PUT .../actions/runners/{1..21}/labels` against production before returning. Here a
    regression is red with zero network.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    calls: list[object] = []
    monkeypatch.setattr(topo, "apply_plan", lambda *a, **kw: calls.append((a, kw)) or [])
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: pytest.fail("must not read the pool"))
    rc = topo.main(["apply", "--from-fixture", str(fixture)])
    captured = capsys.readouterr()
    assert rc == 2, f"a write from a fixture must be refused: {captured.out}{captured.err}"
    assert calls == [], "the refusal must happen BEFORE any write is attempted"
    assert "--dry-run" in captured.err, "the refusal must name the flag that makes it safe"


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
    post = _post_pool(topo.plan(pre, engage=True, strip_non_x64=True))
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
    assert payload["post"]["census"]["by_host"]["X64"]["serving_fallback"] == topo.ARM_CAP_DEFAULT, (
        "the POST render counts the CAPPED arm the write produced"
    )
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
    assert payload["post"]["verdict"] != "OK", "the read-back must show the failure, not hide it"


# ------------------------------------- a Windows x64 box is a mis-serve, not a no-op
def test_a_non_linux_runner_carrying_ubuntu_latest_is_a_violation():
    """REGRESSION (#10912 review P2): `violations()` read the ARCH only, so a Windows/X64 runner
    carrying `ubuntu-latest` was neither a violation nor reconcilable — it was skipped, and the
    tool reported "0 changes, already at the desired topology" while that box kept accepting
    Linux jobs. `ubuntu-latest` promises an x86_64 UBUNTU image; platform is part of the promise.
    """
    windows = topo.parse_runner(
        {
            "id": 55,
            "name": "pi-runner-win-1",
            "status": "online",
            "busy": False,
            "labels": [
                {"name": "self-hosted", "type": "read-only"},
                {"name": "Windows", "type": "read-only"},
                {"name": "X64", "type": "read-only"},
                {"name": "ubuntu-latest", "type": "custom"},
            ],
        }
    )
    found = topo.violations([windows])
    kinds = [v.kind for v in found]
    # BOTH, deliberately: the carrier must be stripped (rule 1) AND the label must reach the x64
    # pool (rule 2) — a Windows carrier satisfies neither the image promise nor the capacity, so
    # reporting only rule 1 would leave the pool armed nowhere with the wrong remedy named.
    assert kinds == [topo.VIOLATION_FALLBACK_ON_NON_X64, topo.VIOLATION_NO_X64_FALLBACK], kinds
    assert "platform is not Linux" in found[0].why
    assert topo.unmanaged_reason(windows) is not None, "and the tool must not silently relabel it either"


# --------------------------------------------- engage must deliver capacity, not labels
def test_an_engage_that_cannot_reach_an_online_x64_runner_is_not_a_success(monkeypatch, capsys):
    """REGRESSION (#10912 review P1): on a pool with no x64 arm (or a wholly offline one) the
    ENGAGE plan strips `ubuntu-latest` off the only carriers and attaches it where nobody online
    can serve it. The writes land, the read-back is clean — `check` cannot red, because "engaged
    nowhere" is a legitimate mode=github state — and `apply` reported `ok: true`, exit 0, having
    removed the only runners answering the label. Engage promises CAPACITY; it must fail closed.
    """
    arm_only = topo.parse_runners(
        {
            "runners": [
                _runner(1, "pi-runner-vm-2", ARM64_FALLBACK, busy=True),
                _runner(2, "pi-runner-vm-5", ARM64_FALLBACK, busy=True),
            ]
        }
    )
    offline_x64 = topo.parse_runners(
        {
            "runners": [
                _runner(1, "pi-runner-vm-2", ARM64_FALLBACK, busy=True),
                _runner(3, "pi-runner-x64-1", X64, status="offline"),
            ]
        }
    )
    # THE CASE THE GATE'S `online` PREDICATE EXISTS FOR, and the one the other two pools cannot
    # reach: the label IS attached to x64 — but only to an OFFLINE one, so `check` reports no
    # violation at all (violation #2 is online-filtered) and the gate is the ONLY signal.
    armed_but_offline = topo.parse_runners(
        {
            "runners": [
                _runner(1, "pi-runner-vm-2", ARM64_PLAIN),
                _runner(3, "pi-runner-x64-1", [*X64, topo.FALLBACK_LABEL], status="offline"),
            ]
        }
    )
    # RULE 2 also fires here (a carrier exists, none can serve it) — which is the correct reading
    # after the predicate widening: `check` now reports this pool, where before it said nothing at
    # all. The engage gate is still what separates "armed but dead" from "delivered capacity".
    for pool in (arm_only, offline_x64, armed_but_offline):
        reads = [pool, pool]  # the write takes, and the pool still cannot serve
        monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO, _r=reads: _r.pop(0))
        monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
        monkeypatch.setattr(
            topo,
            "apply_plan",
            lambda pending, *, dry_run, repo=topo.REPO: [
                {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
                for n, (rid, b, a) in pending.changes.items()
            ],
        )
        rc = topo.main(["apply", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert rc == 1, f"engage with no online x64 server must not exit 0: {payload}"
        assert payload["ok"] is False
        kinds = [v["kind"] for v in payload["post"]["violations"]]
        assert topo.VIOLATION_ENGAGE_NO_X64_SERVER in kinds, kinds
        assert payload["post"]["verdict"] == topo.VIOLATION_ENGAGE_NO_X64_SERVER, (
            "the payload must not say verdict=OK while ok=false — that is the same false PASS in the "
            "field every other reader keys on"
        )


# ------------------------------------------------- the PUT body is what the plan said
def test_the_put_body_is_the_planned_custom_set_and_never_a_read_only_label(monkeypatch):
    """REGRESSION (#10912 review P2): nothing asserted the wire body. Every apply test either
    passed `--dry-run` (body never built) or stubbed `apply_plan` out — so the one artefact that
    decides whether a write lands or 422s ("Cannot set labels that duplicate existing read-only
    labels: Linux, X64, self-hosted") had ZERO coverage.
    """
    runner = topo.parse_runner(_runner(42, "pi-runner-x64-1", [*X64, "gpu"]))
    pending = topo.plan([runner], engage=True)
    sent: list[str] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        sent.append(kwargs.get("input", ""))
        return _Proc()

    monkeypatch.setattr(topo.subprocess, "run", _fake_run)
    records = topo.apply_plan(pending, dry_run=False)
    assert records and records[0]["applied"] is True
    assert len(sent) == 1, "one write per changed runner"
    body = json.loads(sent[0])["labels"]
    assert sorted(body) == ["gpu", "self-hosted-x64", "ubuntu-latest"], (
        f"the body must be the planned CUSTOM set (read-only labels are a 422): {body}"
    )
    for read_only in ("self-hosted", "Linux", "X64"):
        assert read_only not in body, f"{read_only} is read-only and must never be PUT"


def test_the_check_human_render_names_a_runner_it_did_not_reconcile(capsys):
    """REGRESSION (#10912 review P2): `apply` printed `unclassified`, `check` did not — so the
    tripwire's human render could read `verdict: OK` while naming nothing about a skipped runner.
    """
    windows = _runner(
        55,
        "pi-runner-win-1",
        ["self-hosted", "Windows", "X64"],
        read_only=frozenset({"self-hosted", "Windows", "X64"}),
    )
    with tempfile_dir() as tmp:
        fixture = Path(tmp) / "runners.json"
        fixture.write_text(json.dumps({"runners": [_runner(1, "pi-runner-x64-1", X64), windows]}))
        proc = subprocess.run(
            [sys.executable, str(TOOL), "check", "--from-fixture", str(fixture)],
            capture_output=True,
            text=True,
        )
    assert "pi-runner-win-1" in proc.stdout, f"a runner the tool skipped must be named: {proc.stdout}"
    assert "UNRECONCILED" in proc.stdout


# ------------------------------------- an unreadable read-back is not a pass
def test_an_unreadable_read_back_fails_closed(monkeypatch, capsys):
    """REGRESSION (#10912 review round 2, P2): the fail-closed guard on the read-back was unpinned —
    mutating it to treat "could not read back" as a pass left every test green. That mutant is the
    pre-fix P1 verbatim, so it needs its own test.
    """
    pre = measured()
    calls = {"n": 0}

    def _fetch(repo=topo.REPO):
        calls["n"] += 1
        if calls["n"] == 1:
            return pre
        raise SystemExit("gh api --paginate failed rc=1: HTTP 502")

    monkeypatch.setattr(topo, "fetch_runners", _fetch)
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply"])
    captured = capsys.readouterr()
    assert rc == 1, "a write we could not read back must not exit 0"
    assert "UNVERIFIED" in captured.err
    assert "already at the desired topology" not in captured.out, (
        "a failed run must not print the success line"
    )


# ------------------------------------- the WRITER's read-only net, not just the plan's
def test_the_writer_strips_a_read_only_label_even_if_a_plan_carries_one(monkeypatch):
    """REGRESSION (#10912 review round 2, P2): the `GITHUB_OWNED` filter in the write path was dead
    from the test's point of view — `desired_label_set` already returns custom-only, so deleting the
    filter left every test green. Drive the writer with a plan that DOES carry read-only labels.
    """
    pending = topo.Plan()
    pending.changes = {"pi-runner-x64-1": (42, [], ["self-hosted", "Linux", "X64", "self-hosted-x64", "ubuntu-latest"])}
    sent: list[str] = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        sent.append(kwargs.get("input", ""))
        return _Proc()

    monkeypatch.setattr(topo.subprocess, "run", _fake_run)
    records = topo.apply_plan(pending, dry_run=False)
    assert records[0]["applied"] is True
    body = json.loads(sent[0])["labels"]
    assert body == ["self-hosted-x64", "ubuntu-latest"], (
        f"the writer must drop read-only labels itself, not rely on the plan: {body}"
    )


# ------------------------------------- an armed-but-unusable pool is REPORTED
def test_an_offline_only_carrier_with_an_absorbing_queue_is_reported_as_starvation():
    """REGRESSION (#10912 review round 2, P2): `starvation_suspected` counted an OFFLINE carrier as
    engagement, so "label armed, nothing online can serve it, queue absorbing" — this PR's own defect
    class — produced verdict OK, no violation and no note.
    """
    pool = topo.parse_runners(
        {
            "runners": [
                _runner(1, "pi-runner-x64-1", [*X64, topo.FALLBACK_LABEL], status="offline"),
                _runner(2, "pi-runner-vm-2", ARM64_PLAIN, busy=True),
            ]
        }
    )
    report = topo.check(pool, queued_oldest_seconds=999_999)
    # `NO_X64_FALLBACK` also names it now (a carrier exists, none can serve it) — before the
    # predicate widening this pool produced NO signal from `check` at all.
    assert topo.VIOLATION_NO_X64_FALLBACK in [v["kind"] for v in report["violations"]], report
    assert report["starvation_suspected"] is True, (
        "an attached label that no ONLINE runner can serve, with an absorbing queue, must be reported"
    )


# ------------------------------ the success line, the render, and the transport guard
def test_a_zero_record_failure_does_not_print_the_success_line(monkeypatch, capsys):
    """REGRESSION (#10912 review round 3, P2): fix "don't print the success line on a failed run"
    was unpinned — reverting `if not records and ok:` to `if not records:` left the suite green,
    because the only test asserting that string ran through `--json`.

    The pool: the label is attached ONLY to an OFFLINE x64 runner, so the plan is EMPTY (nothing to
    write) and the run FAILS on the engage gate. Zero records AND a failure — the exact state.
    """
    pool = topo.parse_runners(
        {
            "runners": [
                _runner(1, "pi-runner-vm-2", ARM64_PLAIN),
                _runner(3, "pi-runner-x64-1", [*X64, topo.FALLBACK_LABEL], status="offline"),
            ]
        }
    )
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: pool)
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    rc = topo.main(["apply"])
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "changed: 0 runner(s)" in out, out
    assert "already at the desired topology" not in out, (
        "a failed engage with zero records must not claim the pool is at the desired topology"
    )
    assert topo.VIOLATION_ENGAGE_NO_X64_SERVER in out, "and the post verdict must name the failure"


def test_the_starvation_note_describes_an_armed_but_dead_arm_not_a_disarmed_one(capsys, tmp_path: Path):
    """REGRESSION (#10912 review round 3, P2): after the online filter, the note still said
    "ENGAGED NOWHERE" directly under `serving_fallback=1` — pointing a human at a disarmed gate
    when the arm was armed-but-dead. Different fault, different remedy; the text must match.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(
        json.dumps(
            {
                "runners": [
                    _runner(1, "pi-runner-x64-1", [*X64, topo.FALLBACK_LABEL], status="offline"),
                    _runner(2, "pi-runner-vm-2", ARM64_PLAIN, busy=True),
                ]
            }
        )
    )
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--from-fixture", str(fixture),
         "--queued-floor-seconds", "1"],
        capture_output=True,
        text=True,
    )
    assert "ENGAGED NOWHERE" not in proc.stdout, (
        f"the label IS armed (on an offline runner) — the note must say so: {proc.stdout}"
    )


def test_the_read_back_survives_a_malformed_page(monkeypatch, capsys):
    """REGRESSION (#10912 review round 3, P2): catching only SystemExit left JSONDecodeError (a
    truncated `--paginate` stream) and KeyError/ValueError (a malformed page) aborting AFTER the
    writes, with a traceback instead of the record of what was written and that it is unverified.
    """
    pre = measured()
    calls = {"n": 0}

    def _fetch(repo=topo.REPO):
        calls["n"] += 1
        if calls["n"] == 1:
            return pre
        raise json.JSONDecodeError("Expecting value", "<truncated page>", 0)

    monkeypatch.setattr(topo, "fetch_runners", _fetch)
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "UNVERIFIED" in err, f"a malformed read-back must be reported, not raised: {err}"


def test_a_transport_failure_on_one_runner_does_not_abort_the_other_writes(monkeypatch):
    """REGRESSION (#10912 review round 3, P2): an unguarded `subprocess.run` in the write loop
    (gh missing, a signal, a fork failure) raised out of the loop AFTER earlier writes landed,
    losing every record of what was written.
    """
    pending = topo.Plan()
    pending.changes = {
        "a-runner": (1, [], ["self-hosted-x64", "ubuntu-latest"]),
        "b-runner": (2, [], ["self-hosted-x64", "ubuntu-latest"]),
    }
    calls = {"n": 0}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(12, "Cannot allocate memory")
        return _Proc()

    monkeypatch.setattr(topo.subprocess, "run", _fake_run)
    records = topo.apply_plan(pending, dry_run=False)
    assert len(records) == 2, f"every planned runner must be reported, {len(records)} were"
    by_name = {r["runner"]: r for r in records}
    assert by_name["a-runner"]["applied"] is False and "transport" in by_name["a-runner"]["error"]
    assert by_name["b-runner"]["applied"] is True, "one failure must not stop the reconcile"


# ---------------------- the post-state render uses ONE definition on both halves
def test_the_post_state_render_counts_only_what_the_gate_counts(monkeypatch, capsys):
    """REGRESSION (#10912 review round 4, P2): the render's x64 half used the gate's online-filtered
    set while its non-x64 half summed `census`'s UNFILTERED `serving_fallback` — so an OFFLINE ARM64
    carrier printed `non-x64 serving=1` on the same line whose x64 half said 0. (And the x64 half
    itself was unpinned: reverting it to census's count left all 31 tests green.)
    """
    pool = topo.parse_runners(
        {
            "runners": [
                # BOTH carriers offline: `census` counts 2 serving, the gate (and therefore the
                # render) must count 0. One pool where the two definitions disagree is the only
                # shape that can tell them apart.
                _runner(1, "pi-runner-x64-1", [*X64, topo.FALLBACK_LABEL], status="offline"),
                _runner(2, "pi-runner-vm-2", ARM64_FALLBACK, status="offline"),
            ]
        }
    )
    reads = [pool, pool]  # the write lands on a pool that still cannot serve
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO, _r=reads: _r.pop(0))
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    topo.main(["apply"])
    out = capsys.readouterr().out
    assert "x64 serving=0" in out, (
        f"the x64 carrier is OFFLINE — census counts it, the gate does not, and the render must "
        f"agree with the gate that produced the ❌ beside it: {out}"
    )
    assert "non-x64 serving=0" in out, (
        f"an OFFLINE ARM64 carrier is not serving anything — the two halves of this line must use "
        f"one definition: {out}"
    )


def test_the_read_back_survives_a_missing_gh_binary(monkeypatch, capsys):
    """REGRESSION (#10912 review round 4, P2): the write loop learned to record an OSError but the
    READ-BACK did not catch it, so `gh` disappearing between the write and the read (or ENOMEM)
    killed the run with a traceback AFTER the writes, printing no payload at all.
    """
    pre = measured()
    calls = {"n": 0}

    def _fetch(repo=topo.REPO):
        calls["n"] += 1
        if calls["n"] == 1:
            return pre
        raise FileNotFoundError(2, "No such file or directory: 'gh'")

    monkeypatch.setattr(topo, "fetch_runners", _fetch)
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "UNVERIFIED" in err, f"a transport failure on the read-back must be reported, not raised: {err}"


# ---------------------------- a marker the host does not own is the same mis-serve
def test_a_foreign_pool_marker_is_a_violation_and_apply_heals_it():
    """REGRESSION (#10912 review round 5, BLOCKING): `desired_label_set` filtered only the FALLBACK
    label, so the OTHER arch's pool marker survived — an ARM64 box carrying `self-hosted-x64`.
    `runs-on: [self-hosted, self-hosted-x64]` is an AND, so x64 work lands on ARM (#6897), and
    `check` read only `ubuntu-latest`, so the pool reported `verdict: OK` — a false PASS in the
    field every reader keys on. `ci_runner_gate.sh:labels_for` drops BOTH markers; so must this.
    """
    arm_with_x64_marker = topo.parse_runner(
        _runner(77, "pi-runner-vm-9", ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", "self-hosted-x64"])
    )
    kinds = [v.kind for v in topo.violations([arm_with_x64_marker])]
    assert topo.VIOLATION_FOREIGN_POOL_MARKER in kinds, (
        f"an ARM64 box advertising the x64 pool must be named, not read as OK: {kinds}"
    )
    desired = topo.desired_label_set(arm_with_x64_marker, engage=True)
    assert "self-hosted-x64" not in desired, f"apply must heal the marker from the host: {desired}"
    assert "self-hosted-arm64" in desired
    post = [
        topo.parse_runner(
            _runner(77, "pi-runner-vm-9",
                    sorted(arm_with_x64_marker.read_only | set(desired)),
                    read_only=arm_with_x64_marker.read_only)
        )
    ]
    assert topo.violations(post) == [], "the healed runner must be clean"


def test_an_x64_marker_on_an_x64_box_survives_the_marker_reset():
    """The counterpart: healing must not strip the correct marker (an idempotent reconcile)."""
    x64 = topo.parse_runner(_runner(1, "pi-runner-x64-1", X64))
    desired = topo.desired_label_set(x64, engage=True)
    assert sorted(desired) == ["self-hosted-x64", "ubuntu-latest"], desired


# ------------------------ a read failure must not carry a capacity CLAIM (round 4 fix 4)
def test_an_empty_read_back_reports_only_the_read_failure(monkeypatch, capsys):
    """REGRESSION (#10912 review round 5): the "a read failure is not a capacity reading" branch
    had ZERO coverage — forcing `already_unmeasured` to False (the pre-fix behaviour: append the
    engage violation and print ❌ capacity over a pool nobody read) left all 33 tests green.
    """
    pre = measured()
    reads = [pre, []]  # the write takes; the read-back returns ZERO rows
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO, _r=reads: _r.pop(0))
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1 and payload["ok"] is False
    kinds = [v["kind"] for v in payload["post"]["violations"]]
    assert kinds == [topo.VIOLATION_UNMEASURED_EMPTY_POOL], (
        f"nothing was measured, so nothing may be claimed about capacity: {kinds}"
    )
    assert payload["post"]["verdict"] == topo.VIOLATION_UNMEASURED_EMPTY_POOL


def test_engage_and_strip_non_x64_are_ONE_topology_not_a_conflict(tmp_path: Path):
    """REGRESSION (VGATE, #11435 review round 2): `--strip-non-x64` was added to the
    `--engage | --disengage` exclusive group, so the ONE combination its own help text
    describes — "additionally" — was rejected by argparse (`not allowed with argument
    '--engage'`). A flag that can only be used by *not* naming the default it qualifies is a
    trap: the operator who asks for the documented thing gets a usage error.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    proc = subprocess.run(
        [
            sys.executable, str(TOOL), "apply", "--dry-run", "--engage", "--strip-non-x64",
            "--from-fixture", str(fixture), "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["strip_non_x64"] is True
    # The x64-only topology reaches a clean pool — the owner-gated half is reachable BY FLAG.
    assert payload["post"]["ok"] is True, payload["post"]
    assert payload["owner_gated"] == {}
    # 3 attaches (the capped arm) + 3 owner-gated strips = 6.
    assert len(payload["changes"]) == topo.ARM_CAP_DEFAULT + 3
    assert payload["arm_cap"] == topo.ARM_CAP_DEFAULT


# ------------------------------------------- the queue age is the OLDEST run, and it is proven
def test_the_queue_age_is_the_head_of_demand_not_the_oldest_ghost(monkeypatch):
    """REGRESSION (#11435 review round 3, P2): the age was `min(created_at)` over every run GitHub
    calls `queued`, which is NOT the gate's definition and which the gate explicitly rejects.

    `ci_runner_gate.sh`'s head scan: "the age of the HEAD OF REAL DEMAND (#2344), i.e. of the
    oldest run that HAS a queued job — not `min(created_at)` over every run GitHub calls queued (a
    ghost run's age is unbounded and means nothing)", with `QUEUE_GHOSTS` machinery because ghosts
    occur on these repos.

    The fixture puts a GHOST 90 days out in front (0 queued jobs), the real head at 30 days, and a
    fresh run at 60 seconds, so all three candidate readings are 3+ orders of magnitude apart:
    pre-#2344 MIN reports ~90 days, the newest-run bug reports ~60 s, and the answer is ~30 days.
    """
    _now = datetime.datetime.now(datetime.UTC)

    def stamp(**delta):
        return (_now - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")

    runs = "\n".join(
        [
            f"30\t{stamp(days=90)}",
            f"20\t{stamp(days=60)}",
            f"10\t{stamp(days=30)}",
            f"40\t{stamp(seconds=60)}",
        ]
    )
    queued_jobs = {30: 0, 20: 0, 10: 1, 40: 1}

    def fake_run(argv, **kwargs):
        assert argv[:2] == ["gh", "api"], argv
        url = next(a for a in argv if "actions/runs" in a)
        assert "--paginate" in argv, argv
        # ⛔ `sort`/`direction` are not parameters of the runs endpoint; sending them is what made
        # an "oldest" read return the newest.
        assert "sort=" not in url and "direction=" not in url, url

        class P:
            returncode = 0
            stderr = ""

        if "/jobs" in url:
            rid = int(re.search(r"/runs/(\d+)/jobs", url).group(1))
            P.stdout = str(queued_jobs[rid])
        else:
            P.stdout = runs
        return P()

    monkeypatch.setattr(topo.subprocess, "run", fake_run)
    probe = topo.fetch_queue_probe("daniel-ospina/tortoise")
    assert probe.head_run == 10, f"the head of real demand is run 10; got {probe.head_run}"
    assert probe.ghosts == 2, f"the two runs in front of it are ghosts; got {probe.ghosts}"
    assert probe.oldest_seconds is not None
    assert 29 * 24 * 3600 <= probe.oldest_seconds <= 31 * 24 * 3600, (
        f"the age must be the HEAD OF DEMAND (~30 days); got {probe.oldest_seconds}s — ~90 days "
        f"means the oldest GHOST was aged (the pre-#2344 reading), ~60 s means the newest run was"
    )
    # The thin reader `check` uses reports the same number.
    assert topo.fetch_oldest_queued_seconds("daniel-ospina/tortoise") == probe.oldest_seconds


def test_a_ghost_only_queue_is_a_readable_zero_and_counts_the_ghosts(monkeypatch):
    """Every queued run has zero queued jobs ⇒ nothing is waiting for a runner. That is a
    COMPLETE measurement (a readable zero), not a failed read and not an unbounded age."""
    _now = datetime.datetime.now(datetime.UTC)
    runs = "\n".join(
        f"{rid}\t{(_now - datetime.timedelta(days=d)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        for rid, d in ((7, 30), (8, 20))
    )

    def fake_run(argv, **kwargs):
        class P:
            returncode = 0
            stderr = ""
            stdout = "0"  # every run: zero queued jobs

        if "actions/runs?" in argv[3]:
            P.stdout = runs
        return P()

    monkeypatch.setattr(topo.subprocess, "run", fake_run)
    probe = topo.fetch_queue_probe("r")
    assert probe.oldest_seconds is None, "nothing is waiting, so there is no head to age"
    assert probe.ghosts == 2
    assert "ZERO jobs" in (probe.note or ""), probe.note


def test_an_unreadable_jobs_read_keeps_the_run_as_the_queue_head(monkeypatch):
    """The gate's doctrine: a ghost is discounted only on POSITIVE evidence. "Could not look" is
    not evidence, so the run STAYS the head — discounting real demand on a failed call is how a
    stalled pool reads healthy."""
    _now = datetime.datetime.now(datetime.UTC)
    old = (_now - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fake_run(argv, **kwargs):
        class P:
            returncode = 0
            stderr = ""
            stdout = f"9\t{old}"

        if "/jobs" in argv[3]:
            P.returncode = 1  # the jobs read FAILED
            P.stdout = ""
        return P()

    monkeypatch.setattr(topo.subprocess, "run", fake_run)
    probe = topo.fetch_queue_probe("r")
    assert probe.head_run == 9, "an unreadable jobs read must NOT discount the run"
    assert probe.ghosts == 0
    assert probe.oldest_seconds is not None and probe.oldest_seconds >= 3 * 24 * 3600 - 60
    assert "FAILED" in (probe.note or ""), probe.note


def test_the_queue_scan_bound_is_the_gates_own_and_an_illegal_one_measures_nothing(monkeypatch):
    """One ruling, two readers: the scan bound is read from the gate's variable, and a value the
    gate refuses (non-numeric, or 0 — "a bound of zero would disarm the leg rather than bound it")
    measures NOTHING here rather than silently scanning an arbitrary number of runs."""
    monkeypatch.delenv(topo.QUEUE_HEAD_SCAN_MAX_ENV, raising=False)
    assert topo.QUEUE_HEAD_SCAN_MAX_ENV == "CI_GATE_QUEUE_HEAD_SCAN_MAX"
    assert topo.queue_head_scan_max() == 5, "the gate's default is ${CI_GATE_QUEUE_HEAD_SCAN_MAX:-5}"
    for bad in ("zero", "0"):
        monkeypatch.setenv(topo.QUEUE_HEAD_SCAN_MAX_ENV, bad)
        assert topo.queue_head_scan_max() is None, bad
    _now = datetime.datetime.now(datetime.UTC)
    runs = f"9\t{(_now - datetime.timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}"

    def fake_run(argv, **kwargs):
        class P:
            returncode = 0
            stderr = ""
            stdout = runs

        return P()

    monkeypatch.setattr(topo.subprocess, "run", fake_run)
    monkeypatch.setenv(topo.QUEUE_HEAD_SCAN_MAX_ENV, "zero")
    probe = topo.fetch_queue_probe("r")
    assert probe.oldest_seconds is None, "an illegal bound must not age the queue from a guess"
    assert "not a legal scan bound" in (probe.note or ""), probe.note


def test_an_unreadable_queue_age_is_none_never_zero(monkeypatch):
    """A queue age nobody could read must not read as 'no queue'. `None` is the only honest
    answer, and `check` then omits the starvation judgement instead of inventing a fresh queue."""
    for rc, stdout in ((1, "2026-11-05T16:29:12Z"), (0, ""), (0, "garbage\nnot-a-timestamp")):
        class P:
            returncode = rc
            stderr = "boom"
            pass

        P.stdout = stdout
        monkeypatch.setattr(topo.subprocess, "run", lambda argv, _P=P, **kw: _P())
        assert topo.fetch_oldest_queued_seconds("r") is None, (rc, stdout)


# ------------------------------------------- the marker gate is code, not just a docstring
def test_a_runner_without_a_pool_marker_is_never_enrolled(monkeypatch):
    """REGRESSION (#11435 review, fresh-context cycle 2 P1): `unmanaged_reason`'s docstring
    attributed a `managed_ids` POOL-MARKER gate to itself, and no such gate existed. Measured
    probe before the fix: a Linux/X64 runner whose custom labels were `['gpu']` (no
    `self-hosted-x64`, no `self-hosted-arm64`) planned to
    `['gpu', 'self-hosted-x64', 'ubuntu-latest']` — i.e. `apply` enrolled an unprovisioned box
    the operator never added to the pool, and both `ubuntu-latest` and
    `[self-hosted, self-hosted-x64]` jobs would route to it. The tool's own contract says such a
    runner is LEFT BYTE-IDENTICAL.
    """
    orphan = topo.parse_runner(
        _runner(99, "legacy-gpu-box", ["self-hosted", "Linux", "X64", "gpu"])
    )
    assert topo.unmanaged_reason(orphan) is not None, "a runner carrying neither pool marker is not ours"
    pending = topo.plan([orphan], engage=True)
    assert pending.changes == {}, "no write may target a runner outside the pool"
    assert "legacy-gpu-box" in pending.unclassified, "the skip must be REPORTED, never silent"
    # …and a marked runner on the same host IS reconciled, so the gate is not over-broad.
    member = topo.parse_runner(
        _runner(100, "pi-runner-x64-9", ["self-hosted", "Linux", "X64", "self-hosted-x64"])
    )
    assert topo.unmanaged_reason(member) is None
    assert "pi-runner-x64-9" in topo.plan([member], engage=True).changes


# ------------------------------------------- the dry run's CAPACITY gate is proven, not assumed
def test_a_dry_run_on_an_x64less_pool_reports_the_capacity_gate(tmp_path: Path):
    """REGRESSION (#11435 review, fresh-context cycle 2 P2): the dry-run branch's
    `engage_gate(...)` could be replaced with `None` and all 38 tests stayed green — the gate that
    makes a pre-flight honest was unproven. On a pool with no x64 arm, a reconcile attaches the
    label nowhere servable, so the PROJECTION must say so and exit non-zero.
    """
    fixture = tmp_path / "arm-only.json"
    fixture.write_text(
        json.dumps(
            {
                "total_count": 2,
                "runners": [
                    _runner(1, "pi-runner-vm-2", ["self-hosted", "Linux", "ARM64", "self-hosted-arm64"]),
                    _runner(2, "pi-runner-vm-3", ["self-hosted", "Linux", "ARM64", "self-hosted-arm64"]),
                ],
            }
        )
    )
    proc = subprocess.run(
        [
            sys.executable, str(TOOL), "apply", "--dry-run", "--engage", "--strip-non-x64",
            "--from-fixture", str(fixture), "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert topo.VIOLATION_ENGAGE_NO_X64_SERVER in proc.stderr, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert payload["post_is_projection"] is True
    assert payload["post"]["verdict"] == topo.VIOLATION_ENGAGE_NO_X64_SERVER


# ------------------------------------------- one render, no self-contradiction
def test_the_apply_render_never_says_untouched_about_a_runner_it_writes(tmp_path: Path):
    """REGRESSION (#11435 review, fresh-context cycle 2 P2): an ARM64 carrier that also carries
    the FOREIGN `self-hosted-x64` marker IS rewritten (the marker is dropped — this PR's round-6
    fix) and was printed as `left untouched` on the same run. The two halves of one render
    contradicted each other, which is the class this PR keeps closing.
    """
    fixture = tmp_path / "mixed.json"
    fixture.write_text(
        json.dumps(
            {
                "total_count": 2,
                "runners": [
                    _runner(1, "pi-runner-x64-1", ["self-hosted", "Linux", "X64", "self-hosted-x64"]),
                    _runner(
                        2,
                        "pi-runner-vm-9",
                        ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", "self-hosted-x64",
                         "ubuntu-latest"],
                    ),
                ],
            }
        )
    )
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
    )
    lines = proc.stdout.splitlines()
    written = [line for line in lines if line.strip().startswith("pi-runner-vm-9:")]
    assert written, proc.stdout
    assert "left untouched" not in " ".join(lines), proc.stdout
    assert "strip skipped (owner-gated)" in proc.stdout, proc.stdout


def test_a_foreign_marker_is_a_violation_on_any_host():
    """REGRESSION (#11435 review, fresh-context cycle 3 P2 — a FALSE PASS): rule 1b skipped any
    runner whose arch could not be derived or that was not Linux, so a
    `Windows, ARM64, self-hosted-arm64, self-hosted-x64` box and a `Linux, self-hosted-x64` box
    (no arch label) both reported `verdict: OK`. `runs-on: [self-hosted, self-hosted-x64]` is a
    pure label AND, so an x64 Linux job is still selected onto both — the #6897 mis-serve the
    rule exists to name, reported clean.

    A marker whose owner cannot be derived belongs to no pool this runner is in, so it is
    reported; only the OWN-marker derivation needs an arch, never the violation.
    """
    windows = topo.parse_runner(
        _runner(1, "win-arm", ["self-hosted", "Windows", "ARM64", "self-hosted-arm64", "self-hosted-x64"])
    )
    archless = topo.parse_runner(_runner(2, "linux-noarch", ["self-hosted", "Linux", "self-hosted-x64"]))
    for runner in (windows, archless):
        kinds = {v.kind for v in topo.violations([runner])}
        assert topo.VIOLATION_FOREIGN_POOL_MARKER in kinds, (runner.name, kinds)
        assert topo.check([runner])["ok"] is False, runner.name
    # …and a host that owns the marker it carries is still clean (the rule is not blanket).
    fine = topo.parse_runner(_runner(3, "win-x64", ["self-hosted", "Windows", "X64"]))
    assert {v.kind for v in topo.violations([fine])} == set()


# ------------------------------- the engaging arm is capped at the gate's own bound (#9878)
#
# OWNER RULING 2026-11-02 (daniel-ospina/tortoise#9878), held by
# `agent-infra/scripts/ci_runner_gate.sh`: "what lands on the pool is bounded by
# FALLBACK_ARM_MAX, so queue length alone no longer decides how much contention a fallback
# creates". `tools/ci_runner_labels.py` CITED that cap and did not apply it, so `apply` armed all
# 16 x64 runners — 13 more than the containment an engaging pass may touch (review round 3 P1).
def _x64_pool(ids_and_names: list[tuple[int, str]], *, carriers: set[str] | None = None,
              offline: set[str] | None = None) -> list[topo.Runner]:
    carriers = carriers or set()
    offline = offline or set()
    return topo.parse_runners(
        {
            "runners": [
                _runner(
                    rid,
                    name,
                    X64 + ([topo.FALLBACK_LABEL] if name in carriers else []),
                    status="offline" if name in offline else "online",
                )
                for rid, name in ids_and_names
            ]
        }
    )


POOL_5 = [(5, "r5"), (1, "r1"), (3, "r3"), (9, "r9"), (2, "r2")]


def _apply_to(pool, pending):
    """Apply a plan to an ARBITRARY pool (the measured one has its own helper above)."""
    out = []
    for runner in pool:
        planned = pending.changes.get(runner.name)
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


def test_engage_arms_at_most_the_cap_lowest_id_first():
    """The cap is the ARM's size, in the gate's order (`sort -n`), not "the first three names"."""
    pending = topo.plan(_x64_pool(POOL_5), engage=True, arm_cap=3)
    assert pending.armed == ["r1", "r2", "r3"], "ids ascending — name order would arm r5, r1, r3"
    assert set(pending.changes) == {"r1", "r2", "r3"}, (
        "arming more than the cap is the defect this test exists for: 16 armed vs a cap of 3"
    )
    assert set(pending.over_cap) == {"r5", "r9"}, "the runners the cap withheld must be REPORTED"


def test_the_cap_is_read_from_the_gates_own_variable_and_default(monkeypatch):
    """One ruling, two readers — so the variable NAME and DEFAULT are pinned, not just the number.

    A tool with its own copy of "3" agrees with the gate by coincidence and drifts silently the
    first time either side is tuned.
    """
    monkeypatch.delenv(topo.ARM_CAP_ENV, raising=False)
    assert topo.ARM_CAP_ENV == "CI_GATE_FALLBACK_ARM_MAX"
    assert topo.fallback_arm_cap() == 3, "the gate's default is `${CI_GATE_FALLBACK_ARM_MAX:-3}`"
    monkeypatch.setenv(topo.ARM_CAP_ENV, "1")
    assert topo.fallback_arm_cap() == 1
    assert topo.plan(_x64_pool(POOL_5), engage=True).armed == ["r1"], (
        "the environment value must actually bound the arm — reading it and ignoring it is the bug"
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "scripts" / "ci_runner_gate.sh").is_file(),
    reason="agent-infra scripts are not materialised here (`tools/link_agent_infra_scripts.py`)",
)
def test_the_cap_string_matches_the_gate_it_mirrors():
    """Cross-repo pin: the gate's own line must still carry this variable with this default.

    Read as TEXT because importing a bash script is not a thing — but a string match on the
    assignment is exact enough to fail when either side renames the lever.
    """
    gate = (REPO_ROOT / "scripts" / "ci_runner_gate.sh").read_text()
    assert 'FALLBACK_ARM_MAX="${CI_GATE_FALLBACK_ARM_MAX:-3}"' in gate, (
        "the gate no longer declares the cap this tool mirrors — re-read #9878 before changing "
        "either side"
    )


@pytest.mark.skipif(
    not (REPO_ROOT / "scripts" / "ci_runner_gate.sh").is_file(),
    reason="agent-infra scripts are not materialised here (`tools/link_agent_infra_scripts.py`)",
)
def test_every_gate_citation_is_a_symbol_that_exists():
    """REGRESSION (#11435 review round 3, P2): every `ci_runner_gate.sh:NNN` cite in the module had
    drifted by tens-to-hundreds of lines with no test noticing.

    Measured against the gate at this head: `:882` (cited for "the explicit disarm") is a
    queue-ghost log line; `:926` / `:930` (cited for the header's capped-disarm warning and for
    "ARCH IS DELIBERATELY NOT A FILTER HERE") are queue-clock comments; `:975` (cited for
    `labels_for`) is an age UNREADABLE check; `:507` (cited for "STRIPPED ON PURPOSE") is a census
    comment. A line number reads as authority while being the one part of the sentence that rots,
    and it makes the claim un-refutable — a reader cannot tell "the cite moved" from "the claim
    was never true".

    So citations are by SYMBOL, and this test pins that each cited symbol is actually DEFINED in
    the gate: a rename on either side reds here instead of quietly making the prose wrong.
    """
    module = (REPO_ROOT / "tools" / "ci_runner_topology.py").read_text()
    gate = (REPO_ROOT / "scripts" / "ci_runner_gate.sh").read_text()
    # A line-number cite of this script is the defect, wherever it appears in the module.
    line_cites = re.findall(r"ci_runner_gate\.sh:\d", module)
    assert not line_cites, (
        f"{len(line_cites)} line-number citation(s) of ci_runner_gate.sh survive — cite the SYMBOL "
        f"(the numbers drift silently against the gate)"
    )
    cited = sorted(set(re.findall(r"ci_runner_gate\.sh::([A-Za-z_][A-Za-z0-9_]*)", module)))
    assert cited, "the module must cite the gate it mirrors, by symbol"
    missing = [
        name for name in cited
        if not re.search(rf"^\s*(?:{name}\(\)|{name}=)", gate, re.MULTILINE)
    ]
    assert not missing, (
        f"cited gate symbol(s) {missing} are not defined in ci_runner_gate.sh — the citation has "
        f"rotted (renamed or removed). Cited: {cited}"
    )


def test_an_illegal_cap_is_a_refusal_not_an_unbounded_arm(monkeypatch):
    """`ci_runner_gate.sh` refuses the pass on a non-numeric cap; so does this (`ARM_CAP_UNREADABLE`).

    The failure mode of guessing is silent and wide: a typo in the variable would arm 16 runners
    under a cap the operator believed was 3.
    """
    monkeypatch.setenv(topo.ARM_CAP_ENV, "three")
    assert topo.fallback_arm_cap() is None
    pending = topo.plan(_x64_pool(POOL_5), engage=True)
    assert pending.armed == [], "an unreadable cap arms NOTHING"
    assert pending.changes == {}, "nothing may be written under a cap that cannot be read"
    report = topo.check(_x64_pool(POOL_5), engage=True)
    assert report["ok"] is False
    assert topo.VIOLATION_ARM_CAP_UNREADABLE in {v["kind"] for v in report["violations"]}
    assert len(report["over_cap"]) == 5, "every candidate is named as unarmed"


def test_a_carrier_beyond_the_cap_keeps_its_label_and_is_reported():
    """The cap bounds ARMING; it never STRIPS. Removing the label is `--disengage`'s direction.

    The live pool is already wide (16 carriers from a previous arm). Tearing that down inside a
    verb named "apply" would re-starve the queue to satisfy a cap on ADDING capacity.
    """
    pool = _x64_pool(POOL_5, carriers={"r5", "r9", "r1", "r2", "r3"})
    pending = topo.plan(pool, engage=True, arm_cap=3)
    assert pending.changes == {}, "the arm (r1/r2/r3) already carries — a wide pool is idempotent"
    assert set(pending.over_cap) == {"r5", "r9"}
    assert "keeps the label it already carries" in pending.over_cap["r5"]
    post = _apply_to(pool, pending)
    assert all(topo.FALLBACK_LABEL in next(r for r in post if r.name == n).labels for n in ("r5", "r9")), (
        "a carrier beyond the cap must keep the label — an `apply` must never strip capacity"
    )


def test_an_offline_x64_runner_is_not_an_arm_slot():
    """A slot that cannot pick up a job is not capacity; arming it would spend a capped slot on a
    dead box (the same reading `serving_x64` applies to the post-state)."""
    pool = _x64_pool(POOL_5, offline={"r5", "r1"})
    pending = topo.plan(pool, engage=True, arm_cap=3)
    assert pending.armed == ["r2", "r3", "r9"], "the two offline rids are skipped, not counted"


def test_the_human_render_does_not_count_offline_carriers_as_capacity(capsys):
    """REGRESSION (#11435 lane finding, this session): `census` counts carriers, `_print_human`
    printed that raw count as `serving_fallback` — measured live, `X64 serving_fallback=16` while
    two of the sixteen were OFFLINE (`pi-runner-cell3-x64-9`, `pi-runner-x64-1`). The line an
    operator reads to answer "have we got capacity?" overstated it by exactly the dead slots.

    The offline carriers are still counted, named as such — hiding them would lose the fact that
    the labels are armed on a dead box (which is what the starvation note renders).
    """
    pool = _x64_pool(POOL_5, carriers={"r1", "r2", "r9"}, offline={"r9"})
    report = topo.check(pool)
    c = report["census"]
    assert c["by_host"]["X64"]["serving_fallback"] == 3, "the raw census stays raw"
    assert c["by_host"]["X64"]["serving_offline"] == 1
    assert c["serving_fallback_online"] == ["r1", "r2"], "the capacity reading is online-only"
    topo._print_human(report)
    out = capsys.readouterr().out
    assert "serving_fallback=2 (+1 offline, cannot serve)" in out, out


def test_a_zero_cap_is_a_disarmed_gate_and_apply_refuses(tmp_path: Path):
    """`FALLBACK_ARM_MAX=0` is DISARMED, not 'arm nothing quietly'.

    `ci_runner_gate.sh:220-222`: "A value of 0 … means the fallback is DISARMED: the pool is
    released and the engaging pass REFUSES, rather than reporting a successful engage carrying
    nothing." A verb named `apply` that printed "0 changes — already at the desired topology" and
    exited 0 under that value IS the successful-looking engage carrying nothing.
    """
    fixture = tmp_path / "runners.json"
    fixture.write_text(json.dumps(MEASURED_2026_11_07))
    env = dict(__import__("os").environ, **{topo.ARM_CAP_ENV: "0"})
    proc = subprocess.run(
        [sys.executable, str(TOOL), "apply", "--dry-run", "--from-fixture", str(fixture)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
    assert "DISARMED" in proc.stderr, proc.stderr
    assert "changed:" not in proc.stdout, "no success line may print when the engage is refused"
    # …and the cap of 0 still arms nothing, so a caller reading `plan` sees the same answer.
    pending = topo.plan(_x64_pool(POOL_5), engage=True, arm_cap=0)
    assert pending.armed == [] and pending.changes == {}


# ------------------------------- a remedy the tool cannot perform is not a remedy
def test_a_carrier_the_tool_cannot_relabel_never_gets_an_unperformable_remedy():
    """REGRESSION (#11435 review round 3, P2): the marker gate made `FALLBACK_ON_NON_X64`'s remedy
    UNPERFORMABLE for a carrier that carries no pool marker.

    `check` printed `REMEDY: strip 'ubuntu-latest' from this runner`, while `apply --strip-non-x64`
    skips exactly that runner (`unmanaged_reason` -> `managed_ids`: a runner with neither marker is
    not ours to relabel). So the diff could never close, and the pair of outputs read as a broken
    tool rather than as a scope boundary: `check` says "strip it", `apply` silently does not.

    The fix keeps the conservative gate (an unenrolled box must not be relabelled on a guess) and
    makes BOTH halves honest: the remedy names who performs it, and the write payload carries the
    runner under `out_of_scope` so `apply` says it too.
    """
    # A Linux/ARM64 carrier with NO pool marker: a real violation (it cannot serve an x64 image),
    # but not a runner this tool will relabel.
    orphan = topo.parse_runner(
        _runner(41, "legacy-arm-box", ["self-hosted", "Linux", "ARM64", topo.FALLBACK_LABEL])
    )
    report = topo.check([orphan])
    assert report["ok"] is False, report
    why = next(v["why"] for v in report["violations"] if v["runner"] == "legacy-arm-box")
    assert "MANUALLY" in why, (
        f"the remedy must name the human action it requires, not promise a strip this tool refuses: {why}"
    )
    assert topo.VIOLATION_FALLBACK_ON_NON_X64 in {v["kind"] for v in report["violations"]}
    # …and the write verb names the runner it cannot help instead of passing over it in silence.
    pending = topo.plan([orphan], engage=True, strip_non_x64=True)
    assert pending.changes == {}, "no write may target a runner outside the pool"
    assert "legacy-arm-box" in pending.out_of_scope, pending.out_of_scope
    assert "MANUAL" in pending.out_of_scope["legacy-arm-box"]

    # A runner the tool CAN relabel keeps the plain, performable remedy — the gate is not over-broad.
    member = topo.parse_runner(
        _runner(
            42, "pi-runner-vm-2",
            ["self-hosted", "Linux", "ARM64", "self-hosted-arm64", topo.FALLBACK_LABEL],
        )
    )
    member_report = topo.check([member])
    member_why = next(v["why"] for v in member_report["violations"] if v["runner"] == "pi-runner-vm-2")
    assert "MANUALLY" not in member_why, member_why
    assert member_report["out_of_scope"] == {}, "a reconcilable carrier is not out of scope"
    assert topo.plan([member], engage=True, strip_non_x64=True).changes, (
        "and the strip the remedy names IS performed for it"
    )


def test_the_check_render_names_the_out_of_scope_carrier(capsys):
    """The channel must reach the form the docstring calls the tripwire, not only `--json`."""
    orphan = topo.parse_runner(
        _runner(43, "legacy-arm-box", ["self-hosted", "Linux", "ARM64", topo.FALLBACK_LABEL])
    )
    topo._print_human(topo.check([orphan]))
    out = capsys.readouterr().out
    assert "OUT-OF-SCOPE legacy-arm-box" in out, out


# ------------------------------- a lossy exit code is a false signal in a script
def test_apply_separates_a_failed_write_from_an_owner_gated_residual(monkeypatch, capsys):
    """REGRESSION (#11435 review round 3, P2): `apply`'s exit code could not distinguish "writes
    failed" from "writes succeeded, owner-gated residual remains".

    On the measured pool a bare `apply` can never reach 0: the three ARM64 carriers keep
    `ubuntu-latest` until `--strip-non-x64`, which is #6897/#9878's decision. Reporting that as
    `1` — the same byte a failed PUT or an unreadable read-back produces — tells a wrapper
    "retry, something is broken" about a run that did exactly what the tool is permitted to do.

    So: 0 clean · 1 the write/verify failed · 3 writes landed, an OWNER-GATED residual remains.
    """
    pre = measured()
    # ENGAGE ONLY: every planned write lands, the ARM64 carriers are left byte-identical.
    post = _post_pool(topo.plan(pre, engage=True))
    reads = [pre, post]
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: reads.pop(0))
    monkeypatch.setattr(topo, "fetch_oldest_queued_seconds", lambda repo=topo.REPO: None)
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": True}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == topo.EXIT_OWNER_GATED == 3, (rc, payload)
    assert payload["exit"] == topo.EXIT_OWNER_GATED
    assert payload["owner_gated_only"] is True
    assert payload["ok"] is False, "the pool is not clean — the code says WHY it is not clean"
    # …and the survivors are exactly the owner-gated ones, not a mixed bag.
    assert {v["kind"] for v in payload["post"]["violations"]} == {topo.VIOLATION_FALLBACK_ON_NON_X64}
    assert {v["runner"] for v in payload["post"]["violations"]} == set(payload["owner_gated"])

    # A FAILED WRITE IS A DIFFERENT CODE — same pool, one PUT that did not take.
    reads = [pre, post]
    monkeypatch.setattr(topo, "fetch_runners", lambda repo=topo.REPO: reads.pop(0))
    monkeypatch.setattr(
        topo,
        "apply_plan",
        lambda pending, *, dry_run, repo=topo.REPO: [
            {"runner": n, "id": rid, "before": b, "after": a, "applied": False, "error": "422"}
            for n, (rid, b, a) in pending.changes.items()
        ],
    )
    rc = topo.main(["apply", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == topo.EXIT_FAILED == 1, (rc, payload)
    assert payload["owner_gated_only"] is False
    assert payload["exit"] == topo.EXIT_FAILED


# ── the refusal this tool prints must name a command that RUNS (#5128, #11483) ──


def test_the_refusal_names_an_invocation_that_actually_runs():
    """★ #11483 review round 4 (P1): a refusal must not name a command that dies.

    `verb` is a REQUIRED positional, so the bare path exits **2** — argparse's own
    usage-error status — and the tool never runs. An operator on a `<3.12`
    interpreter then follows an ATTRIBUTABLE refusal into a SECOND unattributed
    error, which is the exact harm `#5128` exists to prevent.

    The census cannot catch this, which is why it survived to a review round:
    `_expected_invocation` in `tests/test_entry_point_python_guard.py` returns the
    bare path for every loose script and its assertion is a SUBSTRING test, so a
    form that is a PREFIX of a working one passes. This row therefore measures the
    invocation the refusal ACTUALLY PRINTS, by running it.

    Mutant: delete ` --help` from the refusal's f-string in
    `tools/ci_runner_topology.py` — the extracted invocation loses its only
    argument, argparse exits 2 on the missing `verb`, and this row reds on
    `probe.returncode == 0`.
    """
    forced_old_interpreter = (
        "import runpy, sys\n"
        "sys.version_info = (3, 9, 6, 'final', 0)\n"
        "runpy.run_path(sys.argv[1], run_name='__main__')\n"
    )
    refusal = subprocess.run(
        [sys.executable, "-c", forced_old_interpreter, str(TOOL)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    # 4, not 1: 1 is `EXIT_FAILED`, and a refusal that wears a verdict code reads
    # to a caller as "the write failed" (#11342).
    assert refusal.returncode == 4, (
        f"the guard must refuse an old interpreter with 4, got {refusal.returncode} "
        f"(stderr={refusal.stderr!r})"
    )

    named = re.search(r"`(uv run python [^`]+)`", refusal.stderr)
    assert named is not None, (
        f"the refusal must name the invocation that works, in backticks: {refusal.stderr!r}"
    )
    argv = named.group(1).split()
    assert argv[:3] == ["uv", "run", "python"], (
        f"the refusal names `{named.group(1)}`, which is not a `uv run python …` form"
    )

    # Run what the refusal named, under THIS interpreter: `uv run` adds nothing to
    # a `--help` probe and would make the row depend on uv being installed.
    probe = subprocess.run(
        [sys.executable, *argv[3:]],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, (
        f"the refusal names `{named.group(1)}`, which exits {probe.returncode} — a "
        f"refusal that names a command which dies is a SECOND unattributed error, "
        f"the harm #5128 exists to prevent (stderr={probe.stderr!r}). `verb` is a "
        f"required positional, so `--help` is what makes this form resolve."
    )
