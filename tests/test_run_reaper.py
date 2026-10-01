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
  * **a STALE listing** (the listing says in_progress, the individual run OBJECT
    says completed) -> skip: stale-listing, never cancelled. This is the case the
    hand-intervention got wrong; the mutation (drop the object-status guard)
    turns it RED.
  * a run whose bound cannot be derived -> skip: bound-underivable, never
    cancelled (empty green population, and a pending shard with no green sample)
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
        conclusion: str | None = "success") -> dict:
    """A job fixture whose duration is exactly ``secs``.

    ``status="in_progress"`` yields a job with no ``completed_at`` — an
    UNFINISHED shard, which is what the bound pool is computed over.
    """
    started = iso(NOW - secs) if secs is not None else iso(NOW - 1)
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
                 branch="feat/x", conclusion=None) -> None:
        started = iso(NOW - elapsed_s)
        payload = {"id": int(run_id), "status": status, "conclusion": conclusion,
                   "head_branch": branch, "workflow_id": workflow_id,
                   "run_started_at": started, "created_at": started,
                   "html_url": f"https://example.invalid/runs/{run_id}"}
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
                               job("test (g)", None, status="in_progress")])
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
                               job("test (g)", None, status="in_progress")])
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
                               job("test (g)", None, status="in_progress")])
        self.green_population(7, maxes={"test (a)": 500, "test (g)": 1072})
        row = self.rows(["--run", "111"])["111"]
        self.assertEqual(row["action"], "skip")
        self.assertEqual(row["reason"], "in-budget")
        self.assertEqual(row["bound_s"], 2144)
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
                               job("test (g)", None, status="in_progress")])
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
                               job("test (g)", None, status="in_progress")])
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
                               job("test (g)", None, status="in_progress")])
        self.make_run("222", elapsed_s=3000, workflow_id=8)
        self.make_jobs("222", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress")])

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
                                 job("test (g)", None, status="in_progress")])
        (self.gh_dir / "cancel_fail_222").write_text("refuse\n")

        res = self.run_tool(["--json", "--run", "111", "--run", "222", "--apply"])
        self.assertEqual(res.returncode, 4, res.stderr + res.stdout)
        rows = {str(d["run_id"]): d for d in json.loads(res.stdout)["decisions"]}
        self.assertEqual(self.cancelled(), ["111"], "the sibling cancel still landed")
        self.assertEqual(rows["222"]["cancel_result"], "refused")
        self.assertIn("REFUSED", rows["222"]["detail"])

    def test_listing_unreadable_but_explicit_run_still_reaps(self):
        """The listing is an INDEX; an explicit --run stands on its own."""
        self.make_run("111", elapsed_s=25740, workflow_id=7)
        self.make_jobs("111", [job("test (a)", 600),
                               job("test (g)", None, status="in_progress")])
        self.green_population(7, maxes={"test (g)": 1072})
        res = self.run_tool(["--run", "111", "--apply"],
                            extra_env={"GH_STUB_LIST_FAIL": "1"})
        self.assertEqual(res.returncode, 0, res.stderr + res.stdout)
        self.assertEqual(self.cancelled(), ["111"])

    # ── wiring ──────────────────────────────────────────────────────────────

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
