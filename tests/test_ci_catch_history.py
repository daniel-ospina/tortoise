"""#10131 — the catch-history measurement, and the three rules it must not get wrong.

The report answers one question per `slow_files` entry ("has this file ever gone red on a
PR head, or only on main?"), and the value of the answer rests entirely on three
properties. Each has a test here that FAILS when the property is broken:

1. **The newest ATTEMPT decides** — a re-run adds a red, it does not clear one, so a
   name-keyed rollup that keeps the first red (or the first green) is wrong in both
   directions. `test_the_newest_attempt_decides_...` fails if the reduction stops being
   newest-by-id.
2. **Grouping is `(app, workflow, job)`** — two workflows can publish the same job name,
   and then a newer green of one masks the red of the other.
   `test_two_workflows_sharing_a_job_name_...` fails on a name-only grouping — and it calls
   `reduce_attempts` DIRECTLY with a multi-entry workflow map, because `build_history`
   reduces one run at a time (where the workflow component is constant and a name-only
   reducer would produce the same rows): the end-to-end shape cannot fail there.
3. **An unreadable artifact is UNKNOWN, never NEVER** — "we could not check" and "we
   checked and it was clean" are different facts, and folding the first into the second is
   how a report becomes a licence for a wrong decision.
   `test_a_missing_artifact_is_unknown_not_never` fails if UNKNOWN is folded into NEVER.
4. **A file excluded from the pre-merge lane BY DECISION is neither of those** — #10194
   (owner decision A) took `tests/eval/**` out of the gating shards, so those `slow_files`
   rows do not run on a PR head at all. Reading them as UNKNOWN presents a decision as a
   measurement hole; reading them as NEVER presents it as a clean bill.
   `test_a_file_taken_off_the_gating_lane_is_named_as_a_decision_not_a_gap` fails if the
   non-gating lane is folded into either bucket, and the 74-row test fails if the 74 are
   not fully partitioned by the lanes `ci_selection` derives (a hardcoded split rotted once
   already — that is what the merge with #10194 broke).
5. **A leg RUNNING is not a FILE running** — the `test-slow` legs are diff-SCOPED on tier-2,
   so coverage is counted per FILE from the leg's artifact (`junit.xml` testcases +
   `expected-nodeids.txt`).
   `test_a_leg_that_ran_covers_only_the_files_its_artifact_names` fails if coverage is
   credited from the leg's PARTITION, which mints NEVER for files that never ran.
6. **A red a re-run CLEARED is inside a GREEN run** — a run's conclusion is its newest
   attempt's, so a failure-only sample can never see one.
   `test_a_red_cleared_by_a_rerun_is_visible_because_green_runs_are_read` fails if
   `success`-conclusion runs are not enumerated.
7. **A tree that cannot answer the lane question is refused, not guessed from** — the
   non-gating lane is disk-derived, so a partial tree must not produce a report.
   `test_a_tree_without_the_subtree_lane_directories_fails_closed` fails if that read fails
   open.
8. **A wall bound that expires while LISTING stops the listing** — a measured live walk spent
   ~350 s listing six populations before the detail loop's first check, so a bound consulted
   only there bounded nothing, and a population that was never listed must leave its files
   UNKNOWN rather than answering them from an empty sample.
   `test_a_wall_bound_that_expires_while_listing_stops_and_says_so` fails if the bound is
   checked only inside the detail loop (it then lists all six populations).

Everything is offline: the fixtures are the same payloads a live run reads, archived
(`runs.json`, `jobs/<run_id>.json`, `artifacts/<run_id>/<name>.zip`), so these tests also
pin the reviewer-reproducible path.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_catch_history as catch
from tools import ci_selection

REPO = Path(__file__).resolve().parents[1]

SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "1111111111111111111111111111111111111111"
RUN_PR = 1001
RUN_PUSH = 2002
RUN_OTHER_WORKFLOW = 3003

MANIFEST = ci_selection.load_manifest()
LEGS = catch.leg_partitions(MANIFEST)
SLOW_INDEX = catch.manifest_index(MANIFEST)


# --- fixture builders -------------------------------------------------------


def _junit(cases: list[tuple[str, str, bool]]) -> bytes:
    """A JUnit XML with ``file``/``classname`` populated the way xunit1 does."""
    body = []
    for path, name, failed in cases:
        classname = path.removesuffix(".py").replace("/", ".")
        child = "<failure message='boom'/>" if failed else ""
        body.append(
            f'<testcase classname="{classname}" name="{name}" file="{path}" line="1">{child}</testcase>'
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest" tests="{len(cases)}">'
        + "".join(body)
        + "</testsuite></testsuites>"
    ).encode()


def _zip(xml: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("junit.xml", xml)
    return buffer.getvalue()


def _zip_members(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _nodeids(paths: list[str]) -> bytes:
    """`expected-nodeids.txt` as `skip-guard.py --emit-manifest` writes it."""
    return ("# expected nodeids (collect-only)\n" + "".join(f"{p}::test_one\n" for p in paths)).encode()


def _run(
    run_id: int,
    sha: str,
    event: str,
    *,
    pr: int | None = None,
    branch: str = "main",
    workflow_id: int = 111,
    created: str = "2026-10-20T00:00:00Z",
    conclusion: str = "failure",
) -> dict:
    return {
        "id": run_id,
        "head_sha": sha,
        "event": event,
        "head_branch": branch if event == "push" else "feat/x",
        "run_attempt": 1,
        "workflow_id": workflow_id,
        "name": "python-ci",
        "conclusion": conclusion,
        "created_at": created,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "pull_requests": [{"number": pr}] if pr else [],
    }


def _check(
    check_id: int,
    run_id: int,
    job: str,
    conclusion: str,
    *,
    workflow_id: int | None = None,
) -> dict:
    """One JOB record, in the shape `runs/<id>/jobs?filter=all` returns.

    Deliberately the Jobs-API shape (``html_url``, no ``app``): that is what a live run
    reads, and `normalise_job` is what makes `ci_verdict`'s reduction accept it.
    """
    suffix = f"/job/{check_id}"
    details = f"https://github.com/o/r/actions/runs/{run_id}{suffix}"
    if workflow_id is not None:
        details += "?pr=1"
    return {
        "id": check_id,
        "name": job,
        "status": "completed",
        "conclusion": conclusion,
        "html_url": details,
        "run_attempt": 1,
        "started_at": "2026-10-20T00:01:00Z",
    }


def _world(
    tmp: Path,
    *,
    runs: list[dict],
    jobs: dict[int, list[dict]] | None = None,
    artifacts: dict[tuple[int, str], bytes] | None = None,
    retention: int | None = 90,
) -> Path:
    root = tmp / "fixture"
    root.mkdir(parents=True)
    (root / "runs.json").write_text(
        json.dumps({"total_count": len(runs), "workflow_runs": runs}), encoding="utf-8"
    )
    (root / "jobs").mkdir()
    for run_id, items in (jobs or {}).items():
        (root / "jobs" / f"{run_id}.json").write_text(
            json.dumps({"total_count": len(items), "jobs": items}), encoding="utf-8"
        )
    for (run_id, name), payload in (artifacts or {}).items():
        directory = root / "artifacts" / str(run_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.zip").write_bytes(payload)
    if retention is not None:
        (root / "retention.json").write_text(json.dumps({"days": retention}), encoding="utf-8")
    return root


def _report(root: Path) -> dict:
    return catch.build_history(MANIFEST, catch.OfflineClient(root))


def _row(report: dict, file: str) -> dict:
    """Look a row up by its manifest entry OR by the path it lives at."""
    for row in report["files"]:
        if file in (row["file"], row["path"]):
            return row
    raise AssertionError(f"{file} is not in the table")


def _leg_of(file: str) -> tuple[str, str]:
    """Which (family, leg) a manifest entry runs in — read from the real partition."""
    observed = catch.to_observed(file)
    for family, legs in LEGS.items():
        for leg, files in legs.items():
            if observed in files:
                return family, leg
    raise AssertionError(f"{file} is in no leg")


# --- 1. the newest attempt decides -----------------------------------------


def test_the_newest_attempt_decides_and_the_older_red_is_cited_as_cleared(tmp_path):
    """A red a NEWER success re-measured is not a catch — and is not silently dropped either."""
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    red_first = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=42)],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, job, "failure"),
                _check(200, RUN_PR, job, "success"),  # the re-run: NEWEST by id
            ]
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    report = _report(red_first)
    row = _row(report, "test_billing.py")

    # ⛔ If this assertion ever reads CATCH-PR, the reduction has stopped being
    # newest-by-id: attempt 100 was cleared by attempt 200.
    assert row["status"] == catch.NEVER
    assert row["pr_head_catches"] == []
    assert [c["run_id"] for c in row["flake_cleared"]] == [str(RUN_PR)]
    assert row["flake_cleared"][0]["files"] == ["test_billing.py"]
    # ⛔ And it reaches the COMMITTED deliverable, not only the JSON: the row reads NEVER
    # ("attested clean"), so this column is the only place the markdown report can say the
    # file did go red on that very PR head. Pre-fix `flake_cleared` appeared nowhere in
    # `render_table`, i.e. the docs/ report this PR exists to produce was silent about it.
    table = catch.render_table(report)
    assert "cleared by a re-run" in table
    cleared_row = next(line for line in table.splitlines() if "`test_billing.py`" in line)
    assert f"run {RUN_PR}" in cleared_row
    # A red a SUCCESS re-measured is cleared — not "superseded", which is the bucket for a
    # red nothing cleared. The two must not collapse into one number.
    assert report["coverage"]["older_reds_superseded"] == 0

    # The SAME fixture without the newer attempt IS a catch — that is what makes the
    # assertion above bite: the only difference is the newer attempt's existence.
    only_red = _world(
        tmp_path / "red",
        runs=[_run(RUN_PR, SHA, "pull_request", pr=42)],
        jobs={RUN_PR: [_check(100, RUN_PR, job, "failure")]},
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    red_report = _report(only_red)
    assert _row(red_report, "test_billing.py")["status"] == catch.CATCH_PR
    assert _row(red_report, "test_billing.py")["pr_head_catches"][0]["pr"] == 42


def test_the_reduction_takes_the_newest_attempt_by_id_not_the_first_or_the_oldest():
    """The rule, at the level of the reduction itself.

    A name-keyed or first-attempt reading of a re-run is the defect this pins: attempt 100
    failed, attempt 200 passed, attempt 50 also passed. The newest by ``id`` decides, and
    the older red is retained as a CLEARED attempt rather than dropped or promoted.
    """
    attempts = catch.reduce_attempts(
        [
            _check(100, RUN_PR, "test-slow (b)", "failure"),
            _check(200, RUN_PR, "test-slow (b)", "success"),
            _check(50, RUN_PR, "test-slow (b)", "success"),
        ],
        {str(RUN_PR): ("111", "python-ci")},
    )
    assert len(attempts) == 1
    assert attempts[0].latest.check_id == 200
    assert attempts[0].latest.state == "green"
    # ⛔ Not dropped, not promoted: the red is carried as cleared.
    assert [a.check_id for a in attempts[0].cleared] == [100]


def test_a_newer_attempt_that_is_cancelled_does_not_clear_the_red(tmp_path):
    """Only a SUCCESS re-measures. A cancellation is the absence of a measurement."""
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=7)],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, job, "failure"),
                _check(300, RUN_PR, job, "cancelled"),
            ]
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    assert _row(_report(root), "test_billing.py")["status"] == catch.CATCH_PR


def test_an_older_red_that_nothing_cleared_is_never_labeled_flake_cleared(tmp_path):
    """`flake_cleared` asserts a clean re-measurement. A cancelled (or second red) re-run is
    not one, and the attempt's artifact is the SAME artifact the run's red already cites.

    ⛔ Before this was separated, the older red of the same run+job was emitted BOTH as a
    `pr_head_catches` citation and as a `flake_cleared` citation of the identical run — a
    reader counting `flake_cleared` would read "a later attempt re-ran this file green" for a
    file whose only later attempt was cancelled. Nothing was cleared, so nothing may say it
    was; the red is `superseded` (counted in coverage) instead.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    artifact = _zip(_junit([("tests/test_billing.py", "t", True)]))
    for label, newer in (("cancelled", "cancelled"), ("second-red", "failure")):
        root = _world(
            tmp_path / label,
            runs=[_run(RUN_PR, SHA, "pull_request", pr=7)],
            jobs={
                RUN_PR: [
                    _check(100, RUN_PR, job, "failure"),
                    _check(300, RUN_PR, job, newer),
                ]
            },
            artifacts={(RUN_PR, catch.artifact_name(family, leg)): artifact},
        )
        report = _report(root)
        row = _row(report, "test_billing.py")
        assert row["status"] == catch.CATCH_PR
        assert row["flake_cleared"] == [], f"{label}: nothing cleared this red"
        # Cited ONCE, not once per attempt: both attempts' evidence is the run's one artifact.
        assert [c["run_id"] for c in row["pr_head_catches"]] == [str(RUN_PR)]
        # Accounted for, not dropped — the red is visible in the counts.
        assert report["coverage"]["older_reds_superseded"] == 1


# --- 2. the group key is (app, workflow, job) ------------------------------


def test_two_workflows_sharing_a_job_name_do_not_mask_each_other(tmp_path):
    """`test-slow (b)` from workflow 111 (red) and workflow 222 (green) are DIFFERENT groups.

    This is the AGENTS.md failure mode: a name-keyed grouping takes the newer green of the
    other workflow and reports the commit clean, hiding a real red on a PR head.

    ⛔ The DISCRIMINATING half is the direct reduction below, and it has to be: `build_history`
    reduces ONE RUN AT A TIME (`workflow_map = {ref.run_id: …}`), so the workflow component of
    the key is constant inside every call and the end-to-end fixture below cannot tell the
    two groupings apart — a name-only reducer would produce the same rows. The property lives
    in `reduce_attempts`, which is the shape `ci_verdict.group_latest_attempts` is written
    for: many runs' job lists, one multi-entry `run_workflow_map`.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"

    # (a) the grouping itself, on the input where it can actually fail.
    groups = catch.reduce_attempts(
        [
            _check(100, RUN_PR, job, "failure"),
            _check(900, RUN_OTHER_WORKFLOW, job, "success"),  # NEWER id, other workflow
        ],
        {
            str(RUN_PR): ("111", "python-ci"),
            str(RUN_OTHER_WORKFLOW): ("222", "ci"),
        },
    )
    # ⛔ Fails on a name-only grouping: one group, whose newest attempt (id 900) is GREEN.
    assert len(groups) == 2, "the two workflows must stay two groups"
    by_run = {group.latest.run_id: group for group in groups}
    assert by_run[str(RUN_PR)].latest.state == "red"
    assert by_run[str(RUN_OTHER_WORKFLOW)].latest.state == "green"

    # (b) end to end: the run loop must not collapse them either.
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PR, SHA, "pull_request", pr=88, workflow_id=111),
            _run(RUN_OTHER_WORKFLOW, SHA, "pull_request", pr=88, workflow_id=222),
        ],
        jobs={
            RUN_PR: [_check(100, RUN_PR, job, "failure")],
            RUN_OTHER_WORKFLOW: [_check(900, RUN_OTHER_WORKFLOW, job, "success")],
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    row = _row(_report(root), "test_billing.py")
    assert row["status"] == catch.CATCH_PR
    assert [c["run_id"] for c in row["pr_head_catches"]] == [str(RUN_PR)]


# --- 3. an unreadable artifact is UNKNOWN, never NEVER ---------------------


def test_a_missing_artifact_is_unknown_not_never(tmp_path):
    """The 90-day retention bound is a COVERAGE LIMIT, not a clean bill of health."""
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=13)],
        jobs={RUN_PR: [_check(100, RUN_PR, job, "failure")]},
        artifacts={},  # expired / never produced
    )
    report = _report(root)
    row = _row(report, "test_billing.py")

    # ⛔ Both assertions are load-bearing: UNKNOWN and NEVER are the two answers a reader
    # would act on oppositely.
    assert row["status"] == catch.UNKNOWN
    assert row["status"] != catch.NEVER
    assert row["unknown"], "an unattributable red must carry a reason"
    assert "retention" in row["unknown"][0]["reason"]
    assert row["unknown"][0]["run_id"] == str(RUN_PR)
    assert report["coverage"]["red_jobs_unattributable"] == 1
    assert report["coverage"]["red_jobs_attributed"] == 0


def test_an_unattributable_red_is_scoped_to_its_leg(tmp_path):
    """A missing artifact in leg (b) says nothing about the files in leg (a)."""
    family, leg = _leg_of("test_billing.py")
    other_leg = next(leg_name for leg_name in LEGS[family] if leg_name != leg)
    other_file = sorted(LEGS[family][other_leg])[0]
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=13)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
    )
    report = _report(root)
    scoped = _row(report, "test_billing.py")
    assert scoped["status"] == catch.UNKNOWN
    assert any("artifact" in u["reason"] for u in scoped["unknown"])
    # The other leg is NOT tarred with this red: it has its own, different UNKNOWN reason
    # (nothing measured it) and no citation pointing at this run.
    elsewhere = _row(report, other_file)
    assert all(u["run_id"] != str(RUN_PR) for u in elsewhere["unknown"])


# --- attribution through the job's own JUnit ------------------------------


def test_a_push_only_red_is_catch_push_only(tmp_path):
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PUSH, SHA, "push")],
        jobs={RUN_PUSH: [_check(100, RUN_PUSH, f"{family} ({leg})", "failure")]},
        artifacts={
            (RUN_PUSH, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    row = _row(_report(root), "test_billing.py")
    assert row["status"] == catch.CATCH_PUSH_ONLY
    assert row["pr_head_catches"] == []
    assert [c["run_id"] for c in row["push_main_catches"]] == [str(RUN_PUSH)]


def test_classname_is_the_fallback_when_the_file_attribute_is_absent(tmp_path):
    family, leg = _leg_of("test_billing.py")
    xml = (
        b'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        b'<testcase classname="tests.test_billing.TestThing" name="t"><failure/></testcase>'
        b"</testsuite></testsuites>"
    )
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=5)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
        artifacts={(RUN_PR, catch.artifact_name(family, leg)): _zip(xml)},
    )
    # classname-only attribution must still name the file, not drop the red.
    assert _row(_report(root), "test_billing.py")["status"] == catch.CATCH_PR


def test_a_red_with_no_failing_testcase_is_unknown_not_attributed(tmp_path):
    """The watchdog-kill shape: the leg ran, was killed, and names nothing."""
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=3)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
        artifacts={(RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([]))},
    )
    row = _row(_report(root), "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    assert "names no failing slow file" in row["unknown"][0]["reason"]


# --- the 74 rows, and the carve-out half of them --------------------------


def test_the_74_rows_are_fully_partitioned_by_the_lanes_ci_selection_derives(tmp_path):
    """Every `slow_files` entry belongs to EXACTLY one pre-merge lane — derived, not assumed.

    ⛔ This is the assertion that rotted in the first cut: the split was hardcoded as
    "74 = 65 `test-slow` + 9 carve-out", and #10194's owner decision then took 5 eval files
    out of the gating shards, so the constant described a partition that no longer existed.
    Reading the lanes out of `ci_selection` makes a re-cut a non-event, and an entry that
    falls out of every lane is caught here instead of being silently reported as UNKNOWN.
    """
    root = _world(tmp_path, runs=[])
    report = _report(root)
    # ⛔ DERIVED, not the literal 74: a snapshot of the live config reds on any legitimate
    # `slow_files` edit with no update guidance, and it is redundant with the manifest read
    # right below it.
    assert len(report["files"]) == len(MANIFEST["slow_files"])
    assert len(catch.manifest_index(MANIFEST)) == len(MANIFEST["slow_files"])

    lanes = catch.slow_lane(MANIFEST)
    slow_legs = {f for legs in LEGS[catch.JOB_SLOW].values() for f in legs}
    carve_legs = {f for legs in LEGS[catch.JOB_CARVE].values() for f in legs}
    non_gating = {catch.to_observed(f) for f in ci_selection.on_demand_files(MANIFEST)}

    assert len(lanes) == len(MANIFEST["slow_files"])
    assert report["coverage"]["lane_" + catch.LANE_ORPHAN] == 0, (
        "a slow file is in no gating leg and not on the declared non-gating lane"
    )
    assert report["coverage"]["lane_" + catch.LANE_GATING] == len(slow_legs)
    assert report["coverage"]["lane_" + catch.LANE_NON_GATING] == len(
        set(lanes) & non_gating - carve_legs
    )
    # The carve-out half is still read (it is the half a `test-slow`-only report loses).
    assert _row(report, "test_hosted_backup.py")["lane"] == catch.LANE_CARVE
    # With no runs at all nothing was measured: the gating legs are UNKNOWN (a gap) while
    # the non-gating lane is NOT-PREMERGE (a decision) — the two are never the same cell.
    assert _row(report, "test_billing.py")["status"] == catch.UNKNOWN
    assert report["coverage"]["status_" + catch.UNKNOWN] == len(slow_legs) + len(
        carve_legs & set(lanes)
    )


def test_a_file_taken_off_the_gating_lane_is_named_as_a_decision_not_a_gap(tmp_path):
    """The non-gating lane reads as a DECISION, never as an unmeasured hole or a clean bill.

    #10194 (owner decision A) removed `tests/eval/**` from the merge-gating shards. A row
    for one of those files must say so: UNKNOWN would present the decision as a measurement
    gap, NEVER would present it as "we ran it on every PR and it caught nothing".
    """
    root = _world(tmp_path, runs=[])
    report = _report(root)
    non_gating = [row for row in report["files"] if row["lane"] == catch.LANE_NON_GATING]
    assert non_gating, "#10194's non-gating lane must show up in the partition"
    assert all(catch.to_observed(row["file"]) not in {
        f for legs in LEGS.values() for files in legs.values() for f in files
    } for row in non_gating)

    row = non_gating[0]
    # ⛔ Both buckets are the wrong answer; only NOT-PREMERGE carries the reason.
    assert row["status"] == catch.NOT_PREMERGE
    assert row["status"] != catch.UNKNOWN
    assert row["status"] != catch.NEVER
    assert row["unknown"] == [], "an exclusion has no UNKNOWN reason — it has a decision"
    assert "#10194" in row["note"] and "NON-GATING" in row["note"]
    # And the table renders the reason instead of the blank a "-" cell would show.
    table = catch.render_table(report, only="nonpremerge")
    assert "#10194" in table
    assert f"{len(non_gating)}" in catch.render_summary(report)


def test_the_bare_carve_out_job_is_shard_a_and_attributes_the_nine(tmp_path):
    """`test-carve-out` (no suffix) is shard 0 — its artifact is `...-a`, not `...-`."""
    assert catch.job_family("test-carve-out") == ("test-carve-out", "a")
    assert catch.job_family("test-slow") is None  # matrix-only; never claimed
    assert catch.job_family("test (c)") is None
    assert catch.job_family("python-ci-gate") is None

    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=77)],
        jobs={RUN_PR: [_check(100, RUN_PR, "test-carve-out", "failure")]},
        artifacts={
            (RUN_PR, "pytest-log-test-carve-out-a"): _zip(
                _junit([("tests/test_hosted_backup.py", "t", True)])
            )
        },
    )
    row = _row(_report(root), "test_hosted_backup.py")
    assert row["status"] == catch.CATCH_PR
    assert row["pr_head_catches"][0]["job"] == "test-carve-out"


def test_the_markdown_table_renders_its_citations_and_its_unknowns(tmp_path):
    """`--md-out` IS the deliverable — the table must render the SERIALISED rows.

    ⛔ `FileHistory.to_dict()` returns mappings, so a table that calls `.label()` on the
    dataclasses raises `AttributeError` on every row that carries a citation or an UNKNOWN
    reason: the empty row renders, the rows that matter do not. `render_summary(report)` was
    asserted by another test and never touched `render_table`, so nothing caught it.
    """
    family, leg = _leg_of("test_billing.py")
    other_leg = next(name for name in LEGS[family] if name != leg)
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=21)],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, f"{family} ({leg})", "failure"),
                _check(101, RUN_PR, f"{family} ({other_leg})", "failure"),  # no artifact
            ]
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            )
        },
    )
    report = _report(root)
    table = catch.render_table(report)
    # The CITATION row (a dataclass-free mapping) and the UNKNOWN row both render.
    assert f"#21/run {RUN_PR} ({family} ({leg}))" in table
    assert f"#21 ({family} ({other_leg})): artifact" in table
    assert "CATCH-PR" in table and "UNKNOWN" in table


def test_every_table_row_declares_the_same_number_of_cells(tmp_path):
    """The committed table must actually BE a table: header, delimiter and rows agree.

    ⛔ GFM refuses a table whose header and delimiter rows differ in cell count (spec §4.10,
    Tables: "The header row must match the delimiter row in the number of cells. If not, a
    table will not be recognized"), and it then renders the whole thing as one paragraph of
    pipe text — checked against GitHub's own renderer (`POST /markdown`, `mode: gfm`): a
    9-cell delimiter returns `<p>` while a 10-cell one returns `<table>`.

    That is exactly what happened when the `cleared by a re-run` column was added to the
    header and to the data row but NOT to the hand-written delimiter (`04bfd156a`): the
    report #10131's acceptance calls for — "a committed report with the 74-row table" —
    rendered as prose, and no test noticed, because the existing ones assert substrings
    (`"CATCH-PR" in table`) which are present either way. This assertion is the parity that
    was missing: it reds on the 9-vs-10 delimiter with `{10, 9}`, and it holds for the
    derived delimiter introduced to fix it.
    """
    root = _world(tmp_path, runs=[])
    report = _report(root)
    table = catch.render_table(report)
    lines = table.splitlines()
    assert len(lines) >= 3, "the header, the delimiter row and at least one data row"

    header, delimiter, *_ = lines
    assert header.startswith("|") and header.endswith("|")
    assert [c.strip() for c in delimiter.strip().strip("|").split("|")] == [
        "---"
    ] * len(delimiter.strip().strip("|").split("|")), "the delimiter row is all `---`"

    # ONE cell count across header, delimiter and every data row. A single dominant count
    # means a column added to one of the three and not the others.
    counts = {line.count("|") for line in lines}
    assert len(counts) == 1, f"header/delimiter/data rows disagree on cell count: {sorted(counts)}"

    # The filter path builds the same shape (a filtered table is still a table).
    filtered = catch.render_table(report, only="nonpremerge").splitlines()
    assert len({line.count("|") for line in filtered}) == 1

    # And the REPORT — the artifact that is actually committed — carries that same table.
    rendered = catch.render_report(report)
    assert catch.render_table(report) in rendered
    assert len({line.count("|") for line in table.splitlines()}) == 1


def test_the_cli_writes_the_markdown_table_from_an_offline_fixture(tmp_path, capsys):
    """The documented invocation (`--offline-dir --md-out`) runs end to end — and the file it
    writes is the REPORT, not a bare table: the table, its window and its method together.

    ⛔ Acceptance #1 of #10131 is "a committed report with the 74-row table, its window, its
    UNKNOWNs and its method". A `--md-out` that emitted only rows would leave a reader unable
    to tell what the rows cover or how to re-derive them.
    """
    root = _world(tmp_path, runs=[])
    out = tmp_path / "catch.md"
    assert catch.main(["--offline-dir", str(root), "--md-out", str(out)]) == 0
    rendered = out.read_text(encoding="utf-8")
    assert "| file | status | lane |" in rendered
    assert rendered.count("\n") >= 74  # one row per slow file, plus the header and method
    assert "NOT-PREMERGE" in rendered  # the non-gating lane is named in the table
    # The file on disk is a TABLE, not a paragraph of pipe text: GFM refuses a table whose
    # delimiter row has a different cell count from the header (see
    # `test_every_table_row_declares_the_same_number_of_cells`), so the parity has to survive
    # the write, not just `render_table`.
    table_part = [line for line in rendered.splitlines() if line.startswith("|")]
    assert len({line.count("|") for line in table_part}) == 1
    # The window and the method travel WITH the table.
    assert "## Method" in rendered
    assert "Window in one line:" in rendered
    assert "newest **ATTEMPT** decides" in rendered
    assert "regenerate it, do not edit it" in rendered


# --- 5. a leg RUNNING is not a FILE running --------------------------------


def test_a_leg_that_ran_covers_only_the_files_its_artifact_names(tmp_path):
    """A green leg is coverage for the files it EXECUTED — not for its partition.

    ⛔ The legs are diff-SCOPED on tier-2 (`.github/workflows/python-ci.yml` builds each leg's
    `FILES` from `matrix.files` ∩ `changes.outputs.slow_selected`), so a leg that ran can have
    executed 2 of its 13 files. Counting the partition credited the whole leg and minted
    `NEVER` ("we checked and it was clean") for files that never ran — the clean bill of
    health #10131 forbids, with the false evidence printed in a rendered column.
    """
    family, leg = _leg_of("test_billing.py")
    ran_path = catch.to_observed("test_billing.py")
    not_run = sorted(LEGS[family][leg] - {ran_path})[0]
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=31)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "success")]},
        artifacts={
            # The leg executed ONE of its files — the shape a diff-scoped selection produces.
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", False)])
            )
        },
    )
    report = _report(root)

    executed = _row(report, "test_billing.py")
    assert executed["pr_runs_attested"] == 1
    assert executed["status"] == catch.NEVER  # ran, clean: that IS the answer NEVER means

    skipped = _row(report, not_run)
    # ⛔ Pre-fix these two assertions read NEVER/1: the leg's partition was credited as
    # coverage. The leg running says nothing about a file it did not touch.
    assert skipped["status"] == catch.UNKNOWN
    assert skipped["status"] != catch.NEVER
    assert skipped["pr_runs_attested"] == 0
    assert skipped["pr_leg_runs_unattested"] == 1
    # And the reason names the shape, so the UNKNOWN is actionable rather than a shrug.
    assert any("diff-scoped" in u["reason"] for u in skipped["unknown"])


def test_the_expected_nodeids_attest_a_file_whose_tests_all_skipped(tmp_path):
    """`expected-nodeids.txt` is the second per-file witness, in the same artifact zip.

    A file whose tests were all skipped/deselected has no JUnit testcase, so JUnit alone
    would read it as never executed. The leg was still SCOPED to it — the manifest the
    workflow writes is that scope — so the file is attested and reads NEVER, not UNKNOWN.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=32)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "success")]},
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip_members(
                {
                    "junit.xml": _junit([]),  # every test skipped: zero testcases
                    "expected-nodeids.txt": _nodeids(["tests/test_billing.py"]),
                }
            )
        },
    )
    row = _row(_report(root), "test_billing.py")
    assert row["pr_runs_attested"] == 1
    assert row["status"] == catch.NEVER


def test_a_green_leg_with_no_artifact_attests_nothing(tmp_path):
    """An unreadable GREEN leg is no evidence either — it may not license NEVER."""
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=33)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "success")]},
        artifacts={},  # expired past retention
    )
    row = _row(_report(root), "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    assert row["pr_runs_attested"] == 0
    # ⛔ An UNREAD artifact is not the diff-scoped shape: counting it as "the selection ran
    # other files" put a false reason in the row (and inflated that column) for a file whose
    # leg simply expired. It gets its own counter and its own wording.
    assert row["pr_leg_runs_unread"] == 1
    assert row["pr_leg_runs_unattested"] == 0
    assert any("could not be read" in u["reason"] for u in row["unknown"])
    assert not any("diff-scoped" in u["reason"] for u in row["unknown"])


# --- 6. a red a re-run cleared is inside a GREEN run ------------------------


def test_a_red_cleared_by_a_rerun_is_visible_because_green_runs_are_read(tmp_path):
    """The `flake_cleared` bucket is unreachable from a failure-only sample — and was empty live.

    A run's `conclusion` is its NEWEST attempt's, so re-running the only failing job flips the
    whole run to `success`: the red lives in a run the failure population never returns. The
    code claimed the sample was complete ("a green run is a complete answer for 'no catch
    here'"), which is exactly wrong for this shape — and the disclosure the comment promised
    was never rendered.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    green_run = _run(RUN_PR, SHA, "pull_request", pr=55, conclusion="success")
    green_run["run_attempt"] = 2  # a re-run: the only shape that can carry a cleared red
    root = _world(
        tmp_path,
        runs=[green_run],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, job, "failure"),
                _check(200, RUN_PR, job, "success"),  # the re-run
            ]
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            )
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")

    # ⛔ Pre-fix: `success` runs were never enumerated, so this row was UNKNOWN with an empty
    # `flake_cleared` — a red that happened, invisible.
    assert [c["run_id"] for c in row["flake_cleared"]] == [str(RUN_PR)]
    assert row["status"] == catch.NEVER
    assert report["coverage"]["reds_cleared_by_a_rerun"] == 1
    window = report["window"]
    assert window["success_runs"] == 1
    assert window["rerun_success_runs"] == 1
    assert "success-conclusion runs: 1 read (1 of them re-runs" in catch.render_summary(report)


def test_a_cleared_red_whose_attempt_artifact_is_gone_is_unknown_not_never(tmp_path):
    """The LIVE shape of a cleared red, and the one that used to be dropped silently.

    GitHub's artifact listing is PER RUN (`actions/runs/<id>/artifacts`, no attempt parameter),
    so after the only failing job is re-run green the artifact at `pytest-log-<job>-<leg>`
    holds that PASSING attempt — its JUnit names no failure. The cleared-red loop read it,
    found no failing path, and `continue`d: no citation, no UNKNOWN, nothing. The file then
    read `NEVER` ("we ran it and it caught nothing") for a leg that went RED on that very PR
    head, while `coverage.reds_cleared_by_a_rerun` counted the red just discarded.

    ⛔ The assertion that bites is `status != NEVER`: pre-fix this row is NEVER, because the
    green run attests the file executed and nothing recorded the red.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    green_run = _run(RUN_PR, SHA, "pull_request", pr=56, conclusion="success")
    green_run["run_attempt"] = 2
    root = _world(
        tmp_path,
        runs=[green_run],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, job, "failure"),
                _check(200, RUN_PR, job, "success"),  # the re-run that replaced the artifact
            ]
        },
        artifacts={
            # The surviving artifact is the PASSING attempt: no failure to attribute.
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", False)])
            )
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")
    assert row["status"] != catch.NEVER, "a red on this PR head is not a clean bill"
    assert row["status"] == catch.UNKNOWN
    assert row["flake_cleared"] == []
    assert any("re-measured" in u["reason"] for u in row["unknown"])
    # The red is still ACCOUNTED FOR (never silently dropped), and it is not a catch either.
    assert report["coverage"]["reds_cleared_by_a_rerun"] == 1
    assert row["pr_head_catches"] == []


def test_an_archived_runs_listing_shorter_than_its_total_count_is_read_not_crashed(tmp_path):
    """An archive of a `--paginate` read is shorter than `total_count` — and that is normal.

    In an `actions/runs` listing `total_count` is the POPULATION's size; the service refuses
    page >10, so a busy window's archived page set is always short. The live client reads that
    payload happily (its pager never consults the total), so the offline path must not refuse
    it — and it must not escape as a `SurfaceReadError` traceback + exit 1 either, which is
    neither of the two documented exits.
    """
    root = _world(tmp_path, runs=[_run(RUN_PR, SHA, "pull_request", pr=5)])
    (root / "runs.json").write_text(
        json.dumps(
            {"total_count": 2500, "workflow_runs": [_run(RUN_PR, SHA, "pull_request", pr=5)]}
        ),
        encoding="utf-8",
    )
    assert catch.main(["--offline-dir", str(root), "--summary"]) == 0
    report = _report(root)
    assert report["window"]["populations"]["pull_request/failure"]["read"] == 1


def test_a_truncated_offline_jobs_listing_is_a_read_failure_not_a_traceback(tmp_path, capsys):
    """A short `jobs/<run_id>.json` is a READ failure: reported, never a traceback.

    ⛔ Unlike the runs listing, one run's job listing has `total_count` describing exactly that
    read, so a short page set means the archive is incomplete and the attempt reduction would
    run on a partial surface. Before the fix this escaped as an uncaught `SurfaceReadError` — a
    `RuntimeError` `main` did not catch — i.e. a traceback + exit 1, which is neither documented
    exit and reads as a crash rather than an UNKNOWN.

    The run-level read failure is the one `build_history` already handles: the run's slow legs
    become UNKNOWN with an `api_notes` entry, and the report still produces (exit 0). A whole
    listing that cannot be parsed is the exit-2 case (below), where no report is possible.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=5)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
    )
    (root / "jobs" / f"{RUN_PR}.json").write_text(
        json.dumps(
            {"total_count": 99, "jobs": [_check(100, RUN_PR, f"{family} ({leg})", "failure")]}
        ),
        encoding="utf-8",
    )
    assert catch.main(["--offline-dir", str(root), "--summary"]) == 0
    printed = capsys.readouterr().out
    assert "jobs for run 1001 unreadable" in printed
    assert "TRUNCATED" in printed
    _row(_report(root), "test_billing.py")  # the table is still produced
    assert _row(_report(root), "test_billing.py")["status"] == catch.UNKNOWN


def test_an_unparseable_offline_listing_exits_2(tmp_path, capsys):
    """A payload set that cannot be decoded is exit 2 — the documented "could not read" code."""
    root = _world(tmp_path, runs=[_run(RUN_PR, SHA, "pull_request", pr=5)])
    (root / "runs.json").write_text("{not json at all", encoding="utf-8")
    assert catch.main(["--offline-dir", str(root), "--summary"]) == 2
    assert "could not read a surface" in capsys.readouterr().err


def test_a_runs_file_that_is_not_a_runs_listing_is_a_read_failure_not_an_empty_window(tmp_path):
    """Dropping the `total_count` refusal must not drop the SHAPE witness with it.

    `total_count` is the POPULATION's size (and the service caps at page 10), so an archived
    busy-window page set is legitimately short — but an API error body (`{"message": …}`) or a
    mistyped `workflow_runs` is not an empty window. Reading it as one would exit 0 with
    "the report was produced" for a surface that was never read at all.
    """
    for label, payload in (
        ("error-body", {"message": "Not Found"}),
        ("total-only", {"total_count": 2500}),
        ("wrong-type", {"total_count": 3, "workflow_runs": {"a": 1}}),
    ):
        root = _world(tmp_path / label, runs=[_run(RUN_PR, SHA, "pull_request", pr=5)])
        (root / "runs.json").write_text(json.dumps(payload), encoding="utf-8")
        assert catch.main(["--offline-dir", str(root), "--summary"]) == 2, label


def test_a_budget_exhausted_artifact_listing_does_not_claim_expiry(tmp_path):
    """An unread listing is not an expired artifact — the row reason must say which it is.

    ⛔ `GhClient.artifacts` swallowed `BudgetExhausted` and returned `{}`, so the listing note
    was unreachable and every red leg of that run read UNKNOWN with "expired past the repo's
    N-day retention" — the report asserting an artifact is gone when it was never looked up.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=57)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
    )
    client = catch.OfflineClient(root)

    def _broke(run_id: str) -> dict:
        raise catch.BudgetExhausted("API-call bound reached (1)")

    client.artifacts = _broke  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    reason = row["unknown"][0]["reason"]
    assert "retention" not in reason, "the artifact was never looked up — it is not expired"
    assert "budget exhausted" in reason.lower()
    assert any("budget exhausted" in note for note in report["window"]["api_notes"])

    # The same rule one level down: the LISTING was read, the DOWNLOAD was not. The row must
    # not claim expiry for bytes that were never fetched either.
    downloaded = catch.OfflineClient(root)
    downloaded.artifacts = lambda run_id: {  # type: ignore[method-assign]
        catch.artifact_name(family, leg): {"name": catch.artifact_name(family, leg), "id": "x"}
    }

    def _no_download(artifact_id: str) -> bytes:
        raise catch.BudgetExhausted("API-call bound reached (2)")

    downloaded.artifact_zip = _no_download  # type: ignore[method-assign]
    blocked = _row(catch.build_history(MANIFEST, downloaded), "test_billing.py")
    assert blocked["status"] == catch.UNKNOWN
    assert "retention" not in blocked["unknown"][0]["reason"]
    assert "could not be READ" in blocked["unknown"][0]["reason"]


def test_a_job_retried_many_times_marks_the_leg_unknown_once(tmp_path):
    """A retried job yields one cleared attempt per retry — and ONE UNKNOWN block per row."""
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    green_run = _run(RUN_PR, SHA, "pull_request", pr=58, conclusion="success")
    green_run["run_attempt"] = 4
    root = _world(
        tmp_path,
        runs=[green_run],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, job, "failure"),
                _check(150, RUN_PR, job, "failure"),
                _check(180, RUN_PR, job, "failure"),
                _check(200, RUN_PR, job, "success"),
            ]
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", False)])
            )
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    assert len(row["unknown"]) == 1, "one block per leg, not one per retried attempt"
    # Every cleared red is still COUNTED even though only one block is written.
    assert report["coverage"]["reds_cleared_by_a_rerun"] == 3


# --- 7. a tree that cannot answer the lane question fails closed ------------


def test_a_tree_without_the_subtree_lane_directories_fails_closed(tmp_path, monkeypatch):
    """The non-gating lane is derived from DISK — a tree without it must not be reported.

    `on_demand` carries subtree entries (`eval/**`, `longmem_eval/**`) that
    `ci_selection.on_demand_subtree_files` expands by globbing `tests/<prefix>`. A `tools/` +
    `config/`-only extraction therefore derives an EMPTY non-gating lane and every eval suite
    reads as a gating leg — a confidently wrong partition. (That exact state produced a
    review finding that the five eval rows could not exist at all.) Fail closed instead.
    """
    healthy = _world(tmp_path / "healthy", runs=[])
    assert catch.dead_subtree_prefixes(MANIFEST) == ()  # the real tree answers the question

    monkeypatch.setattr(ci_selection, "TESTS_DIR", tmp_path / "no-such-tests-dir")
    dead = catch.dead_subtree_prefixes(MANIFEST)
    assert dead, "a tree with no tests/ must be named"
    with pytest.raises(catch.ReadError) as excinfo:
        catch.build_history(MANIFEST, catch.OfflineClient(healthy))
    assert "on_demand" in str(excinfo.value)
    # And the CLI surfaces it as its documented exit 2 (a read failure, not a report).
    assert catch.main(["--offline-dir", str(healthy)]) == 2


# --- coverage arithmetic ---------------------------------------------------


def test_coverage_counts_reconcile_and_never_extrapolate(tmp_path):
    """Every one of the 74 lands in exactly one bucket, and the summary says so."""
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=9)],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, f"{family} ({leg})", "failure"),
                _check(101, RUN_PR, f"{family} (zz)", "success"),  # a leg we do not run
            ]
        },
        artifacts={},
    )
    report = _report(root)
    coverage = report["coverage"]
    statuses = (catch.CATCH_PR, catch.CATCH_PUSH_ONLY, catch.NEVER, catch.UNKNOWN, catch.NOT_PREMERGE)
    total = sum(coverage[f"status_{key}"] for key in statuses)
    assert total == 74
    # The leg's files carry the artifact reason; every other row is UNKNOWN for the OTHER
    # reason (nothing measured it) — the two are never collapsed into one number.
    attributed_unknown = [
        row for row in report["files"] if any("artifact" in u["reason"] for u in row["unknown"])
    ]
    assert len(attributed_unknown) == len(LEGS[family][leg])
    assert "UNKNOWN (not extrapolated)" in catch.render_summary(report)
    assert "not run pre-merge (non-gating lane)" in catch.render_summary(report)


def test_a_sha_whose_check_surface_is_unreadable_is_not_a_clean_sha(tmp_path):
    """A read failure is not "no reds": it makes the legs that could have run UNKNOWN."""
    root = _world(tmp_path, runs=[_run(RUN_PR, SHA, "pull_request", pr=11)])
    report = _report(root)
    measured_lane = report["coverage"]["slow_files"] - report["coverage"][
        "lane_" + catch.LANE_NON_GATING
    ]
    # ⛔ NEVER would be the fail-open reading of a failed READ.
    assert report["coverage"]["status_" + catch.UNKNOWN] == measured_lane
    assert report["coverage"]["status_" + catch.NEVER] == 0
    # The non-gating lane is unaffected by a PR-head read failure: it is not run there.
    assert report["coverage"]["status_" + catch.NOT_PREMERGE] == report["coverage"][
        "lane_" + catch.LANE_NON_GATING
    ]
    assert any("unreadable" in note for note in report["window"]["api_notes"])


# --- the window is named, not implied --------------------------------------


def test_retention_caps_the_window_and_is_reported(tmp_path):
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=1)],
        jobs={RUN_PR: [_check(100, RUN_PR, "test-slow (a)", "success")]},
        retention=30,
    )
    window = _report(root)["window"]
    assert window["artifact_retention_days"] == 30
    assert window["effective_days"] == 30
    assert window["requested_days"] == catch.DEFAULT_DAYS


def test_the_registered_test_file_is_classified(tmp_path):
    """`--integrity` needs this file in a surface; the manifest is the source of truth.

    Without the `core` registration `ci_selection --integrity` exits non-zero, so this
    asserts the registration this PR adds rather than a property of this module.
    """
    assert ci_selection.classify_test_file("test_ci_catch_history.py", MANIFEST) is not None


# --- the wall bound bounds the LISTING, not just the detail loop -------------


class _Stopwatch:
    """A monotonic clock that ADVANCES on every read.

    A wall bound is otherwise untestable without sleeping for it — and a test that sleeps
    is a test nobody runs. Every `monotonic()` read spends `step` seconds, so the bound
    trips at a deterministic point in the walk.
    """

    def __init__(self, step: float) -> None:
        self.step = step
        self.elapsed = 0.0

    def monotonic(self) -> float:
        self.elapsed += self.step
        return self.elapsed


class _ListingRecorder(catch.OfflineClient):
    """An offline client that records WHICH populations were listed."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.listed: list[tuple[str, str | None, str]] = []

    def list_runs(self, event, branch, conclusion, limit):
        self.listed.append((event, branch, conclusion))
        return super().list_runs(event, branch, conclusion, limit)


def test_a_wall_bound_that_expires_while_listing_stops_and_says_so(tmp_path, monkeypatch):
    """⛔ The bound must bound the LISTING too — a measured 350 s for 120 s of bound.

    On this fleet a live walk spent ~350 s of wall clock listing six populations (10 API
    calls) before the DETAIL loop ran its first `monotonic()` check — so `--max-seconds 120`
    ran to 350 s and then reported "wall-clock bound reached (120s)" as though it had
    stopped on time. The bound is now checked per population, the populations that were
    never listed are NAMED, and their absence can only leave files UNKNOWN — never NEVER.
    """
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=7)],
        jobs={RUN_PR: [_check(100, RUN_PR, "test-slow (a)", "failure")]},
        artifacts={(RUN_PR, catch.artifact_name("test-slow", "a")): _zip(_junit([("tests/test_billing.py", "t", True)]))},
    )
    client = _ListingRecorder(root)
    clock = _Stopwatch(step=100.0)
    monkeypatch.setattr(catch, "time", SimpleNamespace(monotonic=clock.monotonic))
    report = catch.build_history(MANIFEST, client, max_seconds=120)

    # The listing STOPPED: pre-fix it read all six populations (the bound was only consulted
    # in the detail loop, which never ran), so this is the discriminating assertion.
    assert len(client.listed) == 1, f"the bound did not stop the listing: {client.listed}"
    window = report["window"]
    populations = window["populations"]
    assert len(populations) == 6, "every population must be accounted for, read or not"
    unlisted = [key for key, info in populations.items() if info["listed"] is False]
    assert len(unlisted) == 5
    assert all(info["read"] == 0 for key, info in populations.items() if key in unlisted)

    # The truncation NAMES the elapsed time and the populations it never read — a disclosure
    # that reads "bound reached" while 350 s had already elapsed is not a disclosure.
    truncation = window["truncation"]
    assert truncation and "while LISTING" in truncation
    assert "s elapsed" in truncation
    assert "push-main/failure" in truncation and "pull_request/success" in truncation

    # And the consequence: nothing was attested, so no file may claim a clean bill of health.
    assert any(row["status"] == catch.UNKNOWN for row in report["files"])
    assert not [row for row in report["files"] if row["status"] == catch.NEVER]


# --- 9. NEVER is licensed by a PR-HEAD attestation only ---------------------


def test_a_push_only_clean_run_does_not_license_never(tmp_path):
    """A file attested clean on a MAIN PUSH but never run on a PR head is UNKNOWN.

    ⛔ `pr_runs_attested or push_runs_attested` licensed `NEVER` from either population, so
    "never ran on a PR head at all" and "ran pre-merge and was clean" collapsed into one
    bucket. The populations are not interchangeable: every push runs the WHOLE gating pool
    while the PR legs are diff-scoped, so a push attestation says nothing about a PR head —
    and `NEVER` here is exactly the clean bill of health a `slow_files` reopen would argue
    from.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PUSH, SHA, "push")],
        jobs={RUN_PUSH: [_check(100, RUN_PUSH, f"{family} ({leg})", "success")]},
        artifacts={
            (RUN_PUSH, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", False)])
            )
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")
    assert row["push_runs_attested"] == 1 and row["pr_runs_attested"] == 0
    assert row["status"] != catch.NEVER, "a push attestation is not PR-head evidence"
    assert row["status"] == catch.UNKNOWN
    assert any("NO PR head" in u["reason"] for u in row["unknown"])
    assert report["coverage"]["status_" + catch.NEVER] == 0


# --- 10. the live transport: a cut listing is REPORTED ----------------------


def _pager(*, fail_page: int, per_page: int = 100) -> catch.GhClient:
    """A `GhClient` whose `_gh` serves `per_page` runs per page and fails from `fail_page`."""

    client = catch.GhClient("o/r", max_calls=10_000)

    def _gh(args, binary=False):
        page = int(args[1].rsplit("page=", 1)[1])
        if page >= fail_page:
            raise catch.ReadError(
                "gh api repos/o/r/actions/runs failed (rc=1): check your internet connection"
            )
        return json.dumps(
            {
                "total_count": 1000,
                "workflow_runs": [
                    _run(page * 1000 + i, SHA, "pull_request", pr=1) for i in range(per_page)
                ],
            }
        )

    client._gh = _gh  # type: ignore[method-assign]
    return client


def test_a_page_two_read_failure_is_a_reported_truncation_not_a_smaller_population():
    """`_gh` has already spent every retry, so a page>1 failure is PERSISTENT — not the end.

    ⛔ Pre-fix the branch was `if page == 1: raise` / `break` with nothing recorded, so
    `window.populations` reported `read: 100, requested: 500` as an ordinary read under a
    caption that only flags the at-requested case — inviting the reader to conclude 100 was
    the population's size. Fewer runs cannot mint a false `NEVER` (coverage is per FILE and
    attested), but they under-report `CATCH-PR`, which is the direction that argues for
    taking a file off the pre-merge gate.
    """
    client = _pager(fail_page=2)
    runs = client.list_runs("pull_request", None, "failure", 500)
    assert len(runs) == 100, "the pages that WERE read are kept"
    assert client.truncation is not None, "the cut was silent"
    assert "pull_request/failure" in client.truncation
    assert "page 1" in client.truncation
    assert "internet connection" in client.truncation


def test_a_cut_listing_reaches_the_report_window(tmp_path):
    """The truncation travels: `window["truncation"]` is what `render_summary` prints."""
    seed = _world(tmp_path, runs=[])
    client = catch.OfflineClient(seed)
    pager = _pager(fail_page=2)

    def _list_runs(event, branch, conclusion, limit):
        runs = pager.list_runs(event, branch, conclusion, limit)
        client.truncation = pager.truncation
        return runs

    client.list_runs = _list_runs  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)
    truncation = report["window"]["truncation"]
    # ⛔ EVERY cut, not just the last: `truncation` was a single field, so six cut populations
    # reported only the sixth and the other five vanished.
    assert truncation and "pull_request/failure" in truncation and "push/success" in truncation
    # Every population whose requested count exceeds one page was cut, and each cut is named
    # (the `cancelled` populations ask for 60, so page 1 IS their whole read).
    long_reads = [v for v in report["window"]["populations"].values() if v["requested"] > 100]
    assert long_reads
    assert truncation.count("listing stopped after page 1") == len(long_reads)
    assert "TRUNCATED" in catch.render_summary(report)


# --- 11. one bad READ of a run must not discard the walk --------------------


def test_an_unreadable_artifact_listing_degrades_to_unknown_instead_of_aborting(tmp_path):
    """`RunArtifacts.get` caught only `BudgetExhausted`, so a `ReadError` escaped `build_history`.

    The same run's JOBS read already degrades to `api_notes` + UNKNOWN, and so does the
    DOWNLOAD one level below. Only the listing was fatal — `main` exited 2 with no report at
    all, discarding a walk bounded at 1500 s, which is the outcome `GhClient.RETRIES` exists
    to prevent.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=61)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
    )
    client = catch.OfflineClient(root)

    def _unreadable(run_id: str) -> dict:
        raise catch.ReadError("gh api repos/o/r/actions/runs/1001/artifacts failed (rc=1): boom")

    client.artifacts = _unreadable  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)  # pre-fix: raises out of build_history
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    assert any("unreadable" in note for note in report["window"]["api_notes"])
    reason = row["unknown"][0]["reason"]
    assert "the listing could not be read" in reason
    assert "retention" not in reason, "the artifact may exist — the listing was never read"


def test_a_failed_download_is_not_quoted_as_another_legs_reason(tmp_path):
    """`download_failed` was per RUN, so leg (a)'s failure was printed as leg (b)'s reason.

    ⛔ The reproduced shape: row (b) read *"artifact pytest-log-test-slow-b is absent or
    unreadable … it could not be READ (artifact pytest-log-test-slow-a could not be
    downloaded: boom)"* — two contradictory statements in one sentence, in the field a
    reviewer auditing the rows follows.
    """
    first, second = sorted(LEGS[catch.JOB_SLOW])[:2]
    a_path = sorted(LEGS[catch.JOB_SLOW][first])[0]
    b_path = sorted(LEGS[catch.JOB_SLOW][second])[0]
    a_name = catch.artifact_name(catch.JOB_SLOW, first)
    b_name = catch.artifact_name(catch.JOB_SLOW, second)
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=62)],
        jobs={
            RUN_PR: [
                _check(100, RUN_PR, f"{catch.JOB_SLOW} ({first})", "failure"),
                _check(200, RUN_PR, f"{catch.JOB_SLOW} ({second})", "failure"),
            ]
        },
        # Leg (a)'s artifact IS in the listing (so its download is attempted and fails);
        # leg (b)'s is simply absent — the expired/never-produced shape.
        artifacts={(RUN_PR, a_name): _zip(_junit([(a_path, "t", True)]))},
    )
    client = catch.OfflineClient(root)
    real = client.artifact_zip

    def _broke(artifact_id):
        if str(artifact_id) == a_name:
            raise catch.ReadError("boom")
        return real(artifact_id)

    client.artifact_zip = _broke  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)
    index = catch.manifest_index(MANIFEST)

    def _reasons(row: dict) -> str:
        return " ".join(u["reason"] for u in row["unknown"])

    row_a, row_b = _row(report, index[a_path]), _row(report, index[b_path])
    assert row_a["status"] == catch.UNKNOWN and row_b["status"] == catch.UNKNOWN
    assert a_name in _reasons(row_a), "the leg that failed must name its own artifact"
    assert a_name not in _reasons(row_b), "another leg's failure is not this row's reason"
    assert b_name in _reasons(row_b)


# --- 12. the transport is bounded per call, and its exits are documented -----


def test_a_stalled_gh_call_is_bounded_by_a_timeout(monkeypatch):
    """`--max-seconds` is consulted BETWEEN calls, so one stalled `gh` blocks the walk forever."""
    seen: list[float | None] = []

    def _timeout(cmd, capture_output=False, timeout=None):
        seen.append(timeout)
        raise catch.subprocess.TimeoutExpired(cmd, timeout or 0)

    monkeypatch.setattr(catch.subprocess, "run", _timeout)
    monkeypatch.setattr(catch.time, "sleep", lambda _s: None)
    client = catch.GhClient("o/r", max_calls=100)
    with pytest.raises(catch.ReadError) as excinfo:
        client._gh(["api", "repos/o/r/actions/runs"])
    assert len(seen) == catch.GhClient.RETRIES, "a timeout is a failed ATTEMPT, not a wait"
    assert all(t for t in seen), "subprocess.run was called with no timeout"
    assert "timed out" in str(excinfo.value)


def test_an_exhausted_budget_leaves_retention_unknown_instead_of_escaping():
    """`BudgetExhausted` is a bare RuntimeError and escaped `main`'s handler as a traceback."""
    client = catch.GhClient("o/r", max_calls=1)

    def _spent(args, binary=False):
        raise catch.BudgetExhausted("API-call bound reached (1)")

    client._gh = _spent  # type: ignore[method-assign]
    assert client.retention_days() is None


def test_a_numeric_string_check_id_does_not_mint_a_phantom_superseded_red():
    """`-1` for a non-int id made `attempt.check_id < latest.check_id` unconditionally true.

    The Jobs API returns integers, so this is low-reachability — but it is the one place this
    module and `ci_verdict._as_int` could disagree, and the disagreement emits a red NEWER
    than its "clearing" attempt as `flake_cleared` (a fabricated clean re-measurement).
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    red = _check("900", RUN_PR, job, "failure")  # the id arrives as a STRING
    green = _check(600, RUN_PR, job, "success")

    attempts = catch._family_attempts([red])
    assert attempts and attempts[0][0].check_id == 900, "a numeric string is not -1"

    groups = catch.reduce_attempts([red, green], {str(RUN_PR): ("111", "python-ci")})
    # 900 is the NEWEST attempt, so the group is RED and nothing is superseded. Pre-fix the
    # red carried -1, matched no group and was re-emitted as a phantom superseded leftover.
    assert len(groups) == 1
    assert groups[0].latest is not None and groups[0].latest.state == "red"
    assert groups[0].superseded == ()


# --- 13. the lane split is DERIVED, not a constant --------------------------


def test_the_lane_split_follows_ci_selection_instead_of_a_constant(tmp_path, monkeypatch):
    """Shift one file between lanes and every row moves with it.

    ⛔ The 74-row test compares `report[...]` against `catch.slow_lane()`, so a hardcoded dict
    equal to today's split satisfies every one of its assertions — the property #10194 already
    broke once was still only pinned against a stale constant.
    """
    moved = catch.to_observed("test_billing.py")
    real_slow = ci_selection.slow_shard_entries
    real_on_demand = ci_selection.on_demand_files

    def _slow(manifest):
        return [
            {
                **entry,
                "files": [f for f in entry["files"] if catch.to_observed(str(f)) != moved],
            }
            for entry in real_slow(manifest)
        ]

    monkeypatch.setattr(ci_selection, "slow_shard_entries", _slow)
    monkeypatch.setattr(
        ci_selection,
        "on_demand_files",
        lambda manifest: set(real_on_demand(manifest)) | {"test_billing.py"},
    )
    assert catch.slow_lane(MANIFEST)[moved] == catch.LANE_NON_GATING
    report = _report(_world(tmp_path, runs=[]))
    row = _row(report, "test_billing.py")
    assert row["lane"] == catch.LANE_NON_GATING
    assert row["status"] == catch.NOT_PREMERGE, "a by-decision exclusion, not a gap"
