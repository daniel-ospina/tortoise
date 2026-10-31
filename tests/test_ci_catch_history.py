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
   `test_two_workflows_sharing_a_job_name_...` fails on a name-only grouping.
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

Everything is offline: the fixtures are the same payloads a live run reads, archived
(`runs.json`, `check-runs/<sha>.json`, `artifacts/<run_id>/<name>.zip`), so these tests also
pin the reviewer-reproducible path.
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

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


# --- 2. the group key is (app, workflow, job) ------------------------------


def test_two_workflows_sharing_a_job_name_do_not_mask_each_other(tmp_path):
    """`test-slow (b)` from workflow 111 (red) and workflow 222 (green) are DIFFERENT groups.

    This is the AGENTS.md failure mode: a name-keyed grouping takes the newer green of the
    other workflow and reports the commit clean, hiding a real red on a PR head.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PR, SHA, "pull_request", pr=88, workflow_id=111),
            _run(RUN_OTHER_WORKFLOW, SHA, "pull_request", pr=88, workflow_id=222),
        ],
        jobs={
            RUN_PR: [_check(100, RUN_PR, job, "failure")],
            # A DIFFERENT run (another workflow) whose same-named job is green: it must not
            # supersede this run's red, and a name-keyed rollup across runs would.
            RUN_OTHER_WORKFLOW: [_check(900, RUN_OTHER_WORKFLOW, job, "success")],
        },
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([("tests/test_billing.py", "t", True)])),
        },
    )
    row = _row(_report(root), "test_billing.py")
    # ⛔ Fails on a name-only grouping: the newer `success` (id 900) would win and mask it.
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
    assert len(report["files"]) == 74
    assert len(catch.manifest_index(MANIFEST)) == 74

    lanes = catch.slow_lane(MANIFEST)
    slow_legs = {f for legs in LEGS[catch.JOB_SLOW].values() for f in legs}
    carve_legs = {f for legs in LEGS[catch.JOB_CARVE].values() for f in legs}
    non_gating = {catch.to_observed(f) for f in ci_selection.on_demand_files(MANIFEST)}

    assert len(lanes) == 74
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
