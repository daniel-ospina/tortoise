"""Hermetic tests for tools/collision_preflight.py (#3061).

No network, no Docker, no FalkorDB. The GitHub surfaces are driven by a stub
`gh` binary that reads JSON fixtures from $GH_STUB_DIR; the git surfaces use a
real temp git repo (branches, a remote-tracking ref, real `git worktree add`).

Run standalone:      python3 tests/test_collision_preflight.py
Run under pytest:    python3 -m pytest tests/test_collision_preflight.py -q

Coverage:
  * CLEAN: no hits on any surface -> exit 0, VERDICT: CLEAN
  * one stubbed/real HIT per hit-capable surface -> exit != 0, surface named
  * UNTRUNCATED worktree scan: a hit that a `tail -8` window would hide is found
  * number boundary: `3061` does NOT match `30610` (no fabricated collision)
  * PR-body PROSE is not a collision: a closed PR body that merely
    cross-references `#N` (the live #2926/#2754 shapes) is WEAK/non-blocking ->
    CLEAN; a body closing reference (`Closes #N`) is still a strong hit
  * PR lists are completeness-checked: a list longer than its cap is reported
    TRUNCATED and the run is INCOMPLETE (exit 2), never CLEAN
  * keyword hits match name-like fields only (`.worktrees/` structural token
    in a title does not collide)
  * an unqueryable surface (gh / git / keyword source) -> INCOMPLETE, exit 2,
    never CLEAN
  * every surface row is always present (the check cannot run partially)
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "collision_preflight.py"
PYTHON = sys.executable
ISSUE = 3061

GH_STUB = r"""#!/usr/bin/env bash
set -u
d="${GH_STUB_DIR:?GH_STUB_DIR unset}"
state=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--state" ]; then state="$a"; fi
  prev="$a"
done
case "$1 $2" in
  "pr list")   f="$d/${state:-open}_prs.json" ;;
  "issue view") f="$d/issue.json" ;;
  *) echo "gh-stub: unexpected argv: $*" >&2; exit 64 ;;
esac
if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
cat "$f"
"""

GIT_STUB = r"""#!/usr/bin/env bash
set -u
if [ "${1:-}" = "for-each-ref" ]; then
  for a in "$@"; do
    if [ "$a" = "refs/heads" ]; then
      echo "git-stub: refusing refs/heads" >&2
      exit 1
    fi
  done
fi
exec "${REAL_GIT:?REAL_GIT unset}" "$@"
"""

SURFACE_ROWS = (
    "open PRs",
    "recently-closed PRs",
    "local branches",
    "remote branches",
    "local worktrees",
    "issue assignee/comments",
    "issue keywords",
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _write_exec(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class CollisionPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="collision-preflight-")
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main", "--template=")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        (self.repo / "seed.txt").write_text("seed\n")
        _git(self.repo, "add", "seed.txt")
        _git(self.repo, "commit", "-qm", "seed")

        self.gh_dir = self.tmp / "ghstub"
        self.gh_dir.mkdir()
        self.gh = _write_exec(self.gh_dir / "gh", GH_STUB)
        # Default: every GitHub surface is queryable and empty.
        self.gh_fixtures(open_prs=[], closed_prs=[], issue=self.issue_payload())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────

    def issue_payload(self, title="florfenicol dosing audit", assignees=(),
                      comments=(), state="OPEN") -> dict:
        return {
            "number": ISSUE,
            "title": title,
            "state": state,
            "url": f"https://example.invalid/issues/{ISSUE}",
            "assignees": [{"login": a} for a in assignees],
            "comments": [{"author": {"login": "someone"}, "body": c} for c in comments],
        }

    def gh_fixtures(self, open_prs=None, closed_prs=None, issue=None) -> None:
        if open_prs is not None:
            (self.gh_dir / "open_prs.json").write_text(json.dumps(open_prs))
        if closed_prs is not None:
            (self.gh_dir / "closed_prs.json").write_text(json.dumps(closed_prs))
        if issue is not None:
            (self.gh_dir / "issue.json").write_text(json.dumps(issue))

    def clear_fixtures(self) -> None:
        for name in ("open_prs.json", "closed_prs.json", "issue.json"):
            (self.gh_dir / name).unlink(missing_ok=True)

    def add_worktree(self, name: str, branch: str | None = None) -> Path:
        path = self.tmp / "wt" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if branch:
            subprocess.run(["git", "worktree", "add", "-q", "--no-checkout", "-b", branch, str(path), "HEAD"],
                           cwd=self.repo, check=True, capture_output=True, text=True)
        else:
            subprocess.run(["git", "worktree", "add", "-q", "--no-checkout", "--detach", str(path), "HEAD"],
                           cwd=self.repo, check=True, capture_output=True, text=True)
        return path

    # ── runner ──────────────────────────────────────────────────────────────

    def run_tool(self, issue: int = ISSUE, keywords: str | None = None,
                 git_bin: Path | None = None, env_extra: dict | None = None,
                 extra_args: list[str] | None = None):
        env = dict(os.environ)
        env["GH_STUB_DIR"] = str(self.gh_dir)
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        cmd = [PYTHON, str(TOOL), str(issue), "--repo", str(self.repo), "--gh", str(self.gh)]
        if keywords:
            cmd += ["--keywords", keywords]
        if git_bin:
            cmd += ["--git", str(git_bin)]
        if extra_args:
            cmd += [str(a) for a in extra_args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
        return proc.returncode, proc.stdout + proc.stderr

    def assert_all_surface_rows(self, out: str) -> None:
        for row in SURFACE_ROWS:
            self.assertIn(row, out, f"surface row missing from output: {row}")

    # ── clean ───────────────────────────────────────────────────────────────

    def test_clean_no_hits_exits_zero_and_says_clean(self):
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("no in-flight work found for #3061", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertNotIn("INCOMPLETE", out)
        self.assert_all_surface_rows(out)
        self.assertIn("worktree(s) enumerated (untruncated)", out)

    # ── one stubbed/real hit per hit-capable surface ────────────────────────

    def test_open_pr_hit_names_surface(self):
        self.gh_fixtures(open_prs=[{
            "number": 9999, "title": "fix: guard retrieval (#3061)",
            "body": "closes it", "headRefName": "fix/guard",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[open PRs]", out)
        self.assertIn("issue-number (3061) in title", out)

    def test_closed_pr_body_and_branch_hits(self):
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "time-dependent ranking",
            "body": "Addresses #3061.", "headRefName": "fix/3061-fts-determinism",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[recently-closed PRs]", out)

    def test_closed_pr_body_prose_mention_is_not_a_collision(self):
        # LIVE-BUG FIXTURES. These are verbatim shapes from a real run:
        #   * closed PR #2926's body says "(restored in #2745)"
        #   * closed PR #2754's body says "Triaged and filed as #2751"
        # A closed PR cannot be in-flight and cross-reference prose is not
        # work. Matching the bare number used to fabricate a strong COLLISION
        # ("do NOT dispatch") for #2745 and #2751. Both must now be weak
        # signals only -> CLEAN, exit 0.
        self.gh_fixtures(closed_prs=[
            {"number": 2926,
             "title": "chore(battery): remove the unreachable class-sentinel pre-flight branch (#2746)",
             "body": "`test_real_preflight_refuses_unpinned_or_fixed_sentinel` "
                     "(restored in #2745): the arms.yaml pin must win.",
             "headRefName": "chore/2746-dead-sentinel"},
            {"number": 2754,
             "title": "test(e2e): migrate dashboard specs to local-preview document loads",
             "body": "Triaged and filed as #2751 — **not introduced by this PR**.",
             "headRefName": "fix/2744-local-preview-migration"},
        ])
        rc, out = self.run_tool(issue=2745)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("VERDICT: COLLISION", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("prose mention of #2745", out)
        self.assertIn("WEAK SIGNALS", out)
        self.assertIn("non-blocking", out)

        rc, out = self.run_tool(issue=2751)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("prose mention of #2751", out)

    def test_closed_pr_own_number_is_weak_not_blocking(self):
        # Closed PR #3061 (the issue is itself a closed PR). Its own number and
        # any prose in its body are not separate in-flight work.
        self.gh_fixtures(closed_prs=[{
            "number": 3061, "title": "fix(battery): #2712 restore the pin test",
            "body": "restored in #3061", "headRefName": "fix/2712-pin-preflight-test",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("PR number == issue (3061)", out)

    def test_closed_pr_closing_reference_is_still_a_hit(self):
        # "Closes #N" IS a claim on the issue and must stay a strong hit.
        for body in ("Closes #3061.", "closes: #3061", "Fixes #3061",
                     "Resolves #3061", "fixed #3061"):
            self.gh_fixtures(closed_prs=[{
                "number": 9997, "title": "unrelated title",
                "body": body, "headRefName": "chore/9997-unrelated",
            }])
            rc, out = self.run_tool()
            self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
            self.assertIn("VERDICT: COLLISION", out)
            self.assertIn("closing reference to #3061", out)

    def test_closed_pr_prose_without_closing_keyword_is_not_a_hit(self):
        # "fixed in #3061" / "see #3061" are prose, not closing keywords.
        for body in ("fixed in #3061", "see #3061 for context", "restored in #3061"):
            self.gh_fixtures(closed_prs=[{
                "number": 9996, "title": "unrelated title",
                "body": body, "headRefName": "chore/9996-unrelated",
            }])
            rc, out = self.run_tool()
            self.assertEqual(rc, 0, f"body={body!r}\n{out}")
            self.assertNotIn("do NOT dispatch", out)

    def test_remote_branch_hit(self):
        _git(self.repo, "update-ref", "refs/remotes/origin/fix/3061-collision", "HEAD")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[remote branches]", out)
        self.assertIn("refs/remotes/origin/fix/3061-collision", out)

    def test_local_branch_hit(self):
        _git(self.repo, "branch", "fix/3061-collision-preflight")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local branches]", out)
        self.assertIn("refs/heads/fix/3061-collision-preflight", out)

    def test_worktree_hit_untruncated(self):
        # The hit sorts FIRST among worktrees; 8 later worktrees follow it, so
        # a `git worktree list | tail -8` window (the #3061 failure mode) hides
        # it. The tool must still find it and report the full enumeration count.
        self.add_worktree("000-w3061-hit")
        for i in range(8):
            self.add_worktree(f"zzz-{i:02d}-unrelated")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local worktrees]", out)
        self.assertIn("000-w3061-hit", out)
        # 1 main worktree + 1 hit + 8 fillers = 10, all enumerated.
        self.assertIn("10 worktree(s) enumerated (untruncated)", out)

    def test_issue_assignee_hit(self):
        self.gh_fixtures(issue=self.issue_payload(assignees=("other-agent",)))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[issue assignee/comments]", out)
        self.assertIn("assignee:other-agent", out)

    def test_issue_claim_comment_hit(self):
        self.gh_fixtures(issue=self.issue_payload(comments=("I'm working on this now.",)))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[issue assignee/comments]", out)
        self.assertIn("claim-style comment", out)

    # ── keyword matching ────────────────────────────────────────────────────

    def test_keyword_hit_on_branch_without_number(self):
        _git(self.repo, "branch", "fix/florfenicol-dosing")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION (keyword-only)", out)
        self.assertIn("[local branches]", out)
        self.assertIn("keyword(s): florfenicol", out)

    def test_worktree_structural_token_is_not_a_collision(self):
        # Title contains "worktree"; the worktree lives under a `.worktrees/`
        # parent. The structural directory token must not fabricate a hit.
        self.gh_fixtures(issue=self.issue_payload(title="investigate worktree enumeration"))
        self.add_worktree(".worktrees/plain-1")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_number_boundary_no_false_positive(self):
        # 30610 must NOT match 3061 — no substring collisions.
        _git(self.repo, "branch", "fix/30610-other")
        self.gh_fixtures(open_prs=[{
            "number": 1, "title": "30610 unrelated PR", "body": "see 30610",
            "headRefName": "fix/30610-other",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_explicit_keywords_override_without_gh_title(self):
        self.clear_fixtures()
        self.gh_fixtures(issue=None)  # gh issue view fails -> keyword source gone
        # Provide the keywords explicitly so the run is complete, and plant them.
        _git(self.repo, "branch", "fix/florfenicol-dosing")
        rc, out = self.run_tool(keywords="florfenicol,dosing")
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[local branches]", out)
        # The issue surface is still INCOMPLETE even though a hit was found.
        self.assertIn("ALSO INCOMPLETE", out)

    def test_single_keyword_does_not_collide_by_default(self):
        # 'florfenicol' alone is not enough — the default gate is 2 distinct
        # keywords, which is what keeps a common word ('remote') from matching
        # hundreds of unrelated branches.
        _git(self.repo, "branch", "fix/florfenicol-unrelated")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_min_keywords_one_opts_into_single_keyword_hits(self):
        _git(self.repo, "branch", "fix/florfenicol-unrelated")
        env = dict(os.environ)
        env["GH_STUB_DIR"] = str(self.gh_dir)
        proc = subprocess.run(
            [PYTHON, str(TOOL), str(ISSUE), "--repo", str(self.repo),
             "--gh", str(self.gh), "--min-keywords", "1"],
            capture_output=True, text=True, env=env, timeout=90,
        )
        out = proc.stdout + proc.stderr
        self.assertNotEqual(proc.returncode, 0, out)
        self.assertIn("VERDICT: COLLISION (keyword-only)", out)

    def test_remote_branch_keyword_scan_is_disabled(self):
        # Remote-tracking refs are number-matched only; a multi-keyword match
        # there must NOT fabricate a hit (878 false hits on a real run).
        _git(self.repo, "update-ref", "refs/remotes/origin/fix/florfenicol-dosing", "HEAD")
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    # ── incomplete, never silently clean ────────────────────────────────────

    def test_gh_unavailable_is_incomplete_not_clean(self):
        self.clear_fixtures()
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[open PRs]", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("[issue assignee/comments]", out)
        self.assert_all_surface_rows(out)

    def test_git_surface_unavailable_is_incomplete(self):
        git_stub = _write_exec(self.tmp / "git-stub", GIT_STUB)
        rc, out = self.run_tool(git_bin=git_stub,
                                env_extra={"REAL_GIT": shutil.which("git")})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[local branches]", out)
        # Other git surfaces remain queryable and clean — the worktree scan
        # still enumerated (its note only appears on a successful scan).
        self.assertIn("1 worktree(s) enumerated (untruncated)", out)

    def test_keyword_source_unavailable_is_incomplete(self):
        self.clear_fixtures()
        self.gh_fixtures(issue=None)
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[issue keywords]", out)
        self.assertIn("keyword-source-unavailable", out)

    # ── truncation is INCOMPLETE, never CLEAN ───────────────────────────────

    def test_open_pr_list_truncation_is_incomplete(self):
        # A PR list longer than its cap is a PARTIAL query — the fail-open
        # class this tool exists to prevent. It must read INCOMPLETE (exit 2).
        self.gh_fixtures(open_prs=[
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 2, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(extra_args=["--pr-limit", "1"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("TRUNCATED", out)
        self.assertIn("[open PRs]", out)

    def test_closed_pr_list_truncation_is_incomplete(self):
        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 4, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool(extra_args=["--closed-pr-limit", "1"])
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("TRUNCATED", out)
        self.assertIn("[recently-closed PRs]", out)

    def test_complete_pr_list_reports_the_count_and_stays_clean(self):
        self.gh_fixtures(open_prs=[
            {"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},
        ])
        rc, out = self.run_tool(extra_args=["--pr-limit", "5"])
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("1 PR(s) enumerated (complete, cap 5)", out)

    # ── partial-run prevention ──────────────────────────────────────────────

    def test_every_surface_is_always_evaluated(self):
        _rc, out = self.run_tool()
        self.assert_all_surface_rows(out)
        self.assertIn("7/7 surfaces queried", out)

    def test_usage_error_on_bad_issue(self):
        proc = subprocess.run(
            [PYTHON, str(TOOL), "0", "--repo", str(self.repo)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
