"""Hermetic mutation tests for tools/run_reaper.py (#6868).

No network, no Docker, no `gh`. A stub `gh` binary reads every fixture from
``$GH_STUB_DIR`` and records every argv; the tool is driven entirely through its
``RUN_REAPER_GH`` / ``RUN_REAPER_NOW`` seams, so the clock is fixed and the
GitHub surface is deterministic.

Run standalone:      python3 tests/test_run_reaper.py
Run under pytest:    python3 -m pytest tests/test_run_reaper.py -q

Coverage (the issue's own mutations, plus the guard that matters):
  * a run genuinely OVER its own derived bound -> action=cancel, and on --apply
    the API is called and the run is cancelled
  * the SAME run under dry-run (the default) -> reported, NOT cancelled
  * a run NOT over its bound -> skip: in-budget, never cancelled
  * a run EXACTLY AT its bound -> not cancelled (pins the strict `>`, so `>=`
    turns it RED)
  * a run OBJECT whose id is not the id asked for -> skip: run-unreadable, never
    cancelled (pins the read_run id-match guard; dropping it turns it RED)
  * a run whose run_started_at is absent -> skip: start-time-unreadable, never
    judged from created_at (pins the no-fallback clock; restoring the fallback
    turns it RED)
  * **a STALE listing** (the listing says in_progress, the individual run OBJECT
    says completed) -> skip: stale-listing, never cancelled. This is the case the
    hand-intervention got wrong; the mutation (drop the object-status guard)
    turns it RED.
  * a run whose bound cannot be derived -> skip: bound-underivable, never
    cancelled (empty green population, and a pending shard with no green sample)
  * a FULLY-HUNG matrix (every shard in_progress, green covering each) -> cancel;
    the old `target_completed == 0` gate declined the tool's PRIMARY case as
    bound-underivable
  * a fully-hung matrix with a pool shard that has no green sample -> still
    bound-underivable (the newly-allowed path stays fail-closed)
  * an scp-like origin with a DIGIT-LEADING owner (`git@github.com:1inch/foo.git`)
    resolves to `1inch/foo`, not a stripped `foo`; lookalike hosts still rejected
  * an argparse usage error (a bad/abbreviated flag) exits 3, never 2, so 2 means
    INCOMPLETE and only 2 means INCOMPLETE
  * an unreadable run object -> skip: run-unreadable, never cancelled
  * an unreadable job list -> skip: jobs-unreadable, never cancelled
  * the bound is DERIVED PER RUN: the same elapsed is in-budget under a slow
    workflow's green population and overrun under a fast one
  * a cancel REFUSED by the API (the `Cannot cancel …` class) -> exit 4, while a
    sibling cancellation in the same run still lands
  * an unreadable candidate listing with no --run -> INCOMPLETE, exit 2
  * an unreadable candidate listing WITH --run -> that run is still read and
    cancelled (the listing is an index, not an input to the decision)
  * a tools/run_reaper.py-only diff stays covered (TOOL_CARVEOUTS)
"""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_reaper.py"
PYTHON = sys.executable

#: A fixed clock. The tool reads it through RUN_REAPER_NOW, so every elapsed
#: figure below is exact and the suite is timezone/clock independent.
NOW = 1_800_000_000


def _load_tool_module():
    """Import tools/run_reaper.py as a module (for the pure-unit surfaces)."""
    spec = importlib.util.spec_from_file_location("run_reaper_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GH_STUB = r"""#!/usr/bin/env bash
set -u
d="${GH_STUB_DIR:?GH_STUB_DIR unset}"
printf '%s\n' "$*" >> "$d/calls"

status=""; workflow=""; prev=""
for a in "$@"; do
  [ "$prev" = "--status" ] && status="$a"
  [ "$prev" = "--workflow" ] && workflow="$a"
  prev="$a"
done

case "${1:-} ${2:-}" in
  "run list")
    if [ "$status" = "in_progress" ] && [ -n "${GH_STUB_LIST_FAIL:-}" ]; then
      echo "gh-stub: list transport failure" >&2; exit 1
    fi
    case "$status" in
      success)
        if [ -n "$workflow" ] && [ -f "$d/success_ids_$workflow" ]; then
          cat "$d/success_ids_$workflow"
        elif [ -f "$d/success_ids" ]; then cat "$d/success_ids"; fi ;;
      in_progress) [ -f "$d/inprogress_ids" ] && cat "$d/inprogress_ids" ;;
    esac
    exit 0 ;;
esac

path=""
for a in "$@"; do
  case "$a" in repos/*) path="$a" ;; esac
done
[ -n "$path" ] || { echo "gh-stub: no repos/ path in: $*" >&2; exit 1; }

case "$path" in
  */cancel)
    rid="$(printf '%s' "$path" | sed -E 's#.*/runs/([0-9]+)/cancel#\1#')"
    if [ -f "$d/cancel_fail_$rid" ]; then
      echo "Cannot cancel a workflow run that is not in progress." >&2; exit 1
    fi
    if [ -f "$d/cancel_incomplete_$rid" ]; then
      # An EMPTY body on exit 0 is the documented Incomplete shape: the POST's
      # outcome is genuinely unreadable, so the cancel is AMBIGUOUS — it may
      # have landed. Used to pin the exit-6 path (a fault after a cancel may
      # have been issued must never be reported as "nothing was cancelled").
      exit 0
    fi
    printf '%s\n' "$rid" >> "$d/cancelled"
    echo "{}"; exit 0 ;;
  */jobs*)
    rid="$(printf '%s' "$path" | sed -E 's#.*/runs/([0-9]+)/jobs.*#\1#')"
    f="$d/jobs_$rid.json"
    [ -f "$f" ] || { echo "gh-stub: no jobs fixture for $rid" >&2; exit 1; }
    cat "$f"; exit 0 ;;
  */runs/*)
    rid="$(printf '%s' "$path" | sed -E 's#.*/runs/([0-9]+)#\1#')"
    f="$d/run_$rid.json"
    [ -f "$f" ] || { echo "gh-stub: no run fixture for $rid" >&2; exit 1; }
    cat "$f"; exit 0 ;;
esac
echo "gh-stub: unexpected argv: $*" >&2; exit 1
"""


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def job(name: str, secs: int | None, *, status: str = "completed",
        conclusion: str | None = "success", started_s: int | None = None) -> dict:
    """A job fixture whose duration is exactly ``secs``.

    ``status="in_progress"`` yields a job with no ``completed_at`` — an
    UNFINISHED shard, which is what the bound pool is computed over.

    ``started_s`` is the shard's OWN age in seconds and defaults to ``secs``. A
    job with NEITHER has NOT STARTED: ``started_at`` is None, meaning it is
    queued/waiting behind a starved fleet. The fixture must never FABRICATE a
    start clock for that case — a fabricated one-second-old clock is precisely
    what let ``decide()`` cancel a run whose shard had never started, and let
    this suite assert that as correct.
    """
    age = started_s if started_s is not None else secs
    started = iso(NOW - age) if age is not None else None
    completed = iso(NOW) if status == "completed" else None
    return {"name": name, "status": status,
            "conclusion": (conclusion if status == "completed" else None),
            "started_at": started, "completed_at": completed}


def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class RunReaperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run-reaper-test-"))
        self.gh_dir = self.tmp / "gh"
        self.gh_dir.mkdir()
        self.gh = _write_exec(self.gh_dir / "gh", GH_STUB)

    # ── fixtures ────────────────────────────────────────────────────────────

    def make_run(self, run_id, *, status="in_progress", elapsed_s=0, workflow_id=7,
                 branch="feat/x", conclusion=None, object_id=None,
                 omit_run_started_at=False) -> None:
        started = iso(NOW - elapsed_s)
        payload = {"id": int(run_id if object_id is None else object_id),
                   "status": status, "conclusion": conclusion,
                   "head_branch": branch, "workflow_id": workflow_id,
                   "created_at": started,
                   "html_url": f"https://example.invalid/runs/{run_id}"}
        if not omit_run_started_at:
            payload["run_started_at"] = started
        (self.gh_dir / f"run_{run_id}.json").write_text(json.dumps(payload))

    def make_jobs(self, run_id, jobs) -> None:
        (self.gh_dir / f"jobs_{run_id}.json").write_text(
            json.dumps({"total_count": len(jobs), "jobs": jobs}))

    def set_ids(self, name: str, ids) -> None:
        (self.gh_dir / name).write_text("".join(f"{i}\n" for i in ids))

    def cancelled(self) -> list[str]:
        path = self.gh_dir / "cancelled"
        if not path.exists():
            return []
        return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]

    def calls(self) -> str:
        path = self.gh_dir / "calls"
        return path.read_text() if path.exists() else ""

    def green_population(self, workflow_id, *, maxes, n=3):
        """``maxes`` maps a shard name -> its slowest green duration."""
        ids = [f"{workflow_id}{i:02d}" for i in range(1, n + 1)]
        self.set_ids(f"success_ids_{workflow_id}", ids)
        for gid in ids:
            self.make_run(gid, status="completed", conclusion="success",
                          elapsed_s=100, workflow_id=workflow_id)
            self.make_jobs(gid, [job(name, secs) for name, secs in maxes.items()])
        return ids

    def repo_with_origin(self, url: str) -> Path:
        """A real (local, network-free) git repo whose origin is exactly ``url``.

        `git remote get-url origin` is a purely local read, so this exercises the
        tool's REAL subprocess path against real git without any network.
        """
        d = Path(tempfile.mkdtemp(prefix="reaper-origin-", dir=self.tmp))
        subprocess.run(["git", "init", "-q", str(d)], check=True,
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", str(d), "remote", "add", "origin", url],
                       check=True, capture_output=True, text=True)
        return d

    # ── driver ──────────────────────────────────────────────────────────────

    def run_tool(self, args=None, *, extra_env=None):
        env = dict(os.environ)
        env["RUN_REAPER_GH"] = str(self.gh)
        env["GH_STUB_DIR"] = str(self.gh_dir)
        env["RUN_REAPER_NOW"] = str(NOW)
        if extra_env:
            env.update({k: str(v) for k, v in extra_env.items()})
        cmd = [PYTHON, str(TOOL), "--repo", "owner/repo", *(args or [])]
        return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

    def rows(self, args=None, extra_env=None):
        """Run with --json; return ``{run_id: decision}``. Asserts exit 0 or 4."""
        res = self.run_tool(["--json", *(args or [])], extra_env=extra_env)
        self.assertIn(res.returncode, (0, 4), res.stderr + res.stdout)
        return {str(d["run_id"]): d for d in json.loads(res.stdout)["decisions"]}

    # ── the mutations the issue names ───────────────────────────────────────

    def test_overrun_is_cancelled_on_apply(self):
        """A run past its OWN derived bound is cancelled (and the API is called)."""
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=25740)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "cancel")
        self.assertEqual(row["reason"], "verified-overrun")
        self.assertEqual(row["bound_s"], 2144)   # max(floor=1200, 2*1072)
        self.assertEqual(row["bound_shard"], "test (g)")
        self.assertEqual(row["elapsed_s"], 25740)
        self.assertEqual(self.cancelled(), [], "dry-run must mutate nothing")

        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), ["111"])
        self.assertIn("actions/runs/111", self.calls(), "must read the run individually")

    def test_overrun_is_only_reported_on_the_default_dry_run(self):
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=25740)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})
        res = self.run_tool(["--run", "111"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])
        self.assertIn("WOULD CANCEL", res.stdout)
        self.assertIn("DRY-RUN", res.stdout)

    def test_in_budget_run_is_not_cancelled(self):
        """A genuinely-still-running run inside its bound is left alone."""
        self.make_run("111", elapsed_s=1500, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=1500)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})
        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "in-budget")
        self.assertEqual(row["bound_s"], 2144)
        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_elapsed_exactly_at_the_bound_is_not_cancelled(self):
        """The overrun test is STRICT (`>`): elapsed == bound is still in budget.

        Pins the off-by-one control — `>=` would cancel a run that is exactly
        at its own derived bound.
        """
        self.make_run("111", elapsed_s=2144, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=2144)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["bound_s"], 2144)
        self.assertEqual(row["elapsed_s"], 2144)
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "in-budget")
        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_stale_listing_is_not_cancelled(self):
        """THE TEST THE HAND-INTERVENTION FAILED.

        The listing offers run 999 as ``in_progress``; the run OBJECT says
        ``completed``. Its elapsed is far past any derived bound, so a tool that
        trusted the list (or the elapsed figure) would cancel a finished run.
        It must skip: stale-listing.
        """
        self.set_ids("inprogress_ids", [999])          # the LIST claims in_progress
        self.make_run("999", status="completed", conclusion="failure",
                      elapsed_s=25740, workflow_id=7)   # the OBJECT says completed
        self.make_jobs("999", [job("test (a)", 600), job("test (g)", 1072)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows([])["999"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "stale-listing")
        self.assertIn("completed", row["detail"])

        res = self.run_tool(["--apply"])               # candidates come from the listing
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [], "a stale listing must never cancel")
        # The stale entry was disproven by an INDIVIDUAL read, not a list re-read.
        self.assertIn("actions/runs/999", self.calls())

    def test_underivable_bound_empty_green_population_is_not_cancelled(self):
        """No green history -> no bound -> no cancel (fail closed)."""
        self.make_run("555", elapsed_s=99999, workflow_id=7)
        self.make_jobs("555", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=5000)])
        # success_ids intentionally absent -> green_run_ids returns []
        row = self.rows(["--run", "555"])["555"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "bound-underivable")
        self.assertIsNone(row["bound_s"])
        res = self.run_tool(["--run", "555", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_underivable_bound_missing_pending_shard_sample_is_not_cancelled(self):
        """A PENDING shard with no green sample makes the bound underivable.

        ``test (g)`` is unfinished and the green population never ran it, so no
        honest bound covers the shard that might be the wedge. Fail closed.
        """
        self.make_run("555", elapsed_s=99999, workflow_id=7)
        self.make_jobs("555", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=5000)])
        self.green_population(7, maxes={"test (a)": 500})   # no "test (g)" sample
        row = self.rows(["--run", "555"])["555"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "bound-underivable")
        res = self.run_tool(["--run", "555", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_fully_hung_matrix_is_reaped(self):
        """A matrix where EVERY shard is still in_progress IS reapable.

        This is the tool's PRIMARY case: a globally-hung matrix (dead dependency,
        expired token, a deadlock in every shard) has no completed job at all.
        The bound is nevertheless fully derivable — the target supplies only the
        shard SET and which shards are unfinished, and every ceiling is measured
        on the green population — so it must be judged, not declined.
        """
        self.make_run("111", elapsed_s=7200, workflow_id=7)
        self.make_jobs("111", [job("test (a)", None, status="in_progress", started_s=7200),
                               job("test (g)", None, status="in_progress", started_s=7200)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "cancel")
        self.assertEqual(row["reason"], "verified-overrun")
        self.assertEqual(row["bound_s"], 2144)          # max(floor, 2*1072)
        self.assertEqual(row["bound_shard"], "test (g)")
        self.assertEqual(row["elapsed_s"], 7200)
        self.assertEqual(self.cancelled(), [], "dry-run must mutate nothing")

        res = self.run_tool(["--run", "111"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("WOULD CANCEL", res.stdout)
        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), ["111"])

    def test_a_never_started_shard_is_not_reaped(self):
        """A run waiting on a QUEUED shard is a CAPACITY WAIT, not a wedge.

        The shard holds no runner slot and has made no progress to measure, so
        cancelling reclaims nothing — and the run's own clock is queue wait, not
        work. This is the tool's DOMINANT waiting state, so it must fail closed.
        Before the per-shard clock this run was cancelled as "verified-overrun";
        the fixture used to fabricate a one-second-old start clock for exactly
        this shape, which is why the suite asserted the cancel as correct.
        """
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress")])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "bound-underivable")
        self.assertIsNone(row["shard_elapsed_s"])

        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [], "a queued shard must never be reaped")

    def test_a_just_started_shard_inside_a_long_run_is_not_reaped(self):
        """THE UNIT MISMATCH, pinned.

        A run genuinely 25740s old whose deciding shard started 60s ago is
        HEALTHY: it was queued behind a starved fleet. The bound is a PER-SHARD
        ceiling, so it must be compared against the SHARD's own clock. Judging it
        on the run clock cancelled healthy runs — the exact class the tool's own
        start-clock comment warns about.
        """
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=60)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "in-budget")
        self.assertEqual(row["shard_elapsed_s"], 60)
        self.assertEqual(row["elapsed_s"], 25740, "the run clock is still reported")

        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [], "a freshly-started shard is healthy")

    def test_fully_hung_matrix_without_a_green_sample_stays_underivable(self):
        """The newly-allowed path is STILL fail-closed on coverage.

        Every shard is unfinished (so the old `target_completed == 0` gate no
        longer applies), but ``test (g)`` has never gone green: no honest bound
        covers the shard that might be the wedge, so the run is skipped.
        """
        self.make_run("555", elapsed_s=99999, workflow_id=7)
        # BOTH shards need a clock, or derive_bound() returns at the UNSTARTED
        # guard and never reaches the missing-green-coverage guard this test is
        # named for — the claim would be unexercised (and a neutralised coverage
        # guard would leave this test green).
        self.make_jobs("555", [job("test (a)", None, status="in_progress", started_s=99999),
                               job("test (g)", None, status="in_progress", started_s=99999)])
        self.green_population(7, maxes={"test (a)": 500})   # no "test (g)" sample
        row = self.rows(["--run", "555"])["555"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "bound-underivable")
        res = self.run_tool(["--run", "555", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    # ── fail-closed reads ───────────────────────────────────────────────────

    def test_unreadable_run_object_is_skipped_not_cancelled(self):
        # No run_777.json -> the individual read fails.
        row = self.rows(["--run", "777"])["777"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "run-unreadable")
        res = self.run_tool(["--run", "777", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_run_object_with_a_different_id_is_skipped_not_cancelled(self):
        """The run OBJECT must BE the run asked for.

        A response carrying a different `id` — a stale, proxied or redirected
        read — is never decided on: it is skipped as run-unreadable and
        --apply cancels nothing. Without the id-match guard the tool judges,
        and cancels, a run it was not asked about.
        """
        self.make_run("111", elapsed_s=25740, workflow_id=7, object_id=999)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress")])
        self.green_population(7, maxes={"test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "run-unreadable")
        self.assertIn("expected", row["detail"])
        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_missing_run_started_at_is_skipped_not_judged_from_created_at(self):
        """An absent start clock SKIPS — created_at is NOT a substitute.

        created_at is always <= run_started_at, so falling back to it inflates
        elapsed by the whole queue wait. This run was queued 2h behind a starved
        fleet but its start clock is unknown; judging it from created_at would
        call it a 2h overrun and cancel a healthy run.
        """
        self.make_run("111", elapsed_s=7200, workflow_id=7, omit_run_started_at=True)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress")])
        self.green_population(7, maxes={"test (g)": 1072})

        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "start-time-unreadable")
        self.assertIn("created_at", row["detail"])
        res = self.run_tool(["--run", "111", "--apply"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), [])

    def test_unreadable_jobs_is_skipped_not_cancelled(self):
        self.make_run("888", elapsed_s=99999, workflow_id=7)
        # No jobs_888.json -> the jobs read fails.
        row = self.rows(["--run", "888"])["888"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "jobs-unreadable")
        self.assertEqual(self.cancelled(), [])

    def test_candidate_listing_unreadable_without_run_is_incomplete(self):
        res = self.run_tool(["--apply"], extra_env={"GH_STUB_LIST_FAIL": "1"})
        self.assertEqual(res.returncode, 2, res.stderr + res.stdout)
        self.assertIn("INCOMPLETE", res.stderr)
        self.assertEqual(self.cancelled(), [])

    def test_truncated_listing_is_warned_but_not_fatal(self):
        # A listing at its cap may be truncated; that can only MISS a candidate,
        # so it warns rather than blocking the reap with INCOMPLETE.
        self.set_ids("inprogress_ids", [901, 902])
        res = self.run_tool(["--list-limit", "2"])
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertIn("TRUNCATED", res.stderr)

    # ── per-run derivation, not a global constant ───────────────────────────

    def test_bound_is_derived_from_the_runs_own_workflow(self):
        """The same elapsed is in-budget for a slow workflow and overrun for a fast one.

        A global constant cannot satisfy both: this is the whole reason the bound
        is derived per run.
        """
        # workflow 7: slowest green test (g) = 1000s -> bound 2000s
        self.green_population(7, maxes={"test (g)": 1000})
        # workflow 8: slowest green test (g) = 5000s -> bound 10000s
        self.green_population(8, maxes={"test (g)": 5000})

        self.make_run("111", elapsed_s=3000, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=3000)])
        self.make_run("222", elapsed_s=3000, workflow_id=8)
        self.make_jobs("222", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=3000)])

        rows = self.rows(["--run", "111", "--run", "222"])
        self.assertEqual(rows["111"]["action"], "cancel")
        self.assertEqual(rows["111"]["bound_s"], 2000)
        self.assertEqual(rows["222"]["action"], "skip")
        self.assertEqual(rows["222"]["reason"], "in-budget")
        self.assertEqual(rows["222"]["bound_s"], 10000)

    # ── cancellation refusal is not a crash ─────────────────────────────────

    def test_cancel_refused_is_exit_4_while_sibling_cancels_land(self):
        """The `Cannot cancel …` class is a per-run refusal, and exit 4."""
        self.green_population(7, maxes={"test (g)": 1072})
        for rid in ("111", "222"):
            self.make_run(rid, elapsed_s=25740, workflow_id=7)
            self.make_jobs(rid, [job("test (a)", 600),
                                 job("test (g)", None, status="in_progress", started_s=25740)])
        (self.gh_dir / "cancel_fail_222").write_text("refuse\n")

        res = self.run_tool(["--json", "--run", "111", "--run", "222", "--apply"])
        self.assertEqual(res.returncode, 4, res.stderr + res.stdout)
        rows = {str(d["run_id"]): d for d in json.loads(res.stdout)["decisions"]}
        self.assertEqual(self.cancelled(), ["111"], "the sibling cancel still landed")
        self.assertEqual(rows["222"]["cancel_result"], "refused")
        self.assertIn("REFUSED", rows["222"]["detail"])

    def test_an_ambiguous_cancel_post_is_exit_6_not_a_nothing_was_cancelled_claim(self):
        """A fault AFTER a cancel was ISSUED must not claim nothing happened.

        A cancel POST whose outcome is unreadable is AMBIGUOUS — it may have
        landed. Reporting EXIT_INCOMPLETE's "Nothing was cancelled" over it is a
        false statement, and discarding the report would hide the cancels that
        DID land earlier in the same loop. Mirrors branch_reaper.py's
        EXIT_INCOMPLETE_AFTER_DELETE.
        """
        self.green_population(7, maxes={"test (g)": 1072})
        for rid in ("111", "222"):
            self.make_run(rid, elapsed_s=25740, workflow_id=7)
            self.make_jobs(rid, [job("test (a)", 600),
                                 job("test (g)", None, status="in_progress", started_s=25740)])
        (self.gh_dir / "cancel_incomplete_222").write_text("x\n")

        res = self.run_tool(["--json", "--run", "111", "--run", "222", "--apply"])
        self.assertEqual(res.returncode, 6, res.stderr + res.stdout)
        self.assertNotIn("Nothing was cancelled", res.stderr)
        # THE SIBLING CANCEL STILL LANDED, so the report must survive the fault.
        self.assertEqual(self.cancelled(), ["111"])
        rows = {str(d["run_id"]): d for d in json.loads(res.stdout)["decisions"]}
        self.assertEqual(rows["111"]["cancel_result"], "cancelled")
        self.assertEqual(rows["222"]["cancel_result"], "unknown")
        self.assertIn("AMBIGUOUS", rows["222"]["detail"])

    def test_a_fault_after_a_cancel_was_issued_is_never_nothing_was_cancelled(self):
        """The TOP-LEVEL handler must not claim "nothing was cancelled" once a
        cancel POST was ISSUED.

        The per-cancel guard covers a fault INSIDE ``cancel_run()``; this covers
        one AFTER it — the report write itself (a broken pipe once the output
        exceeds the stdout buffer, or ENOSPC). ``branch_reaper.py`` guards that
        class with ``_LANDED`` in its top-level handler; without the same flag
        here, landed cancels were reported as "nothing" (reproduced: 80 landed,
        80 claimed as nothing).
        """
        mod = _load_tool_module()
        self.assertFalse(mod._CANCEL_ISSUED, "a fresh process has issued nothing")

        def boom(_args):
            # The POST has been issued; NOW the report render faults.
            mod._CANCEL_ISSUED = True
            raise mod.Incomplete("broken pipe while rendering the report")

        orig, mod._run_main = mod._run_main, boom
        try:
            rc = mod.main(["--repo", "owner/repo"])
        finally:
            mod._run_main = orig
        self.assertEqual(rc, mod.EXIT_INCOMPLETE_AFTER_CANCEL)
        self.assertNotEqual(rc, mod.EXIT_INCOMPLETE)

        # And the SAME fault with NO cancel issued is still the plain exit 2,
        # so the flag distinguishes the two rather than always escalating.
        def boom2(_args):
            raise mod.Incomplete("the listing was unreadable")

        orig, mod._run_main = mod._run_main, boom2
        try:
            rc2 = mod.main(["--repo", "owner/repo"])
        finally:
            mod._run_main = orig
        self.assertEqual(rc2, mod.EXIT_INCOMPLETE)

    def test_the_issued_flag_is_set_BEFORE_the_post(self):
        """THE ORDERING IS THE MECHANISM, so drive the REAL path.

        A test that sets the flag by hand pins the handler, not the ordering —
        and then moving the assignment to AFTER ``cancel_run(...)`` (or deleting
        it, which literally reinstates the original defect) passes the whole
        suite green. So let the POST actually land on the stub, fault the
        RENDER that follows it, and assert exit 6 AND that the cancel was
        recorded. This turns red if the flag is not set before the POST.
        """
        mod = _load_tool_module()
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=25740)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})
        env = {"RUN_REAPER_GH": str(self.gh), "GH_STUB_DIR": str(self.gh_dir),
               "RUN_REAPER_NOW": str(NOW)}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)

        def boom(*_a, **_k):
            raise OSError("broken pipe while rendering the report")

        orig = mod._render_human
        mod._render_human = boom
        try:
            rc = mod.main(["--repo", "owner/repo", "--run", "111", "--apply"])
        finally:
            mod._render_human = orig
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

        self.assertEqual(rc, mod.EXIT_INCOMPLETE_AFTER_CANCEL)
        # The POST really landed on the stub before the render faulted.
        self.assertEqual(self.cancelled(), ["111"])

    def test_the_internal_error_arm_also_honours_the_issued_flag(self):
        """The BaseException arm needs its OWN pin.

        The fault used above is Incomplete/OSError, caught by the EARLIER arm, so
        the later arm is never reached and neutering its check leaves the suite
        green. Both arms must answer 6 once a POST has been issued.
        """
        mod = _load_tool_module()

        def boom_after(_args):
            mod._CANCEL_ISSUED = True
            raise RuntimeError("unexpected fault after the POST was issued")

        orig, mod._run_main = mod._run_main, boom_after
        try:
            rc = mod.main(["--repo", "owner/repo"])
        finally:
            mod._run_main = orig
        self.assertEqual(rc, mod.EXIT_INCOMPLETE_AFTER_CANCEL)

        def boom_none(_args):
            raise RuntimeError("unexpected fault with nothing issued")

        orig, mod._run_main = mod._run_main, boom_none
        try:
            rc2 = mod.main(["--repo", "owner/repo"])
        finally:
            mod._run_main = orig
        self.assertEqual(rc2, mod.EXIT_INTERNAL)

    def test_an_ambiguous_post_whose_handler_faults_still_means_6(self):
        """THE ORDERING PIN — the flag must be set BEFORE the POST.

        The case that a moved (or deleted) assignment reverts silently: the
        POST's outcome is UNREADABLE, so the per-cancel handler runs, and the
        RENDER INSIDE IT then faults. The fault escapes from inside the handler,
        and only a flag set BEFORE the POST can still tell the top-level handler
        that a cancel may have landed. Without it this answers exit 2 with
        "Nothing was cancelled" — the exact defect this series fixed.
        """
        mod = _load_tool_module()
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=25740)])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})
        # The POST's outcome is unreadable -> cancel_run raises Incomplete.
        (self.gh_dir / "cancel_incomplete_111").write_text("x\n")
        env = {"RUN_REAPER_GH": str(self.gh), "GH_STUB_DIR": str(self.gh_dir),
               "RUN_REAPER_NOW": str(NOW)}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)

        def boom(*_a, **_k):
            raise OSError("broken pipe while rendering inside the handler")

        orig = mod._render_human
        mod._render_human = boom
        try:
            rc = mod.main(["--repo", "owner/repo", "--run", "111", "--apply"])
        finally:
            mod._render_human = orig
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

        self.assertEqual(rc, mod.EXIT_INCOMPLETE_AFTER_CANCEL,
                         "a POST whose outcome is unknown must never report "
                         "nothing was cancelled, even when its handler faults")
        self.assertNotEqual(rc, mod.EXIT_INCOMPLETE)

    def test_a_statusless_job_is_unfinished_not_completed(self):
        """A degraded job payload must fail CLOSED, not read as completed.

        `job.get("status") and job.get("status") != "completed"` classified a job
        with an ABSENT or EMPTY status as FINISHED: it never entered the
        unstarted-shard guard and its started_at was never recorded, so if it was
        the max-ceiling shard the deciding clock fell back to the RUN clock and
        the run was cancelled on queue wait — the very P0 this guard stops. An
        unknown status is not evidence of completion.
        """
        mod = _load_tool_module()
        run = {"id": 111, "status": "in_progress", "run_started_at": iso(NOW - 25740)}
        green = [[{"name": "test (g)", "status": "completed", "conclusion": "success",
                   "started_at": iso(NOW - 1072), "completed_at": iso(NOW)}]]
        for bad_status in ("", None):
            jobd = {"name": "test (g)", "conclusion": "success",
                    "started_at": None, "completed_at": None}
            if bad_status is not None:
                jobd["status"] = bad_status
            row = mod.decide(run, [jobd], green, now=NOW, floor=300)
            self.assertNotEqual(
                row["action"], "cancel",
                f"status={bad_status!r} is a degraded payload and must fail closed; "
                f"got {row['action']} via {row.get('reason')}")
            self.assertIsNone(row["shard_elapsed_s"],
                              "no shard clock was available, so no shard clock may be claimed")

    def test_listing_unreadable_but_explicit_run_still_reaps(self):
        """The listing is an INDEX; an explicit --run stands on its own."""
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress", started_s=25740)])
        self.green_population(7, maxes={"test (g)": 1072})
        res = self.run_tool(["--run", "111", "--apply"],
                            extra_env={"GH_STUB_LIST_FAIL": "1"})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), ["111"])

    # ── wiring ──────────────────────────────────────────────────────────────

    def test_scp_like_remote_with_a_digit_leading_owner_resolves(self):
        """scp-like `[user@]host:path` has NO port — the path is the path.

        `git@github.com:1inch/foo.git` used to be parsed as `host:port` and the
        owner segment was stripped, so the remote failed closed and the operator
        had to pass `--repo 1inch/foo` by hand. GitHub owners may start with a
        digit (`1inch`, `0xProject`, `4GeeksAcademy`).
        """
        mod = _load_tool_module()
        for url, want in [
            ("git@github.com:1inch/foo.git", "1inch/foo"),
            ("git@github.com:0xProject/foo.git", "0xProject/foo"),
            ("git@github.com:4GeeksAcademy/foo.git", "4GeeksAcademy/foo"),
            ("git@github.com:tortoise/foo.git", "tortoise/foo"),
            ("https://github.com/1inch/foo.git", "1inch/foo"),
        ]:
            repo = str(self.repo_with_origin(url))
            self.assertEqual(mod._slug_from_remote(repo), want, url)
            self.assertEqual(mod.resolve_slug(repo), want, url)

    def test_scp_like_lookalike_hosts_are_still_rejected(self):
        """A host that merely contains `github.com` never yields a github slug."""
        mod = _load_tool_module()
        for url in ("git@mygithub.com:1inch/foo.git",
                    "git@github.com.evil.test:1inch/foo.git",
                    "https://github.com.evil.test/1inch/foo.git",
                    "https://mygithub.com/1inch/foo.git"):
            repo = str(self.repo_with_origin(url))
            self.assertIsNone(mod._slug_from_remote(repo), url)
            with self.assertRaises(ValueError):
                mod.resolve_slug(repo)

    def test_usage_errors_exit_3_not_2(self):
        """A bad/abbreviated flag is a USAGE error (3), never INCOMPLETE (2).

        argparse itself exits 2 for a usage error, colliding with the documented
        EXIT_INCOMPLETE = 2: a wrapper keying on the status then could not tell a
        typo'd arming flag from a transiently unreadable candidate listing.
        `--help` still exits 0.
        """
        for args in (["--nope"], ["--app"], ["--ap"], ["--a"], ["--appl"],
                     ["--apply=true"], ["--flo", "5"], ["--floor", "0"],
                     ["--green-runs", "0"], ["--run", ""]):
            res = self.run_tool(args)
            self.assertEqual(res.returncode, 3,
                             f"{args} -> rc={res.returncode}\n{res.stderr}{res.stdout}")
        self.assertEqual(self.run_tool(["--help"]).returncode, 0)

    def test_tool_carveout_pins_the_reaper_tests(self):
        # A tools/run_reaper.py-only diff must still run this file (the flat
        # tools/ prefix otherwise drops it to tier-1 smoke).
        spec = importlib.util.spec_from_file_location(
            "ci_selection", ROOT / "tools" / "ci_selection.py")
        cs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cs)
        self.assertIn("tools/run_reaper.py", cs.TOOL_CARVEOUTS)
        sel = cs.select(["tools/run_reaper.py"], "pull_request", cs.load_manifest())
        self.assertTrue(sel["full"], "a reaper-only diff must fail closed to the full matrix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
