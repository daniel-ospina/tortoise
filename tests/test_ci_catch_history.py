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
9. **The markdown report IS a table** — GFM refuses a table whose header and delimiter rows
   differ in cell count, and a cell carrying a newline (an UNKNOWN reason embeds `gh`'s
   stderr) ends its row early. `test_every_markdown_row_has_the_same_number_of_cells` and
   `test_an_unknown_reason_cannot_break_the_row_it_is_rendered_in` fail if either returns,
   which is the difference between #10131's acceptance artifact and 74 rows of pipe text.
10. **EVERY cut of the sample is kept, including the last one** — `window.truncation` is the
    paragraph a reader uses to judge how much of the sample the numbers rest on, so a cut may
    never erase one recorded before it, and a population the API bound shortened must say so.
    `test_a_budget_cut_keeps_the_cuts_recorded_before_it` and
    `test_a_budget_cut_while_listing_names_the_population_it_cut` fail on the `_spend`
    ASSIGNMENT (a budget cut wrote the field directly, erasing every earlier cut).
11. **An EXCLUSIVE label needs a sample without gaps** — `CATCH-PUSH-ONLY` says "only on
    main", which a recorded PR-head gap cannot support (the same asymmetry `NEVER` forbids for
    a push attestation), and an artifact that was downloaded and opened as a valid zip cannot
    be reported as "expired or never produced".
    `test_a_push_only_catch_over_a_pr_head_gap_is_not_called_only_on_main`,
    `test_a_green_pr_leg_with_an_unreadable_artifact_withholds_only_on_main` and
    `test_a_zip_that_lacks_the_members_is_a_READ_gap_not_an_expired_artifact` fail if either
    reading returns. ⛔ The second one is the shape the first does NOT cover: only a RED leg
    creates an `Unknown`, so a GREEN PR leg whose artifact expired stated the exclusive claim
    while its PR-head side had never been read — the documented common shape live.
    ⛔ **A gap the population never sampled outranks every other gap** — the rule is stated
    generally here and the two shapes above are only the ones that had been OBSERVED. A row
    with a push catch and NO PR run of any kind (nothing attested, nothing unread, nothing
    unattested) has no PR-head sample at all, so it cannot state an exclusive claim about PR
    heads; `test_a_push_only_red_with_no_pr_run_at_all_is_not_called_only_on_main` fails if the
    label comes back, and `test_a_push_only_red_is_catch_push_only` is the genuinely-sampled
    counterpart that must KEEP it.
12. **A bound (or transport) failure while LISTING cuts the SAMPLE, never the REPORT** —
    `list_runs` re-raises a page-1 failure, and that error reached `main`, which exited 2
    before writing `--json-out`/`--md-out`: the one state `--max-seconds` exists for produced
    no artifact at all, instead of the truncated report `listing_stop` discloses.
    `test_a_walk_bound_while_listing_cuts_the_sample_instead_of_discarding_the_report`,
    `test_the_walk_bound_while_listing_still_writes_the_report` and
    `test_a_page_two_prose_page_truncates_the_listing_instead_of_discarding_the_walk` fail if
    the listing call is unguarded (each raises, or loses its report, where it expects one).
    ⛔ The rule is PAGE-GUARDED, and it covers every cut spelling in `list_runs` — the API-call
    bound, the transport failure, a page carrying no `workflow_runs`, and an UNDECODABLE page
    (that last one was the only unguarded raise, so a malformed page 2 discarded a walk that
    an empty page 2 merely truncated). Page 1 is the exception for all four: nothing was
    read, so the population is unusable and `main` must exit 2.
13. **Both artifacts are WRITTEN inside the documented-exit handler** — `--json-out` and
    `--md-out` are the reason the walk runs, and they sat outside the `except OSError` that
    covers the reads, so an unwritable path exited 1 as a traceback after a ~1500 s walk
    instead of the documented 2. `test_an_unwritable_output_path_is_the_documented_exit_2_not_a_traceback`
    fails if the writes leave it.
14. **An artifact is identified by `(run_id, name)`, never by name alone** — the live listing is
    run-scoped and every run of a leg uploads the SAME name
    (`pytest-log-<job>-<leg>`), so a name-only key is ambiguous in any archive holding two
    runs of a leg — which is every real one. The offline reader dropped the run scope and
    resolved the name by globbing every run directory, so one run read another run's bytes:
    a green run read a red run's zip (a `CATCH-PR` citation attributed to the WRONG run), a
    red run read a green run's zip (a real red downgraded to UNKNOWN).
    `test_the_offline_client_reads_an_artifact_from_the_run_that_listed_it` and
    `test_a_pr_row_is_attested_from_the_pr_runs_own_artifact_not_the_push_runs` fail if the
    id stops carrying the run.

Everything is offline: the fixtures are the same payloads a live run reads, archived
(`runs.json`, `jobs/<run_id>.json`, `artifacts/<run_id>/<name>.zip`), so these tests also
pin the reviewer-reproducible path.
"""
from __future__ import annotations

import io
import json
import re
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


# --- 3b. an artifact is identified by (run_id, name), not by name --------


def test_the_offline_client_reads_an_artifact_from_the_run_that_listed_it(tmp_path):
    """Two runs, one leg name, different bytes: each read must return ITS run's artifact.

    ⛔ `OfflineClient.artifacts()` reduced the listing to `{path.stem: ...}`, throwing away
    the run it had just scoped to, and `artifact_zip()` then resolved that bare name with
    `(root / "artifacts").glob(f"*/{name}.zip")` — the FIRST match in `scandir` order,
    across every run. `python-ci.yml` names every run's leg artifact identically
    (`pytest-log-<job>-<leg>`, which is why `artifact_name()` exists), so *any* archive with
    two runs of a leg — i.e. every real one — read the wrong run's bytes.

    Both reads are asserted, and that is what makes this order-independent: pre-fix both
    listings hand out the SAME id string, so one call cannot return two different payloads
    and at most one of the two assertions can hold, whichever zip the glob happened to hit.
    """
    family, leg = _leg_of("test_billing.py")
    name = catch.artifact_name(family, leg)
    a_path = catch.to_observed("test_billing.py")
    b_path = sorted(LEGS[family][leg] - {a_path})[0]
    push_zip = _zip(_junit([(a_path, "t", True)]))
    pr_zip = _zip(_junit([(b_path, "t", False)]))
    root = _world(
        tmp_path,
        runs=[],
        artifacts={(RUN_PUSH, name): push_zip, (RUN_PR, name): pr_zip},
    )
    client = catch.OfflineClient(root)

    push_id = client.artifacts(str(RUN_PUSH))[name]["id"]
    pr_id = client.artifacts(str(RUN_PR))[name]["id"]
    assert push_id != pr_id, "the id must carry the run: the name alone is ambiguous"
    assert client.artifact_zip(push_id) == push_zip
    assert client.artifact_zip(pr_id) == pr_zip

    # A bare name is REFUSED rather than resolved — accepting it can only guess a run.
    with pytest.raises(catch.ReadError, match="not run-scoped"):
        client.artifact_zip(name)
    # And a run that never uploaded it is a READ failure, not the other run's bytes.
    with pytest.raises(catch.ReadError, match="no artifact"):
        client.artifact_zip(f"{RUN_OTHER_WORKFLOW}/{name}")


def test_a_pr_row_is_attested_from_the_pr_runs_own_artifact_not_the_push_runs(tmp_path):
    """The end-to-end consequence: push evidence must not become a PR-head attestation.

    ⛔ Measured pre-fix on this exact shape: the file executed ONLY on the push run came out
    `pr_runs_attested=1`, `status=NEVER` — a clean bill of health on a PR head minted purely
    from push evidence, which is the route `status`'s NEVER branch forbids — while the file
    the PR artifact DID name read UNKNOWN for "its artifact named none of it". Two runs of
    one leg share `pytest-log-<job>-<leg>`, so the PR leg read the push run's zip.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    name = catch.artifact_name(family, leg)
    a_path = catch.to_observed("test_billing.py")
    b_path = sorted(LEGS[family][leg] - {a_path})[0]
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PUSH, SHA, "push"),
            _run(RUN_PR, SHA, "pull_request", pr=77, conclusion="success"),
        ],
        jobs={
            RUN_PUSH: [_check(100, RUN_PUSH, job, "failure")],
            RUN_PR: [_check(200, RUN_PR, job, "success")],
        },
        # The SAME artifact name in BOTH runs, which is the real upload shape.
        artifacts={
            (RUN_PUSH, name): _zip(_junit([(a_path, "t", True)])),
            (RUN_PR, name): _zip(_junit([(b_path, "t", False)])),
        },
    )
    report = _report(root)
    a_row = _row(report, a_path)
    b_row = _row(report, b_path)

    # `a` was executed only on the push run: the PR leg's own artifact names `b`, so there
    # is no PR-head attestation — the diff-scoped selection never EXECUTED `a` pre-merge, so
    # "only on main" would be an EXCLUSIVE claim about a file the PR sample never measured.
    # Its main red still survives, cited: only the exclusive WORDING is withheld.
    assert a_row["pr_runs_attested"] == 0, "a push artifact is not PR-head evidence"
    assert a_row["status"] == catch.UNKNOWN, a_row["status"]
    assert a_row["status"] != catch.NEVER
    assert [c["run_id"] for c in a_row["push_main_catches"]] == [str(RUN_PUSH)], (
        "the main catch is still cited — only the exclusive wording is withheld"
    )
    # `b` IS attested — by the PR run that actually uploaded it.
    assert b_row["pr_runs_attested"] == 1, "the PR run's own artifact names this file"


# --- attribution through the job's own JUnit ------------------------------


def test_a_push_only_red_is_catch_push_only(tmp_path):
    """The GENUINELY-SAMPLED shape: the file was measured on a PR head and caught nothing.

    ⛔ This fixture used to carry `runs=[_run(RUN_PUSH, SHA, "push")]` — NO PR run at all —
    and still asserted `CATCH_PUSH_ONLY`. It therefore PINNED the defect as intended: the
    exclusive label over a population that was never sampled. The PR run below is what makes
    the exclusive claim supportable, and it is the difference between this test and
    `test_a_push_only_red_with_no_pr_run_at_all_is_not_called_only_on_main`.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    name = catch.artifact_name(family, leg)
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PUSH, SHA, "push"),
            # The PR-head side IS sampled — green, and it names this file.
            _run(RUN_PR, SHA, "pull_request", pr=79, conclusion="success"),
        ],
        jobs={
            RUN_PUSH: [_check(100, RUN_PUSH, job, "failure")],
            RUN_PR: [_check(200, RUN_PR, job, "success")],
        },
        # The SAME artifact name in both runs, which is the real upload shape.
        artifacts={
            (RUN_PUSH, name): _zip(_junit([("tests/test_billing.py", "t", True)])),
            (RUN_PR, name): _zip(_junit([("tests/test_billing.py", "t", False)])),
        },
    )
    row = _row(_report(root), "test_billing.py")
    assert row["pr_runs_attested"] == 1, "the PR run's own artifact names this file"
    assert row["status"] == catch.CATCH_PUSH_ONLY, row["status"]
    assert row["pr_head_catches"] == []
    assert [c["run_id"] for c in row["push_main_catches"]] == [str(RUN_PUSH)]


def test_a_push_only_red_with_no_pr_run_at_all_is_not_called_only_on_main(tmp_path):
    """The ZERO-MEASUREMENT shape: nothing ran on a PR head, so nothing can be `ONLY on main`.

    ⛔ `_pr_head_gap()` knew two shapes — a recorded `pr-head` `Unknown`, and a READ gap
    (`pr_leg_runs_unread`). Neither fires when the population was never SAMPLED at all: a row
    with a push catch, **zero PR runs of any kind and zero counters** is `None` from the gap
    check, so `status` minted `CATCH_PUSH_ONLY` — an EXCLUSIVE claim about a population that was
    never sampled. That is strictly WEAKER evidence than the read gap the gap check already
    refuses (there the leg at least ran), and doctrine #11 at the top of this file states the
    rule generally: *"An EXCLUSIVE label needs a sample without gaps"*. The two landed fixes
    covered only the two shapes that had been observed; this one is the same asymmetry, one
    step further back.

    It is scored, not merely labelled: `render_summary` prints it into the summary total as
    `"N only on main"` (cited by SYMBOL, never by line — this fix grows the file, so a
    numeral written here is stale the moment it lands),
    and `CATCH_PUSH_ONLY` is the bucket a `slow_files` reopen argues from — so an unsampled
    PR-head side must not be scored as a measured negative about PR heads.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        # NO PR run exists in this world at all — the population was never sampled.
        runs=[_run(RUN_PUSH, SHA, "push")],
        jobs={RUN_PUSH: [_check(100, RUN_PUSH, f"{family} ({leg})", "failure")]},
        artifacts={
            (RUN_PUSH, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            ),
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")

    assert row["pr_runs_attested"] == 0, "no PR run exists to attest it"
    assert row["pr_leg_runs_unread"] == 0 and row["pr_leg_runs_unattested"] == 0, (
        "neither existing gap shape fires — this is the uncovered one"
    )
    assert row["status"] == catch.UNKNOWN, (
        f"an exclusive claim over an unsampled population: {row['status']}"
    )
    assert [c["run_id"] for c in row["push_main_catches"]] == [str(RUN_PUSH)], (
        "the main catch is still cited — only the exclusive WORDING is withheld"
    )
    assert "no PR run attests this file" in (row["note"] or ""), row["note"]
    assert report["coverage"]["status_" + catch.CATCH_PUSH_ONLY] == 0, (
        "a row with no PR-head sample must not be counted as 'only on main'"
    )
    assert report["coverage"]["status_" + catch.UNKNOWN] > 0


def test_a_push_only_catch_over_a_pr_head_gap_is_not_called_only_on_main(tmp_path):
    """`CATCH-PUSH-ONLY` asserts an EXCLUSIVE claim, so a PR-head gap must outrank it.

    ⛔ `status` returned `CATCH_PUSH_ONLY` before it consulted `unknowns`, so a row with a
    recorded PR-head artifact gap was counted in `coverage.status_CATCH_PUSH_ONLY` and
    printed as "*N* only on main" — the bucket a `slow_files` reopen argues from. That is
    the same asymmetry the `NEVER` branch forbids for a push attestation (a gap is an
    UNKNOWN, never a negative about a PR head): "it never caught on a PR head" is
    unmeasured here, not measured-and-negative. The catch on main is still cited.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PR, SHA, "pull_request", pr=71),
            _run(RUN_PUSH, SHA, "push"),
        ],
        jobs={
            # The PR head is RED with NO artifact — an unreadable PR-head sample.
            RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")],
            RUN_PUSH: [_check(200, RUN_PUSH, f"{family} ({leg})", "failure")],
        },
        artifacts={
            (RUN_PUSH, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            ),
        },
    )
    report = _report(root)
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN, row["status"]
    assert [c["run_id"] for c in row["push_main_catches"]] == [str(RUN_PUSH)], (
        "the main catch is still cited — what is withheld is the exclusive wording"
    )
    assert any(u["kind"] == "pr-head" for u in row["unknown"])
    assert "withheld" in (row["note"] or "")
    assert report["coverage"]["status_" + catch.CATCH_PUSH_ONLY] == 0, (
        "a row with a PR-head gap must not be counted as 'only on main'"
    )
    assert report["coverage"]["status_" + catch.UNKNOWN] > 0


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
    # The window and the method travel WITH the table.
    assert "## Method" in rendered
    assert "Window in one line:" in rendered
    assert "newest **ATTEMPT** decides" in rendered
    assert "regenerate it, do not edit it" in rendered


# --- the markdown table is a TABLE ------------------------------------------


def _cells(line: str) -> int:
    """Cells in one markdown table row. An UNESCAPED pipe delimits; `\\|` is content."""
    return len(re.findall(r"(?<!\\)\|", line)) - 1


def test_every_markdown_row_has_the_same_number_of_cells(tmp_path):
    """GFM refuses a table whose header and delimiter rows differ in cell count.

    ⛔ `render_table` built its header and its delimiter row as two hand-maintained
    literals. Adding the `cleared by a re-run` column to the header and to the data rows
    left the delimiter at NINE cells under a TEN-cell header, so GFM (spec §4.10: "The
    header row must match the delimiter row in the number of cells. If not, a table will
    not be recognised") rendered #10131's own acceptance artifact — the committed 74-row
    report — as ONE PARAGRAPH of pipe text. Both rows are generated from `TABLE_COLUMNS`
    now; this assertion is what keeps a future column from splitting them apart again.
    """
    table = catch.render_table(_report(_world(tmp_path, runs=[])))
    cells = [_cells(line) for line in table.splitlines()]
    assert len(set(cells)) == 1, f"the rows disagree on cell count: {sorted(set(cells))}"
    assert cells[0] == len(catch.TABLE_COLUMNS)
    # ⛔ DERIVED, not the literal 76: 76 is today's manifest size, so a snapshot of the
    # live config reds on any legitimate `slow_files` edit with a bare message that names
    # no cause and gives no update guidance — the same rot #10194 caused once.
    assert len(cells) == len(MANIFEST["slow_files"]) + 2  # header + delimiter + one row per slow file


def test_the_counts_follow_the_manifest_not_a_literal(tmp_path):
    """A manifest that GROWS moves the table and the coverage total with it.

    ⛔ Three snapshots of "74" survived in this file (`assert total == 74`, two
    `len(cells) == 76`) — the exact rot the 74-row test below forbids: a legitimate
    `slow_files` add/remove reds them with a message that names no cause and gives no
    update guidance, and the update they demand is a new constant. Pre-fix this test
    reds on BOTH literals while the code is correct; post-fix it is the assertion that
    keeps the derivation honest in the other direction (a row silently dropped would
    still red it).
    """
    widened = dict(MANIFEST)
    widened["slow_files"] = [*MANIFEST["slow_files"], "test_a_file_the_manifest_grew.py"]
    root = _world(tmp_path, runs=[])
    report = catch.build_history(widened, catch.OfflineClient(root))

    expected = len(MANIFEST["slow_files"]) + 1
    assert len(report["files"]) == expected
    coverage = report["coverage"]
    statuses = (
        catch.CATCH_PR,
        catch.CATCH_PUSH_ONLY,
        catch.NEVER,
        catch.UNKNOWN,
        catch.NOT_PREMERGE,
    )
    assert sum(coverage[f"status_{key}"] for key in statuses) == expected

    table = catch.render_table(report)
    cells = [_cells(line) for line in table.splitlines()]
    assert len(set(cells)) == 1, f"the rows disagree on cell count: {sorted(set(cells))}"
    assert len(cells) == expected + 2  # header + delimiter + one row per slow file


def test_an_unknown_reason_cannot_break_the_row_it_is_rendered_in(tmp_path):
    """A reason embeds `gh`'s stderr, which is multi-line and can contain a `|`.

    ⛔ Cells were interpolated raw, so a download failure whose message ran to two lines
    ended the row early and a pipe inside it minted a phantom column: the committed report
    rendered as broken markdown EXACTLY when the data was imperfect — the case it exists
    for. Pre-fix this table's line count and its arity both disagree with its header.
    """
    family, leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=64)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            )
        },
    )
    client = catch.OfflineClient(root)

    def _broke(artifact_id):
        raise catch.ReadError(
            "check your internet connection\npost https://api.github.com/x: 502 | gateway"
        )

    client.artifact_zip = _broke  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)
    assert _row(report, "test_billing.py")["status"] == catch.UNKNOWN

    table = catch.render_table(report)
    cells = [_cells(line) for line in table.splitlines()]
    assert len(set(cells)) == 1, f"a reason split a row: {sorted(set(cells))}"
    assert len(cells) == len(MANIFEST["slow_files"]) + 2  # header + delimiter + one row per slow file
    rendered = next(line for line in table.splitlines() if "`test_billing.py`" in line)
    assert "gateway" in rendered, "the reason belongs IN the row, not dropped from it"
    assert "\\|" in rendered, "a pipe inside a cell is escaped, so it cannot mint a column"


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


def test_a_green_pr_leg_with_an_unreadable_artifact_withholds_only_on_main(tmp_path):
    """`CATCH-PUSH-ONLY` is EXCLUSIVE, and the common live read gap was not a gap to it.

    ⛔ `status` consulted `unknowns` only, but only a RED leg creates an `Unknown`: a GREEN
    PR leg whose artifact expired increments `pr_leg_runs_unread` and nothing turns that into
    an unknown. So the DOCUMENTED common shape ("the leg ran green, its artifact expired past
    retention") printed "only on main" — an exclusive claim about PR heads from a sample in
    which that side was never read, in the one bucket a `slow_files` reopen argues from. The
    table's own gap cell omitted `*_unread` too, so the deliverable showed `| 0 |`.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    name = catch.artifact_name(family, leg)
    a_path = catch.to_observed("test_billing.py")
    root = _world(
        tmp_path,
        runs=[
            _run(RUN_PUSH, SHA, "push"),
            _run(RUN_PR, SHA, "pull_request", pr=78, conclusion="success"),
        ],
        jobs={
            RUN_PUSH: [_check(100, RUN_PUSH, job, "failure")],
            RUN_PR: [_check(200, RUN_PR, job, "success")],
        },
        # The PR leg RAN GREEN and its artifact is gone — the shape the module documents as
        # the common one live. Only the push run's artifact survives.
        artifacts={(RUN_PUSH, name): _zip(_junit([(a_path, "t", True)]))},
    )
    report = _report(root)
    row = _row(report, a_path)

    assert row["pr_leg_runs_unread"] == 1
    assert row["status"] == catch.UNKNOWN, row["status"]
    assert [c["run_id"] for c in row["push_main_catches"]] == [str(RUN_PUSH)], (
        "the main catch is still cited — only the exclusive WORDING is withheld"
    )
    assert "could not be READ" in (row["note"] or "")
    assert report["coverage"]["status_" + catch.CATCH_PUSH_ONLY] == 0

    # And the read gap is legible in the deliverable's own gap column, not only in the JSON.
    # ⛔ Assert the CELL, never an incidental substring of the row: `"| 1 |"` is ALSO the
    # `main runs (attested)` cell in this fixture, so the pre-fix `gap = unattested` — the
    # exact defect this test exists for — satisfied it and the column regressed silently
    # (measured: reverting the `*_unread` terms left this test green). The column index is
    # DERIVED from `TABLE_COLUMNS`, not a literal, so a column inserted before it cannot
    # re-point the assertion at a neighbour; the count guard catches a short/truncated row
    # (GFM truncates body rows longer than the header, so the cell count is not free).
    table = catch.render_table(report)
    rendered = next(line for line in table.splitlines() if f"`{row['file']}`" in line)
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", rendered)][1:-1]
    assert len(cells) == len(catch.TABLE_COLUMNS), f"row is not one cell per column: {cells}"
    gap_cell = cells[catch.TABLE_COLUMNS.index("leg runs w/o this file")]
    assert gap_cell == "1", (
        f"the PR-head READ gap must be IN the gap column, not only in the JSON: {cells}"
    )


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


def test_a_zip_that_lacks_the_members_is_a_READ_gap_not_an_expired_artifact(tmp_path):
    """The artifact was downloaded and IS a valid zip, so none of the expiry causes is true.

    ⛔ The "carries neither member" branch appended a note but never registered the cause on
    `RunArtifacts.download_failed`, so `unread_for(name)` stayed `None` and the row took the
    EXPIRY arm: *"expired past the repo's N-day retention, never produced, or not a parsable
    zip/XML"* — three causes this walk had just disproved (the artifact is in the listing, it
    was produced, and the `ZipFile` open succeeded). The branch is reachable by design of the
    upload it reads (`.github/workflows/python-ci.yml`: `if: always()`,
    `if-no-files-found: ignore`, five paths), so a leg that died before the junit/manifest
    steps uploads exactly this zip.
    """
    family, leg = _leg_of("test_billing.py")
    name = catch.artifact_name(family, leg)
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=72)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} ({leg})", "failure")]},
        artifacts={(RUN_PR, name): _zip_members({"pytest.log": b"collected 0 items\n"})},
    )
    row = _row(_report(root), "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    reason = row["unknown"][0]["reason"]
    assert "retention" not in reason, "the artifact was downloaded — it is not expired"
    assert "neither" in reason and "valid zip" in reason, reason


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
    # ⛔ DERIVED, not the literal 74 — the manifest read is two lines up and a constant
    # here is a snapshot that reds on a legitimate `slow_files` edit.
    assert total == len(MANIFEST["slow_files"])
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


class _FailingListing(catch.OfflineClient):
    """An offline client whose Nth population listing is cut by the WALK BOUND."""

    def __init__(self, root: Path, *, fail_at: int) -> None:
        super().__init__(root)
        self.fail_at = fail_at
        self.listed: list[tuple[str, str | None, str]] = []

    def list_runs(self, event, branch, conclusion, limit):
        self.listed.append((event, branch, conclusion))
        if len(self.listed) == self.fail_at:
            raise catch.WalkBoundReached(
                "gh api repos/o/r/actions/runs not attempted — the walk's wall-clock bound "
                "was reached"
            )
        return super().list_runs(event, branch, conclusion, limit)


def test_a_walk_bound_while_listing_cuts_the_sample_instead_of_discarding_the_report(tmp_path):
    """⛔ `main` exited 2 with NO report when the walk bound fired DURING a listing.

    `list_runs` re-raises a page-1 failure by design (a page-2+ cut is a recorded truncation,
    page 1 leaves that population unusable) — and `build_history`'s listing loop had no
    handler, so the error reached `main`, which returns 2 BEFORE writing `--json-out` /
    `--md-out`. The one state `--max-seconds` exists for therefore produced nothing at all:
    not the truncated sample `listing_stop`/`window.truncation` exist to describe, and not
    even the `UNKNOWN` rows the module promises. The bound must cut the SAMPLE.

    Reachable by arithmetic, not by contrivance: the per-population check is
    `elapsed > max_seconds`, so a population entered with a few seconds of budget left passes
    it, its attempt is clamped to those seconds (measured p100 ≈ 12 s on this repo), and the
    retry's own deadline check then raises exactly this error.
    """
    family, _leg = _leg_of("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=7)],
        jobs={RUN_PR: [_check(100, RUN_PR, f"{family} (a)", "failure")]},
        artifacts={
            (RUN_PR, catch.artifact_name(family, "a")): _zip(
                _junit([("tests/test_billing.py", "t", True)])
            )
        },
    )
    # pull_request/failure is read; push-main/failure is then cut by the walk's bound.
    client = _FailingListing(root, fail_at=2)
    report = catch.build_history(MANIFEST, client, max_seconds=90)  # pre-fix: raises

    key = catch.population_key("pull_request", "failure")
    assert len(client.listed) == 2, f"the walk kept listing past the bound: {client.listed}"
    populations = report["window"]["populations"]
    assert len(populations) == 6, "every population must be accounted for, read or not"
    assert populations[key]["listed"] is True and populations[key]["read"] == 1
    unlisted = [k for k, info in populations.items() if info["listed"] is False]
    assert len(unlisted) == 5
    assert all(populations[k]["read"] == 0 for k in unlisted)
    assert all(populations[k]["hit_bound"] is False for k in unlisted)

    # The cut NAMES the population it stopped at and the reason, in the paragraph a reader
    # uses to judge how much of the sample the numbers rest on.
    truncation = report["window"]["truncation"] or ""
    assert "listing stopped at push-main/failure" in truncation, truncation
    assert "wall-clock bound was reached" in truncation, truncation
    assert "NEVER listed" in truncation, truncation

    # And the consequence: an unlisted population attests NOTHING, so no row may read NEVER.
    assert report["coverage"]["status_" + catch.NEVER] == 0
    assert not [row for row in report["files"] if row["status"] == catch.NEVER]
    assert any(row["status"] == catch.UNKNOWN for row in report["files"])


def test_the_walk_bound_while_listing_still_writes_the_report(tmp_path, monkeypatch):
    """`main` must EXIT 0 and leave the artifact when the bound fires mid-listing.

    ⛔ The handler above is only half the contract: the finding's OUTCOME was "exits 2 with no
    report at all", and `main` writes `--json-out`/`--md-out` AFTER its
    `except ReadError: return 2`. So this drives the real `GhClient` (whose `_gh` is the thing
    that raises) through the CLI and pins the artifact — a report per file is what those flags
    exist for, and a bound the caller asked for must not destroy it.

    ⛔ Deliberately `WalkBoundReached`, not any `ReadError`: a runs payload that IS a response
    but is not a runs listing stays exit 2 (see
    `test_a_runs_file_that_is_not_a_runs_listing_is_a_read_failure_not_an_empty_window`).
    """
    monkeypatch.setattr(catch.ci_selection, "load_manifest", lambda: MANIFEST)

    def _gh(self, args, binary=False):
        raise catch.WalkBoundReached(
            "gh api repos/o/r/actions/runs not attempted — the walk's wall-clock bound "
            "was reached"
        )

    monkeypatch.setattr(catch.GhClient, "_gh", _gh)
    out = tmp_path / "catch-history.md"
    assert catch.main(["--repo", "o/r", "--md-out", str(out)]) == 0, (
        "pre-fix: exit 2 and no artifact"
    )
    text = out.read_text(encoding="utf-8")
    assert "TRUNCATED" in text, text[:400]
    assert "wall-clock bound was reached" in text

    # The shape failure is NOT folded into this: the same transport, a payload that is not a
    # runs listing, is still exit 2 (the guard against catching every `ReadError` here).
    monkeypatch.setattr(
        catch.GhClient, "_gh", lambda self, args, binary=False: '{"message": "Not Found"}'
    )
    assert catch.main(["--repo", "o/r"]) == 2, "a non-listing response is a read failure"


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


def _prose_pager(*, prose_page: int, per_page: int = 100) -> catch.GhClient:
    """A `GhClient` whose `_gh` serves JSON up to `prose_page`, then a non-JSON body.

    ⛔ The shape is the documented hazard, not a hypothetical: every `gh` call here is
    relayed through the fleet's rate-limit wrapper, so a prose line can precede (or replace)
    the payload.
    """
    client = catch.GhClient("o/r", max_calls=10_000)

    def _gh(args, binary=False):
        page = int(args[1].rsplit("page=", 1)[1])
        if page >= prose_page:
            return (
                "warning: relayed gh api call is being retried (rate limit 4/5000)\n"
                '{"total_count": 1000, "workflow_runs": []}'
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


def test_a_page_two_prose_page_truncates_the_listing_instead_of_discarding_the_walk(
    tmp_path,
):
    """An UNDECODABLE page-2+ body is the same CUT as its three siblings, not a fatal error.

    ⛔ Every other cut in `list_runs` is page-guarded — the page-1 `BudgetExhausted` re-raises,
    the page-2+ transport `ReadError` truncates, the page-2+ missing-`workflow_runs` arm
    truncates — but the `json.loads` raise was not. Unguarded it reached `main` as a plain
    `ReadError`, which `build_history`'s listing handler does NOT catch (it handles
    `WalkBoundReached`/`BudgetExhausted` only), so a walk that had already spent hundreds of
    seconds and up to `--max-api-calls` exited 2 and discarded the report AND the page-1 data
    it had read. An EMPTY page-2 body already truncated (it decodes to `{}` → no
    `workflow_runs`), so an empty cut and a garbage cut behaved differently — the walk's
    survival decided by which error the transport happened to raise.
    """
    client = _prose_pager(prose_page=2)
    runs = client.list_runs("pull_request", None, "failure", 500)
    assert len(runs) == 100, "the pages that WERE read are kept"
    assert client.truncation is not None, "the cut was silent"
    assert "pull_request/failure" in client.truncation
    assert "page 1" in client.truncation
    assert "was not decodable" in client.truncation

    # The finding's own OUTCOME: a report still exists. Pre-fix this walk exited 2 with no
    # `--json-out`/`--md-out` at all, so the assertion below is what reddens on the revert.
    seed = _world(tmp_path, runs=[])
    offline = catch.OfflineClient(seed)

    def _list_runs(event, branch, conclusion, limit):
        page_runs = _prose_pager(prose_page=2).list_runs(event, branch, conclusion, limit)
        offline.truncation = client.truncation
        return page_runs

    offline.list_runs = _list_runs  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, offline)
    assert "pull_request/failure" in (report["window"]["truncation"] or "")
    assert "TRUNCATED" in catch.render_summary(report)

    # PAGE 1 is deliberately NOT folded into this: nothing was read, so the population is
    # unusable and `main` must exit 2 (`test_a_runs_file_that_is_not_a_runs_listing_...`).
    page_one = _prose_pager(prose_page=1)
    with pytest.raises(catch.ReadError) as excinfo:
        page_one.list_runs("pull_request", None, "failure", 500)
    assert "unparseable JSON" in str(excinfo.value)
    assert page_one.truncation is None, "a page-1 failure is not a truncation — it is a failure"


def test_a_budget_cut_keeps_the_cuts_recorded_before_it():
    """`_spend` ASSIGNED `truncation`, so the budget sentence erased every earlier cut.

    ⛔ Order is deterministic, not a race: `list_runs` notes its cuts and completes before the
    detail loop spends the budget, so the LAST writer was always `_spend` — and it replaced
    the accumulated list's rendering with its own sentence. The reachable shape: a listing
    that stopped early on page 2, then a walk that exhausted `--max-api-calls` in the detail
    loop, published only "API-call bound reached" and dropped the page-1 cut from the one
    paragraph a reader uses to judge how much of the sample the numbers rest on.
    """
    client = catch.GhClient("o/r", max_calls=1)
    client.note_truncation("pull_request/failure listing stopped after page 1: page cap")
    with pytest.raises(catch.BudgetExhausted):
        client._spend(5)
    text = client.truncation or ""
    assert "listing stopped after page 1" in text, "the earlier cut was erased"
    assert "API-call bound reached (1)" in text


def test_a_budget_cut_while_listing_names_the_population_it_cut():
    """A population the bound cut mid-listing must not read as a population of that size.

    ⛔ `list_runs`'s `except BudgetExhausted: return runs` recorded nothing, so
    `window.populations[key]` said `read: 200, requested: 500, hit_bound: False` under a
    caption that only flags the AT-REQUESTED case — the population read as complete.
    """
    client = catch.GhClient("o/r", max_calls=2)

    def _gh(args, binary=False):
        client._spend(1)  # exactly what the real `_gh` does at the top of every attempt
        page = int(args[1].rsplit("page=", 1)[1])
        return json.dumps(
            {
                "total_count": 1000,
                "workflow_runs": [
                    _run(page * 1000 + i, SHA, "pull_request", pr=1) for i in range(100)
                ],
            }
        )

    client._gh = _gh  # type: ignore[method-assign]
    runs = client.list_runs("pull_request", None, "failure", 500)
    assert len(runs) == 200, "the pages that WERE read are kept"
    text = client.truncation or ""
    assert "pull_request/failure listing stopped after page 2" in text, text
    assert "API-call bound reached (2)" in text


def test_a_page_one_budget_cut_marks_the_population_never_listed():
    """A population the budget cut BEFORE page 1 published as `listed: true, read: 0`.

    ⛔ `list_runs`'s `except BudgetExhausted: return runs` returned an EMPTY listing, so
    `build_history` recorded `listed: true, read: 0` for that population and went on to the
    next one, which returned `[]` in turn: a walk that spent its entire budget before the first
    page published six populations that each read as *listed, and empty*. That is the opposite
    of what happened — NOTHING was listed — and it put two meanings on `listed` in one dict.

    `WalkBoundReached` is re-raised out of `list_runs` for exactly this reason (a page-1 failure
    leaves the population unusable; only a page-2+ cut is a recorded truncation), and the
    API-call bound is the same cut on a different counter, so it takes the same path: every
    population the bound reached is `listed: false`, `listing_stop` names the population it
    stopped at, every row is UNKNOWN and none is NEVER.
    """
    client = catch.GhClient("o/r", max_calls=1)

    def _gh(args, binary=False):
        if "actions/runs" not in args[1]:  # the retention read: not this test's surface
            raise catch.ReadError("retention not read in this test")
        client._spend(1)  # exactly what the real `_gh` does at the top of every attempt
        return json.dumps({"total_count": 0, "workflow_runs": []})

    client._gh = _gh  # type: ignore[method-assign]
    report = catch.build_history(MANIFEST, client)

    populations = report["window"]["populations"]
    assert len(populations) == 6, "every population must be accounted for, read or not"
    # The first population WAS listed (an empty page is a listing, not a cut); everything the
    # bound reached after it was never listed. Pre-fix ALL SIX read `listed: true, read: 0`.
    assert populations["pull_request/failure"]["listed"] is True
    reached = [k for k in populations if k != "pull_request/failure"]
    assert all(populations[k]["listed"] is False for k in reached), populations
    assert all(populations[k]["read"] == 0 for k in reached)
    assert all(populations[k]["hit_bound"] is False for k in reached)

    truncation = report["window"]["truncation"] or ""
    assert "listing stopped at push-main/failure" in truncation, truncation
    assert "API-call bound reached (1)" in truncation, truncation
    assert "NEVER listed" in truncation, truncation

    # And the consequence: nothing was attested, so no row may read a clean bill.
    assert report["coverage"]["status_" + catch.NEVER] == 0
    assert any(row["status"] == catch.UNKNOWN for row in report["files"])


def test_a_budget_cut_in_the_detail_loop_keeps_the_listing_cut_in_the_window():
    """The composition travels: `window["truncation"]` is what `render_summary` prints."""

    class _CutThenBroke(catch.GhClient):
        """A cut listing (real pager), then the budget runs out in the detail loop."""

        def __init__(self) -> None:
            super().__init__("o/r", max_calls=0)
            self._pager = _pager(fail_page=2)

        def list_runs(self, event, branch, conclusion, limit):
            runs = self._pager.list_runs(event, branch, conclusion, limit)
            self.truncations = list(self._pager.truncations)
            self.truncation = self._pager.truncation
            return runs

        def jobs(self, run_id):
            self._spend(1)  # the bound trips here, exactly as it does inside `_gh`
            return []

        def retention_days(self):
            return 90

    report = catch.build_history(MANIFEST, _CutThenBroke())
    truncation = report["window"]["truncation"] or ""
    assert "listing stopped after page 1" in truncation, truncation
    assert "API-call bound reached" in truncation, truncation


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
        if str(artifact_id) == f"{RUN_PR}/{a_name}":
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


def test_a_rerun_artifact_is_reduced_by_created_at_not_by_listing_order():
    """A re-run is a NEW ATTEMPT of the SAME run id, so both artifacts share `(run_id, name)`.

    ⛔ `{name: item for item in items}` kept whichever the API returned LAST — not the newest
    — so which attempt's JUnit a red was attributed from was decided by response order. The
    listing is run-scoped with no attempt-specific sibling, so `created_at` is the only
    ordering available, and the same attempt-identity rule the check-run reduction delegates
    to `ci_verdict.group_latest_attempts` has to hold here too.
    """
    name = "pytest-log-test-slow-a"
    older = {
        "id": 11,
        "name": name,
        "created_at": "2026-10-30T00:00:00Z",
        "expired": False,
    }
    newer = {
        "id": 22,
        "name": name,
        "created_at": "2026-10-31T00:00:00Z",
        "expired": False,
    }
    client = catch.GhClient("o/r", max_calls=100)

    # The NEWEST arrives FIRST — last-wins would hand back `older`.
    client._paginated = lambda url, key: [newer, older]  # type: ignore[method-assign]
    assert client.artifacts("1")[name]["id"] == 22

    # A LIVE older artifact beats an EXPIRED newer one: `RunArtifacts.get` drops an expired
    # hit to UNKNOWN, so letting expiry win discards evidence that still exists.
    expired = {**newer, "expired": True}
    client._paginated = lambda url, key: [expired, older]  # type: ignore[method-assign]
    assert client.artifacts("1")[name]["id"] == 11

    # When EVERY copy is expired the winner is still expired — the row must then say so,
    # rather than read as an artifact that was never produced.
    client._paginated = lambda url, key: [{**older, "expired": True}]  # type: ignore[method-assign]
    assert client.artifacts("1")[name]["expired"] is True


def test_a_zip_download_that_is_not_bytes_is_a_READ_failure_not_a_traceback(monkeypatch):
    """`assert isinstance(raw, bytes)` is ELIDED under `python -O`, and the guard is load-bearing.

    ⛔ With it elided, a `gh` that hands back a str reaches `zipfile.ZipFile(BytesIO(data))`,
    whose `TypeError` is not one of `_leg_evidence`'s handlers — so it escaped `main` as a
    traceback (exit 1, no report) instead of the documented exit 2 / UNKNOWN row. The raise
    also has to be `ReadError`, which IS handled: pre-fix this test sees `AssertionError`
    under a normal interpreter and nothing at all under `-O`.
    """
    client = catch.GhClient("o/r", max_calls=10)
    monkeypatch.setattr(client, "_gh", lambda args, binary=False: "not-bytes")
    with pytest.raises(catch.ReadError, match="not bytes"):
        client.artifact_zip(1234)


def test_a_retry_loop_that_never_ran_is_a_READ_failure_not_an_assert(monkeypatch):
    """`assert last is not None` at the bottom of `_gh` is the same elidable-guard defect."""
    client = catch.GhClient("o/r", max_calls=10)
    monkeypatch.setattr(catch.GhClient, "RETRIES", 0)
    with pytest.raises(catch.ReadError, match="never attempted"):
        client._gh(["api", "repos/o/r/actions/runs"])


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


def test_an_unorderable_red_is_carried_as_superseded_never_as_cleared():
    """`_check_id` maps an unparseable id to `-1`, and `-1 < latest.check_id` for EVERY latest.

    ⛔ So a red of UNKNOWN AGE was emitted as `cleared` — "a clean re-measurement happened" —
    on no evidence of order at all: the same fabricated clean the numeric-string coercion
    above exists to prevent, reached from the other side. Nothing about it is orderable, so it
    is left unaccounted and lands in `superseded`: the red is still COUNTED (it happened), it
    is just never called cleared.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    red = _check("not-a-number", RUN_PR, job, "failure")
    green = _check(600, RUN_PR, job, "success")

    groups = catch.reduce_attempts([red, green], {str(RUN_PR): ("111", "python-ci")})
    cleared = [attempt for group in groups for attempt in group.cleared]
    assert all(a.check_id >= 0 for a in cleared), (
        f"an unorderable red cannot be 'cleared': {[a.check_id for a in cleared]}"
    )
    carried = [attempt for group in groups for attempt in group.superseded]
    assert [a.check_id for a in carried] == [-1], "it is still counted, as superseded"


# --- 13. the lane split is DERIVED, not a constant --------------------------


def test_a_cancelled_RUN_still_cites_the_red_its_leg_completed(tmp_path):
    """A cancelled RUN is not a cancelled JOB: a run cancelled after a leg finished RED is a catch.

    ⛔ The CANCELLED population exists for exactly this (a run's conclusion is its newest
    attempt's), and the body claims a test pins it — but no fixture had a run with
    `conclusion="cancelled"`, so `OfflineClient.list_runs` filtered the population to nothing
    and inserting `if ref.conclusion == "cancelled": continue` in the detail loop left the
    whole suite green. The naive reading the body says was corrected was therefore unpinned.
    """
    family, leg = _leg_of("test_billing.py")
    job = f"{family} ({leg})"
    a_path = catch.to_observed("test_billing.py")
    root = _world(
        tmp_path,
        runs=[_run(RUN_PR, SHA, "pull_request", pr=79, conclusion="cancelled")],
        jobs={RUN_PR: [_check(100, RUN_PR, job, "failure")]},
        artifacts={
            (RUN_PR, catch.artifact_name(family, leg)): _zip(_junit([(a_path, "t", True)]))
        },
    )
    row = _row(_report(root), a_path)
    assert row["status"] == catch.CATCH_PR, row["status"]
    assert [c["run_id"] for c in row["pr_head_catches"]] == [str(RUN_PR)]


def test_an_unwritable_output_path_is_the_documented_exit_2_not_a_traceback(tmp_path, capsys):
    """The `except OSError` covers the READS; the two WRITES sat outside it.

    ⛔ A missing parent directory (or a permission error, or a read-only mount) escaped as an
    uncaught traceback with exit 1 — outside the module's documented "0 the report was
    produced · 2 it could not be" — and it fired AFTER a walk that may have cost ~1500 s, on
    the one artifact the run existed to produce.
    """
    root = _world(tmp_path, runs=[])
    missing = tmp_path / "no-such-directory" / "report.md"
    code = catch.main(["--offline-dir", str(root), "--md-out", str(missing)])
    assert code == 2, f"documented exit 2, not an uncaught traceback (got {code})"
    err = capsys.readouterr().err
    assert "could not write" in err and "report.md" in err


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


# --- 12. a malformed page body is a READ failure, never a discarded walk ---


def _relayed_client(*, malformed: str) -> catch.GhClient:
    """A live `GhClient` whose `_gh` answers every surface, but with a NON-JSON body on one.

    ⛔ The body shape is the module's own documented hazard, not a contrivance: every `gh`
    call here is relayed through the fleet's rate-limit wrapper, so a prose line can precede
    (or replace) the payload — and `gh api --paginate` emits CONCATENATED page objects, whose
    decoder fails on any trailing non-JSON text (cli/cli#8419 is the same shape from the
    other side). `list_runs` already translates that decode failure; `_paginated` did not.
    """
    family, leg = _leg_of("test_billing.py")
    client = catch.GhClient("o/r", max_calls=10_000)
    poisoned = "warning: rate-limit guard engaged\nnot json"

    def _gh(args, binary=False):
        url = args[1]
        if binary:  # pragma: no cover - the listing never resolves in these fixtures
            raise catch.ReadError("no artifact bytes in this fixture")
        if "/jobs?" in url:
            if malformed == "jobs":
                return poisoned
            return json.dumps(
                {
                    "total_count": 1,
                    "jobs": [_check(100, RUN_PR, f"{family} ({leg})", "failure")],
                }
            )
        if "/artifacts?" in url:
            if malformed == "artifacts":
                return poisoned
            return json.dumps({"total_count": 0, "artifacts": []})
        if "retention" in url:
            return json.dumps({"days": 90})
        event = "pull_request" if "event=pull_request" in url else "push"
        conclusion = url.rsplit("status=", 1)[1].split("&")[0]
        page = int(url.rsplit("page=", 1)[1]) if "page=" in url else 1
        if page > 1 or (event, conclusion) != ("pull_request", "failure"):
            return json.dumps({"total_count": 0, "workflow_runs": []})
        return json.dumps(
            {"total_count": 1, "workflow_runs": [_run(RUN_PR, SHA, "pull_request", pr=61)]}
        )

    client._gh = _gh  # type: ignore[method-assign]
    return client


def test_a_non_json_jobs_page_is_a_read_failure_not_an_escape():
    """`decode_pages` raises `JSONDecodeError`, which is neither `ReadError` nor budget.

    ⛔ It was decoded OUTSIDE `_paginated`'s translation, so it escaped BOTH handlers that
    exist to degrade one run instead of discarding the walk — the detail loop's per-run
    `except ReadError` and `main`'s honest exit 2 — and a walk that may already have spent
    ~1500 s / ~2000 API calls produced NO report at all. The ReadError spelling of the same
    failure degrades correctly, so the walk's survival depended on which error type the
    transport happened to raise.
    """
    client = _relayed_client(malformed="jobs")
    with pytest.raises(catch.ReadError) as excinfo:  # pre-fix: json.JSONDecodeError
        client.jobs(str(RUN_PR))
    assert "unparseable JSON" in str(excinfo.value)

    report = catch.build_history(MANIFEST, _relayed_client(malformed="jobs"))  # pre-fix: raises
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN, "an unread run is a gap, never a clean bill"
    assert report["coverage"]["status_" + catch.NEVER] == 0
    assert any("unparseable JSON" in note for note in report["window"]["api_notes"])


def test_a_non_json_artifact_listing_degrades_to_unknown_instead_of_aborting():
    """The `ReadError` spelling of this failure already degraded; the decode one did not."""
    report = catch.build_history(MANIFEST, _relayed_client(malformed="artifacts"))
    row = _row(report, "test_billing.py")
    assert row["status"] == catch.UNKNOWN
    reason = row["unknown"][0]["reason"]
    assert "the listing could not be read" in reason
    assert "unparseable JSON" in reason, reason
    assert "retention" not in reason, "the artifact may exist — the listing was never read"


# --- 13. the walk's own bound bounds the CALLS, not just the loop ------------


def test_a_stalled_call_cannot_outlive_the_walk_bound(monkeypatch):
    """4 × `CALL_TIMEOUT_SECONDS` + backoff ≈ 1214 s — 13× a `--max-seconds 90` request.

    ⛔ The per-attempt bound is right (one stalled relayed `gh` must not hang the walk), but
    on its own it does not bound the thing it names: `--max-seconds` was consulted only
    BETWEEN calls, so every retry took the full 300 s no matter how little of the walk's
    budget was left. That is the same "a bound that does not bound the thing it names" shape
    this module closed for the LISTING phase — so the deadline is propagated into the loop.
    """
    seen: list[float] = []

    def _timeout(cmd, capture_output=False, timeout=None):
        seen.append(timeout)
        raise catch.subprocess.TimeoutExpired(cmd, timeout or 0)

    monkeypatch.setattr(catch.subprocess, "run", _timeout)
    monkeypatch.setattr(catch.time, "sleep", lambda _s: None)
    client = catch.GhClient("o/r", max_calls=100, max_seconds=90)
    with pytest.raises(catch.ReadError) as excinfo:
        client._gh(["api", "repos/o/r/actions/runs"])
    assert seen, "the first attempt is made"
    assert seen[0] <= 90, f"the attempt was bounded at {seen[0]}s, not the 90 s left"
    assert seen[0] < catch.GhClient.CALL_TIMEOUT_SECONDS
    assert "timed out" in str(excinfo.value)


def test_no_attempt_is_spent_once_the_walk_bound_has_passed(monkeypatch):
    """A retry starting after the deadline would spend a call and wait to be killed."""
    ran: list[object] = []
    monkeypatch.setattr(catch.subprocess, "run", lambda *args, **kwargs: ran.append(args))
    monkeypatch.setattr(catch.time, "sleep", lambda _s: None)
    client = catch.GhClient("o/r", max_calls=100, max_seconds=90)
    client.deadline = catch.time.monotonic() - 1  # the walk is already out of time
    # ⛔ The exact TYPE, not just `ReadError`: `build_history`'s listing loop distinguishes a
    # caller-requested CUT (`WalkBoundReached` → truncated report) from a surface that could
    # not be read (`ReadError` → exit 2). If `_gh` raised the base class, a bound reached
    # mid-listing would discard the whole report again.
    with pytest.raises(catch.WalkBoundReached) as excinfo:
        client._gh(["api", "repos/o/r/actions/runs"])
    assert ran == [], "an attempt was made with no walk budget left"
    assert client.calls == 0, "a call that was never made must not be spent"
    assert "wall-clock bound" in str(excinfo.value)
    assert catch.GhClient("o/r", max_seconds=0).deadline is None, "0 disables the bound"


# --- the `durations` note re-derives its own count --------------------------


def test_the_durations_note_counts_the_tests_this_file_actually_has():
    """The `durations` justification cites its own derivation command — keep it true.

    ⛔ `config/ci-surfaces.yml` states the wall of "its offline tests (**N** of them —
    `grep -c '^def test_'`)". It read 29, then 32, while this file grew to 41: two separate
    reviews filed the SAME stale number, because the comment asserts a count it also carries
    the command to refute. So the guard RUNS that command rather than trusting the prose.
    """
    config = (REPO / "config" / "ci-surfaces.yml").read_text(encoding="utf-8")
    block = re.search(r"^\s*test_ci_catch_history\.py:.*(?:\n\s*#.*)*", config, re.MULTILINE)
    assert block, "the durations entry this note justifies is gone — re-derive the note"
    stated = re.search(r"\b(\d+) of them", block.group(0))
    assert stated, "the note no longer states a count — update this guard with the note"
    actual = len(re.findall(r"^def test_", Path(__file__).read_text(encoding="utf-8"), re.MULTILINE))
    assert int(stated.group(1)) == actual, (
        f"config/ci-surfaces.yml claims {stated.group(1)} offline tests; the file defines "
        f"{actual} — refresh the note (the count is asserted, so it cannot go stale silently)"
    )
