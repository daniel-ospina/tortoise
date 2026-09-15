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
  * the closed-PR surface is fetched over the REST API (`gh api --paginate`),
    not the `gh pr list` GraphQL path that resets on this host (#3587). A
    non-zero exit or a truncated stream is INCOMPLETE — partial output is never
    salvaged into a short-but-clean list — and a complete enumeration is CLEAN
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
  "pr list")
    # #3587: the `gh pr list` GraphQL path for CLOSED PRs resets on this host
    # ("read: connection reset by peer") while the REST endpoint works. The
    # tool must use REST for the closed surface; if it ever regresses to the
    # GraphQL path, fail loudly exactly as production does.
    if [ "${state:-open}" = "closed" ] && [ "${GH_STUB_CLOSED_PR_LIST_FAILS:-1}" != "0" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi
    f="$d/${state:-open}_prs.json" ;;
  "api --paginate") f="$d/closed_prs.json"; printf '%s\n' "$@" > "$d/api-argv.txt" ;;
  "issue view") f="$d/issue.json" ;;
  *) echo "gh-stub: unexpected argv: $*" >&2; exit 64 ;;
esac
if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
cat "$f"
# Mid-enumeration transport failure: pages were already printed, the exit is
# non-zero. Partial output must never be salvaged into a "complete" list.
if [ "$1 $2" = "api --paginate" ] && [ "${GH_STUB_API_FAIL_AFTER_OUTPUT:-0}" = "1" ]; then
  echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
  exit 1
fi
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

    def test_generic_keyword_pair_does_not_collide_3325(self):
        # LIVE REGRESSION (#3325): issue #3214's real title contains BOTH
        # "graph" and "delete", so it cleared the >= 2 gate against the
        # unrelated graphs-management PR #2704 and branch
        # `feat/2701-graphs-rename-delete`, fabricating a COLLISION that
        # blocked a legitimate dispatch. The gate counts DISTINCTIVE terms
        # only, so a generic pair must not collide on a branch, worktree or PR.
        title = ("fix(tests): unscoped wipe_server guard has a TOCTOU window "
                 "(peer graph minted between the protection snapshot and the delete)")
        self.gh_fixtures(
            issue=self.issue_payload(title=title),
            open_prs=[{
                "number": 3354,
                "title": "fix(infra): race-safe GRAPH.DELETE — stop poisoning "
                         "the shared FalkorDB AOF (#2961)",
                "body": "", "headRefName": "fix/2961-graph-delete-race",
            }],
            closed_prs=[{
                "number": 2704,
                "title": "feat(graphs): rename graphs (pencil) + type-to-confirm "
                         "delete (#2701)",
                "body": "", "headRefName": "feat/2701-graphs-rename-delete",
            }],
        )
        self.add_worktree("feat-2701-graphs-rename-delete",
                          branch="feat/2701-graphs-rename-delete")
        _git(self.repo, "branch", "fix/2961-graph-delete-race")
        rc, out = self.run_tool(issue=3214)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("COLLISION", out)
        self.assertNotIn("do NOT dispatch", out)
        # Transparency: the excluded generic terms are named in the report, so
        # a suppressed match is never silent.
        self.assertIn("generic term(s) excluded from the gate", out)
        self.assertIn("graph", out)
        self.assertIn("delete", out)

    def test_distinctive_terms_still_collide_for_the_same_title(self):
        # SENSITIVITY GUARD for #3325: the SAME #3214 title still yields a
        # COLLISION when a branch carries its DISTINCTIVE terms (toctou/window/
        # guard) instead of the generic graph+delete pair. The keyword dial is
        # not disabled — only the cross-cutting tier stops counting.
        title = ("fix(tests): unscoped wipe_server guard has a TOCTOU window "
                 "(peer graph minted between the protection snapshot and the delete)")
        self.gh_fixtures(issue=self.issue_payload(title=title))
        _git(self.repo, "branch", "fix/toctou-window-guard")
        rc, out = self.run_tool(issue=3214)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION (keyword-only)", out)
        self.assertIn("[local branches]", out)
        self.assertIn("keyword(s):", out)

    def test_generic_only_title_is_clean_not_incomplete(self):
        # #3325 latent: a title whose every term is generic yields no keywords.
        # That is an EVALUATED, empty keyword dimension — not an unqueryable
        # surface — so it must read CLEAN, never INCOMPLETE. A wider stoplist
        # made this reachable ("fix graph delete error"), and conflating it with
        # a missing title would turn a clean run into exit 2.
        self.gh_fixtures(issue=self.issue_payload(title="fix graph delete error"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("INCOMPLETE", out)
        self.assertIn("0 distinctive keyword(s)", out)
        self.assertIn("excluded:", out)

        # ... and a NUMBER hit under that generic-only title is still a hit.
        _git(self.repo, "branch", "fix/3061-generic-title")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("matched issue-number (3061)", out)

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

    # ── closed-PR REST transport (#3587) ────────────────────────────────────

    def test_closed_pr_surface_uses_rest_not_the_resetting_graphql_path(self):
        # REGRESSION GUARD (#3587). The stub reproduces production exactly: the
        # `gh pr list` GraphQL path FAILS for closed PRs while the REST
        # endpoint serves them. A closed-PR hit must still be found over REST
        # and the surface must be complete — not INCOMPLETE. Before the fix
        # (GraphQL transport) this run was exit 2 with no hit found.
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "time-dependent ranking",
            "body": "", "headRefName": "fix/3061-fts-determinism",
        }])
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("matched issue-number (3061) in branch", out)
        self.assertNotIn("INCOMPLETE", out)
        self.assertIn("1 PR(s) enumerated (complete, cap 5000)", out)

    def test_closed_pr_rest_complete_enumeration_is_clean(self):
        # The gate becoming SATISFIABLE again: a non-empty, fully enumerated
        # closed-PR list with no hit is CLEAN (exit 0), not INCOMPLETE.
        self.gh_fixtures(closed_prs=[
            {"number": 3, "title": "a", "body": "", "headRefName": "chore/a"},
            {"number": 4, "title": "b", "body": "", "headRefName": "chore/b"},
        ])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("INCOMPLETE", out)
        self.assertIn("2 PR(s) enumerated (complete, cap 5000)", out)

    def test_closed_pr_rest_partial_output_on_failure_is_not_salvaged(self):
        # A mid-enumeration transport failure prints the pages already fetched
        # and exits non-zero. Salvaging them would be a SILENT SHORT
        # ENUMERATION — the fail-open class this tool exists to prevent. The
        # planted hit IS in the printed rows; the surface must still read
        # INCOMPLETE and record NO hit from that partial data.
        self.gh_fixtures(closed_prs=[{
            "number": 9998, "title": "fix: 3061 planted",
            "body": "", "headRefName": "fix/3061-planted",
        }])
        rc, out = self.run_tool(env_extra={"GH_STUB_API_FAIL_AFTER_OUTPUT": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("connection reset by peer", out)
        # The positive claim: the printed rows were NOT scanned into hits.
        self.assertNotIn("[recently-closed PRs] PR", out)

    def test_closed_pr_rest_empty_output_is_incomplete_not_clean(self):
        # rc 0 + EMPTY stdout is not an empty closed-PR list: a wrapper/proxy
        # that swallows the body would otherwise read as
        # "0 PR(s) enumerated (complete)" -> CLEAN, a fail-open on the gate's
        # primary contract. A genuinely exhausted list still emits one `[]`.
        (self.gh_dir / "closed_prs.json").write_text("")
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("empty stream", out)

    def test_closed_pr_rest_truncated_stream_is_incomplete(self):
        # A truncated JSON stream (partial write) is not a short-but-valid
        # list. It must raise -> INCOMPLETE, never be parsed as "fewer PRs".
        (self.gh_dir / "closed_prs.json").write_text(
            '[{"number": 1, "title": "a", "body": "", "headRefName": "chore/a"},'
            '{"number": 2, "title": "b"'
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[recently-closed PRs]", out)
        self.assertIn("truncated/malformed JSON stream", out)

    def test_closed_pr_rest_multi_page_stream_is_fully_scanned(self):
        # `--paginate` emits ONE JSON ARRAY PER PAGE. A decoder that stopped at
        # the first value would short-enumerate silently; the hit below lives on
        # the SECOND page, so only a parser that accumulates every page in the
        # stream can find it and report the full count.
        (self.gh_dir / "closed_prs.json").write_text(
            '[{"number": 1, "title": "a", "body": "", "headRefName": "chore/a"}]\n'
            '[{"number": 9998, "title": "b", "body": "", "headRefName": "fix/3061-page2"}]'
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("matched issue-number (3061) in branch", out)
        self.assertIn("2 PR(s) enumerated (complete, cap 5000)", out)

    def test_closed_pr_rest_requests_the_projected_fields(self):
        # The REST response nests the branch under `head.ref` while the scanner
        # reads `headRefName`; the re-keying lives ONLY in the `--jq` filter.
        # The stub cannot run jq, so assert the filter actually asks for it —
        # otherwise a typo would silently disable ALL closed-PR branch matching
        # while every other test stayed green.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        argv = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("state=closed", argv)
        self.assertIn("--paginate", argv)
        self.assertIn("headRefName: .head.ref", argv)

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

    def test_usage_error_on_bad_closed_pr_timeout(self):
        proc = subprocess.run(
            [PYTHON, str(TOOL), str(ISSUE), "--repo", str(self.repo),
             "--closed-pr-timeout", "0"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("--closed-pr-timeout must be > 0", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
