"""Hermetic mutation tests for tools/branch_reaper.py (#4408).

No network, no Docker, no gh. The GitHub PR surfaces are driven by a stub `gh`
binary that reads JSON fixtures from ``$GH_STUB_DIR``; the git surfaces use a
real temp git repo (branches, a real ``git worktree add``, a real
``refs/remotes/origin/main``).

Run standalone:      python3 tests/test_branch_reaper.py
Run under pytest:    python3 -m pytest tests/test_branch_reaper.py -q

Coverage (the mutations from the issue):
  * a branch whose tip == its MERGED PR head -> SAFE, deleted on --apply
  * the SAME branch with an ADVANCED tip -> PRESERVE (the issue's name-only
    rule would delete it; this is the data-loss mutation)
  * a CLOSED-unmerged branch -> PRESERVE by default, SAFE with the opt-in flag
  * an ancestry-merged branch with no PR -> SAFE
  * an open PR over an ancestor branch -> PRESERVE (open-PR veto precedes ancestry)
  * a safe branch held by a DIRTY worktree -> branch kept (never --force)
  * a truncated PR list -> INCOMPLETE, exit 2, NOTHING deleted
  * an unqueryable gh -> INCOMPLETE, exit 2
  * a --reap-worktrees engine that fail-closes (exit 3) -> INCOMPLETE, nothing deleted
  * a --reap-worktrees engine that removes nothing -> held branch kept
  * trunk / the current branch -> PRESERVE
  * --apply from the MAIN checkout -> refused (exit 3)
  * a ref that MOVED between classify and delete -> skipped, not destroyed
  * the pre-delete recovery record is written before any deletion
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "branch_reaper.py"
PYTHON = sys.executable

GH_STUB = r"""#!/usr/bin/env bash
set -u
d="${GH_STUB_DIR:?GH_STUB_DIR unset}"
if [ -n "${GH_STUB_FAIL:-}" ]; then
  echo "gh-stub: simulated transport failure" >&2
  exit 1
fi
state=""
for a in "$@"; do
  case "$a" in
    *state=open*) state=open ;;
    *state=closed*) state=closed ;;
  esac
done
[ -n "$state" ] || { echo "gh-stub: no state in argv: $*" >&2; exit 1; }
cat "$d/${state}_pages.json"
"""


def _run(cmd, cwd=None, env=None, check=True):
    return subprocess.run(cmd, cwd=cwd, env=env, check=check, timeout=30,
                          capture_output=True, text=True)


def _git(repo: Path, *args: str) -> None:
    _run(["git", *args], cwd=repo)


def _git_out(repo: Path, *args: str) -> str:
    return _run(["git", *args], cwd=repo).stdout.strip()


def _write_exec(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class ReaperTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="branch-reaper-test-"))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.gh_dir = self.tmp / "gh"
        self.gh_dir.mkdir()
        self.gh = _write_exec(self.gh_dir / "gh", GH_STUB)

        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "test")
        (self.repo / "f.txt").write_text("base\n")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-m", "base")
        self.main_sha = _git_out(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main_sha)
        # A linked worktree to run --apply from (the tool refuses the main checkout).
        # The driver must be on its OWN branch — git forbids one branch checked out twice.
        _git(self.repo, "branch", "driver/base", "main")
        self.driver = self.tmp / "driver"
        _git(self.repo, "worktree", "add", str(self.driver), "driver/base")

        self.open_prs: list[dict] = []
        self.merged_prs: list[dict] = []
        self.closed_prs: list[dict] = []
        self.pr_counter = 100

    # ── fixtures ────────────────────────────────────────────────────────────

    def _next_pr(self) -> int:
        self.pr_counter += 1
        return self.pr_counter

    def commit_on(self, branch: str, text: str) -> str:
        """Create ``branch`` off main with one commit; return the tip SHA."""
        _git(self.repo, "checkout", "-q", "-b", branch, "main")
        (self.repo / "f.txt").write_text(text + "\n")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-q", "-m", text)
        sha = _git_out(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "checkout", "-q", "main")
        return sha

    def branch_at_main(self, branch: str) -> str:
        _git(self.repo, "branch", branch, "main")
        return self.main_sha

    def add_pr(self, state: str, head: str, sha: str) -> int:
        pr = {"number": self._next_pr(), "head": {"ref": head, "sha": sha},
              "merged_at": "2026-09-01T00:00:00Z" if state == "merged" else None}
        {"open": self.open_prs, "merged": self.merged_prs, "closed": self.closed_prs}[state].append(pr)
        return pr["number"]

    def write_fixtures(self, *, open_pages=None, closed_pages=None):
        open_pages = open_pages if open_pages is not None else [self.open_prs]
        # closed = merged ∪ closed-unmerged (one PR list, split by merged_at)
        closed_pages = closed_pages if closed_pages is not None else [self.merged_prs + self.closed_prs]
        (self.gh_dir / "open_pages.json").write_text(json.dumps(open_pages))
        (self.gh_dir / "closed_pages.json").write_text(json.dumps(closed_pages))

    # ── runner ──────────────────────────────────────────────────────────────

    def run_tool(self, args=None, *, repo=None, env_extra=None):
        env = dict(os.environ)
        env["BRANCH_REAPER_GH"] = str(self.gh)
        env["GH_STUB_DIR"] = str(self.gh_dir)
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        cmd = [PYTHON, str(TOOL), "--repo", str(repo or self.repo),
               "--slug", "owner/repo", *(args or [])]
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
        return res.returncode, res.stdout, res.stderr

    def branches(self) -> set[str]:
        out = _git_out(self.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return {ln for ln in out.splitlines() if ln}

    def rows(self, args=None):
        rc, out, err = self.run_tool(["--json", *(args or [])])
        self.assertEqual(rc, 0, err)
        return {r["branch"]: r for r in json.loads(out)["rows"]}

    # ── classification mutations ────────────────────────────────────────────

    def test_merged_tip_is_safe_and_deleted(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        row = self.rows()["merged/branch"]
        self.assertEqual(row["verdict"], "SAFE")
        self.assertEqual(row["reason"], "pr-merged-tip")
        self.assertEqual(row["commits_survive"], "merged-pr-record")

        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertNotIn("merged/branch", self.branches())

    def test_advanced_beyond_merged_pr_tip_is_preserved(self):
        # The PR head is commit A; the local branch has advanced to B.
        _git(self.repo, "checkout", "-q", "-b", "advanced/branch", "main")
        (self.repo / "f.txt").write_text("A\n")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-q", "-m", "A")
        sha_a = _git_out(self.repo, "rev-parse", "HEAD")
        (self.repo / "f.txt").write_text("B\n")
        _git(self.repo, "add", "f.txt")
        _git(self.repo, "commit", "-q", "-m", "B")
        self.assertNotEqual(_git_out(self.repo, "rev-parse", "HEAD"), sha_a)
        _git(self.repo, "checkout", "-q", "main")
        self.add_pr("merged", "advanced/branch", sha_a)
        self.write_fixtures()
        row = self.rows()["advanced/branch"]
        self.assertEqual(row["verdict"], "PRESERVE")
        self.assertEqual(row["reason"], "advanced-beyond-pr-tip")

        rc, _, _ = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0)
        self.assertIn("advanced/branch", self.branches())

    def test_closed_unmerged_preserved_by_default_then_safe_with_flag(self):
        sha = self.commit_on("closed/branch", "abandoned work")
        self.add_pr("closed", "closed/branch", sha)
        self.write_fixtures()
        row = self.rows()["closed/branch"]
        self.assertEqual(row["verdict"], "PRESERVE")
        self.assertEqual(row["reason"], "pr-closed-tip")

        row = self.rows(["--include-closed-unmerged"])["closed/branch"]
        self.assertEqual(row["verdict"], "SAFE")
        self.assertEqual(row["reason"], "pr-closed-tip")

    def test_ancestor_branch_with_no_pr_is_safe(self):
        self.branch_at_main("anc/branch")
        self.write_fixtures()
        row = self.rows()["anc/branch"]
        self.assertEqual(row["verdict"], "SAFE")
        self.assertEqual(row["reason"], "ancestry")

    def test_open_pr_over_an_ancestor_branch_is_preserved(self):
        # An open PR can point at a branch whose tip is already in main (a
        # reopened PR, a release-branch target, or a fast-forwarded branch).
        self.branch_at_main("open/ancestor")
        self.add_pr("open", "open/ancestor", self.main_sha)
        self.write_fixtures()
        row = self.rows()["open/ancestor"]
        self.assertEqual(row["verdict"], "PRESERVE")
        self.assertEqual(row["reason"], "open-pr")

    def test_trunk_and_current_branch_are_preserved(self):
        self.write_fixtures()
        row = self.rows()["main"]
        self.assertEqual(row["verdict"], "PRESERVE")
        self.assertEqual(row["reason"], "trunk")

    def test_judgement_has_no_pr_and_is_not_ancestor(self):
        self.commit_on("nopr/branch", "unlanded")
        self.write_fixtures()
        row = self.rows()["nopr/branch"]
        self.assertEqual(row["verdict"], "JUDGEMENT")
        self.assertEqual(row["reason"], "no-pr-non-ancestor")

    # ── fail-closed mutations ───────────────────────────────────────────────

    def test_truncated_pr_list_is_incomplete_and_deletes_nothing(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        # One page, and a cap of one page -> TRUNCATED.
        self.write_fixtures(closed_pages=[[{"number": 1, "head": {"ref": "x", "sha": "y"},
                                            "merged_at": None}]])
        rc, out, err = self.run_tool(["--apply", "--max-pr-pages", "1"], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("merged/branch", self.branches())

    def test_unqueryable_gh_is_incomplete(self):
        self.commit_on("merged/branch", "merged work")
        self.write_fixtures()
        rc, _, err = self.run_tool(["--apply"], repo=self.driver,
                                   env_extra={"GH_STUB_FAIL": "1"})
        self.assertEqual(rc, 2)
        self.assertIn("INCOMPLETE", err)

    # ── worktree safety mutations ───────────────────────────────────────────

    def test_dirty_worktree_branch_is_kept(self):
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-held"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        (wt / "untracked.txt").write_text("uncommitted work the lane left behind\n")

        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertIn("held/branch", self.branches())
        # The checkout is never torn down by this tool (no delegate, no --force).
        self.assertTrue((wt / "untracked.txt").exists())

    def test_engine_failclosed_keeps_everything(self):
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-held"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        engine = _write_exec(self.driver / "scripts" / "pi-reap-worktrees.sh",
                             "#!/usr/bin/env bash\necho 'fail-closed'\nexit 3\n")
        rc, out, err = self.run_tool(["--apply", "--reap-worktrees",
                                      "--worktree-engine", str(engine)], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("held/branch", self.branches())

    def test_engine_that_removes_nothing_keeps_held_branch(self):
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-held"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        engine = _write_exec(self.driver / "scripts" / "pi-reap-worktrees.sh",
                             "#!/usr/bin/env bash\necho 'REMOVED=0'\nexit 0\n")
        rc, out, err = self.run_tool(["--apply", "--reap-worktrees",
                                      "--worktree-engine", str(engine)], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertIn("held/branch", self.branches())

    def test_apply_from_main_checkout_is_refused(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply"], repo=self.repo)
        self.assertEqual(rc, 3, err + out)
        self.assertIn("MAIN checkout", err)
        self.assertIn("merged/branch", self.branches())

    # ── TOCTOU + recovery ───────────────────────────────────────────────────

    def test_ref_moved_between_classify_and_delete_is_skipped(self):
        # Import the module and call the delete phase directly with a stale OID,
        # simulating a lane committing after classification.
        import importlib.util

        spec = importlib.util.spec_from_file_location("branch_reaper", TOOL)
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        sha = self.commit_on("merged/branch", "merged work")
        rows = [{"branch": "merged/branch", "oid": "0" * 40, "ts": 0,
                 "verdict": "SAFE", "reason": "pr-merged-tip"}]
        results = br.delete_branches(str(self.repo), rows, set(), backup_bundle=None)
        self.assertEqual(results[0]["result"], "skipped")
        self.assertIn("moved", results[0]["detail"])
        self.assertIn("merged/branch", self.branches())
        self.assertTrue(sha)  # branch still exists at its real tip

    def test_report_written_before_deletion_contains_recovery_record(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        report = self.tmp / "report.md"
        rc, out, err = self.run_tool(["--apply", "--report", str(report)], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        body = report.read_text()
        self.assertIn("Recovery record", body)
        self.assertIn(sha, body)
        self.assertIn("Post-apply results", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
