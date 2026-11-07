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
    assert topo.check(post, strip_non_x64=True)["census"]["by_host"]["X64"]["serving_fallback"] == 16


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
    assert "pi-runner-x64-1" in desired.changes
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
    # 16 x64 attach ONLY: the 3 ARM64 carriers are owner-gated and left byte-identical.
    assert len(payload["changes"]) == 16
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
    assert topo.FALLBACK_LABEL in after["pi-runner-x64-1"], (
        "a bare apply must attach the fallback to the x64 pool, not merely strip it elsewhere"
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
    # 16 attaches + 3 strips.
    assert len(payload["changes"]) == 19
