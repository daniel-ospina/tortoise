"""#14559 — the empty-test-graph reaper has a SCHEDULE, and the schedule is bounded.

The tool (`tools/reap_empty_test_graphs.py`) already refuses correctly; what the
issue asked for is something that runs it unattended. The failure modes that
matter are therefore all about the DRIVER, one level up from the tool:

1. **It must never widen a gate.** A schedule is where a `--test-org` "just for
   the noisy ones" gets added, and the tool's docstring says that widener must
   never exist. The scheduled invocations are asserted to pass NO such flag.
2. **An empty credential must not read as a clean sweep.** With no
   `TORTOISE_DB_URI` the tool falls back to an EMBEDDED database, which on a
   fresh runner answers `list_graphs() == []` — "0 graphs, nothing to reap,
   exit 0". The guard step is executed here with each credential missing in
   turn, so that shape is a RED run, not a silent one (#2140's deaf monitor).
3. **A dead sweep must not look green.** The verdict step is executed against
   fixtures for every arm that has to fail: a plan that refused, a target that
   answered with 0 graphs, deletions that did not take, an unreadable emptiness
   read, and a count above the stated band.
4. **It must not run on a PR or a push.** This job MUTATES production data; as a
   check-run it would red `main` or an unrelated PR — the same rule the sibling
   monitors record (`test_consent_key_check`).

Everything here runs offline: the scripts are executed as shell, never against a
database, and the JSON fixtures are the tool's own report shape.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "test-graph-reaper.yml"
TOOL = ROOT / "tools" / "reap_empty_test_graphs.py"


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> dict[str, dict]:
    return {s.get("name", s.get("uses", "?")): s for s in _doc()["jobs"]["reap"]["steps"]}


def _run_scripts() -> list[str]:
    return [s["run"] for s in _doc()["jobs"]["reap"]["steps"] if "run" in s]


def _script(prefix: str) -> str:
    for name, step in _steps().items():
        if "run" in step and name.startswith(prefix):
            return step["run"]
    raise AssertionError(f"no run step starting with {prefix!r}; have {sorted(_steps())}")


def _bash(script: str, *, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items()
            if not k.startswith(("TORTOISE_", "SUPABASE_", "REAPER_", "GH_", "PLAN_",
                                 "EXECUTE_", "VERIFY_"))}
    base["PATH"] = os.environ.get("PATH", "")
    merged = {**base, **env}
    return subprocess.run(["bash", "-c", script], cwd=str(cwd), env=merged,
                          capture_output=True, text=True, timeout=120)


# ── 1. the schedule's SHAPE ──────────────────────────────────────────────────

class TestScheduleShape:
    def test_the_workflow_exists_and_is_the_one_the_tool_points_at(self):
        assert WORKFLOW.is_file(), "the schedule #14559 asked for is missing"
        # The tool's own docstring names the driver, so a rename cannot silently
        # orphan the reference.
        assert "test-graph-reaper.yml" in TOOL.read_text(encoding="utf-8")

    def test_scopes_are_exactly_checkout_plus_issue_write(self):
        """An EQUALITY, not a presence check: an added `contents: write` is a real
        widening of what an unattended job may do to the repo (#14559's
        reviewers should see it)."""
        assert _doc()["permissions"] == {"contents": "read", "issues": "write"}

    def test_triggers_are_schedule_and_dispatch_only(self):
        """⛔ This job DELETES graphs in production. As a `push`/`pull_request`
        check-run it would red `main` or an unrelated PR for a condition no merge
        caused — and it would run against prod from PR branches."""
        triggers = _doc()[True]  # `on:` parses as the boolean True under YAML 1.1
        assert set(triggers) == {"schedule", "workflow_dispatch"}, sorted(map(str, triggers))

    def test_the_cadence_is_15_minutes(self):
        """The stated cadence is the leak's own arithmetic: a measured burst banks
        ~1.2 graphs/min, so 15 min bounds a run's batch at ~18 graphs ≈ 26 MB,
        while hourly would bank ~70 graphs ≈ 100 MB against a 1 GB instance."""
        triggers = _doc()[True]
        assert triggers["schedule"] == [{"cron": "*/15 * * * *"}]

    def test_a_tick_is_never_cancelled(self):
        """A cancelled tick reports nothing while having deleted something, and the
        next run's `used_memory` delta would silently absorb it."""
        assert _doc()["concurrency"] == {
            "group": "test-graph-reaper", "cancel-in-progress": False}

    def test_the_band_is_a_single_stated_number(self):
        env = _doc()["jobs"]["reap"]["env"]
        assert env["REAPER_TOTAL_BAND"] == "${{ vars.REAPER_TOTAL_BAND || '200' }}"
        # The band's derivation must travel with it, or the next reader raises it
        # to silence a finding.
        header = WORKFLOW.read_text(encoding="utf-8")
        assert "1.46 MB" in header and "292 MB" in header


# ── 2. the schedule must not widen a gate ────────────────────────────────────

class TestNoWidener:
    def test_no_scheduled_invocation_names_an_org(self):
        """`--test-org` is the tool's explicit-naming escape; in a schema it would
        be the "any org" flag the docstring says must never exist."""
        for script in _run_scripts():
            assert "--test-org" not in script, script

    def test_no_force_or_yes_flag_appears(self):
        for script in _run_scripts():
            for flag in ("--force", "--yes", "--all", "--no-verify"):
                assert flag not in script, (flag, script)

    def test_the_plan_pass_is_a_dry_run_and_precedes_the_execute_pass(self):
        names = list(_steps())
        plan_at = names.index("Plan (dry run — the retained rehearsal)")
        execute_at = names.index("Execute (GRAPH.DELETE on the reapable set only)")
        assert plan_at < execute_at
        plan = _steps()["Plan (dry run — the retained rehearsal)"]["run"]
        assert "--execute" not in plan, "the plan pass must not delete"
        execute = _steps()["Execute (GRAPH.DELETE on the reapable set only)"]["run"]
        assert "--execute" in execute
        # And the execute pass only runs when the plan produced a report.
        assert "steps.plan.outputs.rc == '0'" in (
            _steps()["Execute (GRAPH.DELETE on the reapable set only)"].get("if", ""))

    def test_a_verify_pass_follows_the_sweep(self):
        """The retained dry-run report per run (#14559 acceptance) is the PLAN
        artifact; the verify pass is what proves the deletions took."""
        steps = _steps()
        verify = steps["Verify (post-run dry run — the sweep's invariant)"]
        assert "--execute" not in verify["run"]
        assert "steps.plan.outputs.rc == '0'" in verify.get("if", "")

    def test_the_reports_are_uploaded_with_a_retention_window(self):
        upload = [s for s in _doc()["jobs"]["reap"]["steps"]
                  if str(s.get("uses", "")).startswith("actions/upload-artifact")]
        assert len(upload) == 1
        assert upload[0]["with"]["retention-days"] == 90
        assert "reaper-plan.json" in upload[0]["with"]["path"]

    def test_secrets_are_bound_in_env_never_interpolated_into_a_run_body(self):
        """#4361: `${{ secrets.X }}` in a `run:` body is substituted into the shell
        SOURCE before bash parses it, so the value is code, not data."""
        for script in _run_scripts():
            assert "${{ secrets." not in script
            assert "${{ vars." not in script


# ── 3. the credential guard (fail-closed) ────────────────────────────────────

CREDS = {
    "TORTOISE_DB_URI": "rediss://tortoise:s3cr3t-9f@falkordb.example:6379/tortoise",
    "SUPABASE_URL": "https://project.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-role-key",
    "REAPER_EXECUTE": "true",
    "REAPER_TOTAL_BAND": "200",
}

#: The password from the URI above, spelled so a leak is unmistakable. It is
#: asserted ABSENT from the guard's output as a literal: a shape-based check
#: (`"pw" not in out.replace("pw@", "")`) is vacuous — it strips exactly the
#: occurrence it is looking for, and stayed green when the URI was printed whole.
SECRET_PASSWORD = "s3cr3t-9f"


class TestCredentialGuard:
    def _guard(self, tmp_path, **overrides):
        env = {**CREDS, **overrides}
        return _bash(_script("Guard the target"), env=env, cwd=tmp_path)

    def test_a_complete_target_passes_and_prints_the_host_not_the_credential(
            self, tmp_path):
        done = self._guard(tmp_path)
        assert done.returncode == 0, done.stderr
        assert "rediss://falkordb.example" in done.stdout
        assert SECRET_PASSWORD not in done.stdout
        assert SECRET_PASSWORD not in done.stderr

    @pytest.mark.parametrize("missing", ["TORTOISE_DB_URI", "SUPABASE_URL",
                                         "SUPABASE_SERVICE_KEY"])
    def test_a_missing_credential_is_a_red_run(self, tmp_path, missing):
        """⛔ THE FAIL-OPEN THIS EXISTS FOR: with no `TORTOISE_DB_URI` the tool
        falls back to an EMBEDDED database, and a fresh runner's empty local store
        reports `0 graphs, nothing to reap, exit 0` — a green sweep of a database
        that is not production."""
        done = self._guard(tmp_path, **{missing: ""})
        assert done.returncode != 0
        assert "::error::" in done.stdout
        if missing == "TORTOISE_DB_URI":
            assert "EMBEDDED" in done.stdout

    def test_a_non_falkordb_scheme_is_refused(self, tmp_path):
        done = self._guard(tmp_path, TORTOISE_DB_URI="sqlite:///tmp/x.db")
        assert done.returncode != 0
        assert "not a FalkorDB server URI" in done.stdout


# ── 4. the verdict (a dead sweep must not look green) ────────────────────────

def _plan(reapable: int = 1, total: int | None = None, unresolved: int = 0,
          unreadable: int = 0) -> dict:
    graphs = []
    for i in range(reapable):
        graphs.append({"graph": f"org_a{i}", "bucket": "reapable", "org_id": f"a{i}",
                       "org_name": f"agent-{i}", "gid": None, "node_count": 0})
    for i in range(unresolved):
        graphs.append({"graph": f"orphan{i}", "bucket": "refused-unresolved",
                       "org_id": None, "org_name": None, "gid": None,
                       "node_count": None})
    for i in range(unreadable):
        graphs.append({"graph": f"org_u{i}", "bucket": "refused-unreadable",
                       "org_id": f"u{i}", "org_name": f"e2e-{i}", "gid": None,
                       "node_count": None})
    counts: dict[str, int] = {}
    for g in graphs:
        counts[g["bucket"]] = counts.get(g["bucket"], 0) + 1
    return {"graphs": graphs,
            "counts": counts,
            "total": len(graphs) if total is None else total}


def _execute(deleted: int, failed: int = 0, freed: int | None = 3_000_000) -> dict:
    outcome = [{"graph": f"org_a{i}", "org_id": f"a{i}", "deleted": True,
                "error": None} for i in range(deleted)]
    outcome += [{"graph": f"org_b{i}", "org_id": f"b{i}", "deleted": False,
                 "error": "GRAPH.DELETE failed"}
                for i in range(failed)]
    return {"report": {}, "outcome": outcome, "failed": failed,
            "memory": {"used_before": None if freed is None else 800_000_000,
                       "used_after": None if freed is None else 800_000_000 - freed,
                       "bytes_freed": freed}}


def _verdict(tmp_path, *, plan, verify, execute=None, plan_rc=0, verify_rc=0,
             execute_rc="0", band=200):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    if plan is not None:
        (work / "reaper-plan.json").write_text(json.dumps(plan))
    if verify is not None:
        (work / "reaper-verify.json").write_text(json.dumps(verify))
    if execute is not None:
        (work / "reaper-execute.json").write_text(json.dumps(execute))
    summary = work / "summary.md"
    done = _bash(_script("Verdict"), env={
        "REAPER_WORKDIR": str(work),
        "PLAN_RC": str(plan_rc),
        "VERIFY_RC": str(verify_rc),
        "EXECUTE_RC": execute_rc,
        "REAPER_TOTAL_BAND": str(band),
        "GITHUB_STEP_SUMMARY": str(summary),
    }, cwd=tmp_path)
    record = json.loads((work / "reaper-verdict.json").read_text())
    return done, record, (summary.read_text() if summary.exists() else "")


class TestVerdict:
    def test_a_converged_sweep_is_green_and_records_the_numbers(self, tmp_path):
        done, record, summary = _verdict(
            tmp_path, plan=_plan(reapable=2), verify=_plan(reapable=0),
            execute=_execute(deleted=2))
        assert done.returncode == 0, done.stdout + done.stderr
        assert record["verdict"] == "green"
        assert record["deleted"] == 2
        assert record["bytes_freed"] == 3_000_000
        # The MB figure is freed, which is the only reading the delete-measured
        # cost is honest about.
        assert "used_memory freed: 2.9 MB" in summary

    def test_a_residue_after_the_sweep_is_a_failure(self, tmp_path):
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=1), verify=_plan(reapable=1),
            execute=_execute(deleted=0))
        assert done.returncode != 0
        assert any("still reapable AFTER" in r for r in record["reasons"])

    def test_a_failed_deletion_is_a_failure(self, tmp_path):
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=1), verify=_plan(reapable=0),
            execute=_execute(deleted=0, failed=1))
        assert done.returncode != 0
        assert any("FAILED" in r for r in record["reasons"])

    def test_a_refused_plan_is_a_failure(self, tmp_path):
        done, record, _ = _verdict(
            tmp_path, plan=None, verify=None, plan_rc=2, verify_rc=-1,
            execute_rc="")
        assert done.returncode != 0
        assert any("no report" in r for r in record["reasons"])

    def test_a_target_that_answers_with_zero_graphs_is_a_failure(self, tmp_path):
        """A wrong target that ANSWERS is indistinguishable from an empty one."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=0, total=0),
            verify=_plan(reapable=0, total=0), execute=_execute(deleted=0))
        assert done.returncode != 0
        assert any("0 graphs" in r for r in record["reasons"])

    def test_an_unreadable_emptiness_read_is_a_failure(self, tmp_path):
        """`refused-unreadable` means the sweep is partly BLIND — a defect in the
        sweep, so it fails. `refused-unresolved` is the tool working as designed,
        so it is a WARNING and the run stays green."""
        done, record, summary = _verdict(
            tmp_path, plan=_plan(reapable=1, unreadable=1),
            verify=_plan(reapable=0, unreadable=1), execute=_execute(deleted=1))
        assert done.returncode != 0
        assert any("refused-unreadable" in r or "partly blind" in r
                   for r in record["reasons"])

    def test_an_unresolved_refusal_is_reported_not_failed(self, tmp_path):
        done, record, summary = _verdict(
            tmp_path, plan=_plan(reapable=1, unresolved=3),
            verify=_plan(reapable=0, unresolved=3), execute=_execute(deleted=1))
        assert done.returncode == 0, record["reasons"]
        assert record["verdict"] == "green"
        assert any("REFUSED as unresolved" in n for n in record["notes"])
        assert "refused-unresolved: 3" in summary
        assert "::warning::" in done.stdout

    def test_a_count_above_the_band_is_a_failure(self, tmp_path):
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=1, total=201),
            verify=_plan(reapable=0, total=200), execute=_execute(deleted=1), band=200)
        assert done.returncode != 0
        assert any("above the stated band" in r for r in record["reasons"])

    def test_a_rehearsal_deletes_nothing_and_is_not_failed_for_it(self, tmp_path):
        """`workflow_dispatch` with execute=false runs plan + verify only. The
        residue invariant is meaningless there, and reporting it would file an
        issue on every rehearsal."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=4), verify=_plan(reapable=4),
            execute=None, execute_rc="")
        assert done.returncode == 0, record["reasons"]
        assert record["execute_ran"] is False
        assert record["deleted"] == 0

    def test_an_unmeasured_memory_delta_is_never_zero(self, tmp_path):
        """A 0 would be read downstream as 'freed nothing', which is the opposite
        of 'not measured'."""
        done, record, summary = _verdict(
            tmp_path, plan=_plan(reapable=1), verify=_plan(reapable=0),
            execute=_execute(deleted=1, freed=None))
        assert done.returncode == 0
        assert record["bytes_freed"] is None
        assert "unmeasured" in summary


# ── 5. the arms the first review found missing ───────────────────────────────

class TestReviewFindings:
    """Each case here is a state that was GREEN before this class existed.

    They are kept together (rather than folded into `TestVerdict`) so the reader
    can see the shape they share: **a sweep that did nothing, or could not be
    read, must never look like a sweep that worked.**
    """

    def test_a_control_plane_that_resolves_NOTHING_is_a_failure(self, tmp_path):
        """⛔ The `total == 0` guard's mirror image. A control plane that ANSWERS
        (a restored/empty project, a key scoped elsewhere, namespace drift) turns
        EVERY graph into `refused-unresolved`: `reapable` is 0, the total is
        healthy, every other arm passes — and the sweep silently deletes nothing
        forever while the leak keeps running. Measured green before this arm."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(unresolved=138, total=138),
            verify=_plan(unresolved=138, total=138), execute=_execute(deleted=0))
        assert done.returncode != 0, "an all-unresolved control plane read as clean"
        assert any("resolved none" in r for r in record["reasons"])

    def test_a_CLUSTER_of_unresolved_orphans_is_still_only_a_warning(self, tmp_path):
        """The discriminator for the arm above: SOME orphans is the tool working as
        designed (adjudication), so it must not turn red."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=2, unresolved=3),
            verify=_plan(reapable=0, unresolved=3), execute=_execute(deleted=2))
        assert done.returncode == 0, record["reasons"]

    def test_a_killed_execute_pass_is_a_failure_even_with_a_clean_verify(
            self, tmp_path):
        """A pass killed by the runner (137) or by `timeout` writes NO document, and
        the tool had already deleted some graphs — so `deleted` would read 0 while
        `verify` reads 0 reapable. Verified green before this arm: plan reapable=3,
        verify reapable=0, EXECUTE_RC=137, no execute document."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=3), verify=_plan(reapable=0),
            execute=None, execute_rc="137")
        assert done.returncode != 0, "a killed reap read as a clean sweep"
        assert any("execute pass produced no report" in r for r in record["reasons"])

    def test_an_execute_exit_outside_the_reap_verdicts_is_a_failure(self, tmp_path):
        """Exit 2 IS reachable on the execute path (`main` re-runs `build_report`),
        so a refused reap must be read, not assumed impossible."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=1), verify=_plan(reapable=0),
            execute=_execute(deleted=0), execute_rc="2")
        assert done.returncode != 0
        assert any("exited 2" in r for r in record["reasons"])

    def test_a_refused_VERIFY_pass_is_a_failure_even_on_a_rehearsal(self, tmp_path):
        """The residue invariant is rehearsal-exempt; the verify pass's own health
        is NOT — a partial view is never a clean one, and a rehearsal that cannot
        read the estate must not read as green (it would also close the incident)."""
        done, record, _ = _verdict(
            tmp_path, plan=_plan(reapable=4), verify=None, execute=None,
            execute_rc="", verify_rc=2)
        assert done.returncode != 0, "a refused verify read as clean on a rehearsal"
        assert any("verify pass produced no report" in r for r in record["reasons"])

    def test_a_rehearsal_cannot_CLOSE_the_incident_issue(self, tmp_path):
        """⛔ A rehearsal is green by construction, so gating the resolve arm on the
        verdict alone let a manual `execute: false` run close a live incident and
        comment "a clean scheduled run" about a run that never swept anything.

        Behaves without the network: the resolve arm sources
        `.github/scripts/auto-file-issue.sh`, whose dedupe search uses `gh` — a
        stub that always fails makes an ATTEMPT loud (non-zero) and a SKIP silent
        (zero)."""
        script = _script("Resolve the adjudication issue")
        # A `gh` that always fails: the dedupe search in the resolve arm then
        # REFUSES (by design, #3907), so an ATTEMPT is non-zero and a SKIP is 0.
        stub = tmp_path / "stub"
        stub.mkdir()
        gh = stub / "gh"
        gh.write_text("#!/bin/sh\nexit 1\n")
        gh.chmod(0o755)
        for execute_ran, expect_attempt in ((False, False), (True, True)):
            work = tmp_path / f"work-{execute_ran}"
            work.mkdir()
            (work / "reaper-verdict.json").write_text(json.dumps({
                "verdict": "green", "reasons": [], "notes": [], "counts": {},
                "execute_ran": execute_ran}))
            env = {"REAPER_WORKDIR": str(work), "GH_TOKEN": "stub",
                   "RUN_URL": "https://example/run/1",
                   "PATH": f"{stub}:{os.environ.get('PATH', '')}"}
            done = _bash(script, env=env, cwd=ROOT)
            assert (done.returncode != 0) is expect_attempt, (
                f"execute_ran={execute_ran}: expected "
                f"{'an attempt' if expect_attempt else 'a skip'}; "
                f"rc={done.returncode} out={done.stdout} err={done.stderr}")
