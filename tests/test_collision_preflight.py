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
repo=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--repo" ]; then repo="$a"; fi
  prev="$a"
done
json_args=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--json" ]; then json_args="$a"; fi
  prev="$a"
done

# `gh api user -q .login` -> the current account (ownership attribution).
if [ "$1 $2" = "api user" ]; then
  if [ -f "$d/user.txt" ]; then cat "$d/user.txt"; else echo "test-agent"; fi
  exit 0
fi

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
    # The target repo is sent EXPLICITLY (#4027): record it so a test can
    # prove the selector reached gh rather than being inferred from the cwd.
    printf '%s\n' "$@" > "$d/pr-list-argv.txt"
    f="$d/${state:-open}_prs.json" ;;
  "api --paginate") f="$d/closed_prs.json"; printf '%s\n' "$@" > "$d/api-argv.txt" ;;
  "issue view")
    # A number ABSENT from the target repo is not "no in-flight work" (#4027).
    if [ "${GH_STUB_ISSUE_ABSENT:-0}" = "1" ]; then
      echo "GraphQL: Could not resolve to an issue or pull request with the number of $3. (repository.issue)" >&2
      exit 1
    fi
    # Probe form (the cross-repo ambiguity check asks for `--json number` only).
    if [ "$json_args" = "number" ]; then
      if [ -f "$d/probe_unqueried.txt" ] && grep -qx "$repo" "$d/probe_unqueried.txt"; then
        echo "gh-stub: could not connect to api.github.com" >&2; exit 1
      fi
      if [ -f "$d/probe_holds.txt" ] && grep -qx "$repo" "$d/probe_holds.txt"; then
        echo "{\"number\": $3}"; exit 0
      fi
      echo "GraphQL: Could not resolve to an issue or pull request with the number of $3. (repository.issue)" >&2
      exit 1
    fi
    f="$d/issue.json"; printf '%s\n' "$@" > "$d/issue-argv.txt" ;;
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

# A Python stand-in for the fleet's `map-sessions.py` was written for a
# fleet-session surface (#1233) and then deliberately NOT shipped: its
# prescribed discipline cannot tell a session that holds an issue from one that
# has merely read the fleet board. See the module docstring of
# tools/collision_preflight.py.

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


_TOOL_MODULE = None


def _tool_module():
    """Import `tools/collision_preflight.py` (hyphenated name) once, so the
    claim pattern and the escape stripper can be asserted directly without
    spawning the tool 40 times. The module has no import-time side effects (its
    `main()` is guarded)."""
    global _TOOL_MODULE
    if _TOOL_MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "collision_preflight_under_test", TOOL)
        module = importlib.util.module_from_spec(spec)
        # Register BEFORE exec: dataclasses resolve annotations via
        # `sys.modules[cls.__module__]` while the module body runs.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _TOOL_MODULE = module
    return _TOOL_MODULE


class CollisionPreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="collision-preflight-")
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main", "--template=")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Test")
        # The GitHub target is DERIVED OFFLINE from the git remote (#4027), so
        # every gh call can be sent an explicit `--repo owner/name` without a
        # network round-trip.
        _git(self.repo, "remote", "add", "origin",
             "https://github.com/test-owner/test-repo.git")
        (self.repo / "seed.txt").write_text("seed\n")
        _git(self.repo, "add", "seed.txt")
        _git(self.repo, "commit", "-qm", "seed")

        self.gh_dir = self.tmp / "ghstub"
        self.gh_dir.mkdir()
        self.gh = _write_exec(self.gh_dir / "gh", GH_STUB)
        (self.gh_dir / "user.txt").write_text("test-agent")
        # Default: every GitHub surface is queryable and empty.
        self.gh_fixtures(open_prs=[], closed_prs=[], issue=self.issue_payload())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── fixtures ────────────────────────────────────────────────────────────

    def issue_payload(self, title="florfenicol dosing audit", assignees=(),
                      comments=(), state="OPEN", comment_author=None) -> dict:
        """`comments` items are body strings (author "someone") or
        `(author_login, body)` tuples. The author is still controllable so a
        test can assert the comment is NAMED (the REMEDY line), even though
        claim comments are no longer attributed by author."""
        rendered = []
        for comment in comments:
            if isinstance(comment, dict):
                rendered.append(comment)
                continue
            if isinstance(comment, tuple):
                author, body = comment
            else:
                author, body = (comment_author or "someone"), comment
            rendered.append({"author": {"login": author}, "body": body})
        return {
            "number": ISSUE,
            "title": title,
            "state": state,
            "url": f"https://example.invalid/issues/{ISSUE}",
            "assignees": [{"login": a} for a in assignees],
            "comments": rendered,
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
                 extra_args: list[str] | None = None,
                 repo_arg: object = "__default__", cwd: Path | None = None,
                 gh_bin: Path | None = None):
        env = dict(os.environ)
        env["GH_STUB_DIR"] = str(self.gh_dir)
        if env_extra:
            env.update({k: str(v) for k, v in env_extra.items()})
        cmd = [PYTHON, str(TOOL), str(issue)]
        if repo_arg == "__default__":
            cmd += ["--repo", str(self.repo)]
        elif repo_arg is not None:
            cmd += ["--repo", str(repo_arg)]
        cmd += ["--gh", str(gh_bin or self.gh)]
        if keywords:
            cmd += ["--keywords", keywords]
        if git_bin:
            cmd += ["--git", str(git_bin)]
        if extra_args:
            cmd += [str(a) for a in extra_args]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(cwd) if cwd else None, timeout=90)
        return proc.returncode, proc.stdout + proc.stderr

    def assert_all_surface_rows(self, out: str) -> None:
        for row in SURFACE_ROWS:
            self.assertIn(row, out, f"surface row missing from output: {row}")

    def surface_row(self, out: str, name: str) -> tuple[str, int]:
        """(STATUS, hit count) for one surface row, parsed from the report's
        fixed-width table. The #4375 matrix pins EXACT per-surface counts, so a
        surface that silently gains or loses a hit fails the test."""
        prefix = f"{name:<24} "
        for line in out.splitlines():
            if line.startswith(prefix):
                parts = line[len(prefix):].split(None, 2)
                return parts[0], int(parts[1])
        raise AssertionError(f"surface row not found: {name}\n{out}")

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
        # any prose in its body are not separate in-flight work. The title
        # carries #3061 too, so this also pins the ORDER: the number==issue
        # check runs BEFORE the number tier (which would otherwise be strong).
        self.gh_fixtures(closed_prs=[{
            "number": 3061, "title": "fix(battery): #3061 restore the pin test",
            "body": "restored in #3061", "headRefName": "fix/2712-pin-preflight-test",
        }])
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("do NOT dispatch", out)
        self.assertIn("PR number == issue (3061)", out)

    def test_4375_closed_pr_keyword_only_hit_is_advisory(self):
        # The #3673/#4365 shape: the ONLY hits are keyword-only matches on
        # MERGED closed PRs, so nothing is in flight. `merged_at` distinguishes
        # MERGED (advisory) from unmerged-closed (blocking).
        self.gh_fixtures(
            issue=self.issue_payload(title="onboarding parity gate — drift is silent"),
            closed_prs=[{
                "number": 3974,
                "title": "fix(deps): re-sync requirements.txt with uv.lock",
                "body": "",
                "headRefName": "fix/deploy-parity-requirements-drift",
                "merged_at": "2026-09-18T14:31:32Z",
            }],
        )
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("keyword(s): parity, drift", out)
        self.assertIn("merged PR", out)
        self.assertIn("(weak)", out)

    def test_4375_unmerged_closed_pr_keyword_hit_still_blocks(self):
        # An UNMERGED closed PR is abandoned-but-unfinished: its keyword hit
        # must still block. The decision is PER-PR, so one merged PR must not
        # swallow it. An ABSENT `merged_at` is not evidence of a merge.
        for merged in (None, "__absent__"):
            pr = {
                "number": 3974,
                "title": "fix(deps): re-sync requirements.txt with uv.lock",
                "body": "",
                "headRefName": "fix/deploy-parity-requirements-drift",
            }
            if merged != "__absent__":
                pr["merged_at"] = merged
            with self.subTest(merged=merged):
                self.gh_fixtures(
                    issue=self.issue_payload(
                        title="onboarding parity gate — drift is silent"),
                    closed_prs=[pr],
                )
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, out)
                self.assertIn("VERDICT: COLLISION (keyword-only)", out)
                self.assertIn("keyword(s): parity, drift", out)
                self.assertNotIn("(weak)", out)

    def test_4375_open_pr_keyword_hit_still_blocks(self):
        # An OPEN PR is in-flight work; only the CLOSED surface has the
        # merged-PR advisory. A keyword-only head-ref match still refuses.
        self.gh_fixtures(
            issue=self.issue_payload(title="onboarding parity gate — drift is silent"),
            open_prs=[{
                "number": 9001, "title": "unrelated title", "body": "",
                "headRefName": "fix/deploy-parity-requirements-drift",
            }],
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION (keyword-only)", out)
        self.assertNotIn("(weak)", out)

    def test_4375_advisory_hits_do_not_mask_a_hard_incomplete(self):
        # An unqueryable surface is INCOMPLETE (exit 2) no matter how many
        # advisory weak hits are listed. Weakening the keyword tier must never
        # weaken the completeness contract.
        self.gh_fixtures(
            issue=self.issue_payload(title="onboarding parity gate — drift is silent"),
            closed_prs=[{
                "number": 3974, "title": "unrelated", "body": "",
                "headRefName": "fix/deploy-parity-requirements-drift",
                "merged_at": "2026-09-18T14:31:32Z",
            }],
        )
        (self.gh_dir / "open_prs.json").unlink()  # open-PR surface now fails
        rc, out = self.run_tool()
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("[open PRs]", out)
        self.assertEqual(self.surface_row(out, "recently-closed PRs"), ("HIT", 1))

    def test_4375_invoking_checkout_branch_is_weak_but_another_lane_blocks(self):
        # Re-running the pre-flight from the lane's own worktree must not refuse
        # its own dispatch. A DIFFERENT lane's branch/worktree for the SAME
        # issue is not self and must still block.
        mine = self.add_worktree("impl/3061-self", branch="fix/3061-self")
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("OWN branch", out)
        self.assertIn("OWN checkout", out)
        self.assertEqual(self.surface_row(out, "local branches"), ("HIT", 1))
        self.assertEqual(self.surface_row(out, "local worktrees"), ("HIT", 1))

        self.add_worktree("impl/3061-other", branch="fix/3061-other")
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("refs/heads/fix/3061-other", out)
        self.assertEqual(self.surface_row(out, "local branches"), ("HIT", 2))
        self.assertEqual(self.surface_row(out, "local worktrees"), ("HIT", 2))

    def test_4375_own_pushed_remote_branch_is_weak_not_blocking(self):
        # A lane that PUSHED its branch before re-checking sees its own work as
        # refs/remotes/origin/<branch>. That ref is the same work as the local
        # branch and must receive the same self relaxation: normalising only
        # refs/heads/ left the pushed form blocking, so pushing made the gate
        # refuse the lane's own dispatch. A DIFFERENT lane's pushed branch is
        # not self and still blocks.
        mine = self.add_worktree("impl/3061-pushed", branch="fix/3061-pushed")
        _git(self.repo, "update-ref",
             "refs/remotes/origin/fix/3061-pushed", "HEAD")
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("OWN branch", out)
        self.assertEqual(self.surface_row(out, "local branches"), ("HIT", 1))
        self.assertEqual(self.surface_row(out, "remote branches"), ("HIT", 1))

        _git(self.repo, "update-ref",
             "refs/remotes/origin/fix/3061-other", "HEAD")
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("refs/remotes/origin/fix/3061-other", out)
        self.assertEqual(self.surface_row(out, "remote branches"), ("HIT", 2))

    def test_4375_own_pr_head_branch_is_weak_not_blocking(self):
        # The issue's own PR usually carries the number in its BRANCH name, not
        # its GitHub number, so the PR surface needs the same self relaxation
        # as the branch surfaces. Without it the lane's own PR blocked its own
        # dispatch. A DIFFERENT lane's PR for the same issue still blocks.
        mine = self.add_worktree("impl/3061-ownpr", branch="fix/3061-ownpr")
        self.gh_fixtures(open_prs=[{
            "number": 9001, "title": "fix(collision): stop refusing dispatch",
            "body": "", "headRefName": "fix/3061-ownpr",
        }])
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("OWN branch", out)
        self.assertEqual(self.surface_row(out, "open PRs"), ("HIT", 1))

        self.gh_fixtures(open_prs=[{
            "number": 9002, "title": "unrelated", "body": "",
            "headRefName": "fix/3061-other",
        }])
        rc, out = self.run_tool(repo_arg=str(mine), cwd=mine)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertEqual(self.surface_row(out, "open PRs"), ("HIT", 1))

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
        self.assertIn("another account", out)

    def test_assignee_shared_account_is_unattributable_but_still_a_hit(self):
        # Every lane shares ONE account, so an assignee equal to our own login
        # cannot be attributed to THIS lane — nor can it be ruled out as any
        # other lane's. Suppressing it would blind the surface to every lane on
        # the fleet (a false negative, the worse failure mode), so it stays a
        # hit and the report says WHY (fail closed).
        self.gh_fixtures(issue=self.issue_payload(assignees=("test-agent",)))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("assignee:test-agent", out)
        self.assertIn("shared account", out)
        self.assertIn("fail closed", out)

    def test_issue_claim_comment_hit(self):
        self.gh_fixtures(issue=self.issue_payload(comments=("I'll claim this.",)))
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
        # a missing title would turn a clean run into exit 2. It is still BLIND
        # for keyword matching and the verdict must say so (#3378 P2-1).
        self.gh_fixtures(issue=self.issue_payload(title="fix graph delete error"))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertNotIn("INCOMPLETE", out)
        self.assertIn("0 distinctive keyword(s)", out)
        self.assertIn("excluded:", out)
        self.assertIn("BLIND", out)

        # ... and a NUMBER hit under that generic-only title is still a hit.
        _git(self.repo, "branch", "fix/3061-generic-title")
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("matched issue-number (3061)", out)

    def test_blind_keyword_surface_is_annotated_on_the_verdict(self):
        # The historical claim "the run cannot be CLEAN without the title" was
        # false: --keywords can complete the dimension, and a zero-distinctive
        # title still reported a complete 7/7 scan. When no distinctive keyword
        # exists the verdict must say BLIND instead of advertising completeness.
        self.gh_fixtures(issue=self.issue_payload(title=None))
        rc, out = self.run_tool(keywords=" ")
        self.assertEqual(rc, 0, out)
        self.assertIn("title: (unavailable", out)
        self.assertIn("VERDICT: CLEAN", out)
        self.assertIn("BLIND", out)
        self.assertIn("keyword-only collision could be missed", out)

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
        rc, out = self.run_tool(extra_args=["--min-keywords", "1"])
        self.assertNotEqual(rc, 0, out)
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
        self.assertIn("merged_at", argv)

    # ── target repo: never certify a scope you did not establish (#4027) ────

    def test_output_always_names_resolved_repo_and_full_title(self):
        # The cheap high-value half of #4027: a verdict that does not name what
        # it measured cannot be trusted, so the repo AND the full title appear
        # on every path, including the verdict line itself.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: test-owner/test-repo", out)
        self.assertIn("title: florfenicol dosing audit", out)
        self.assertIn("for #3061 in test-owner/test-repo", out)

        rc, out = self.run_tool(extra_args=["--keywords", "florfenicol,dosing"])
        self.assertIn("repo: test-owner/test-repo", out)
        self.assertIn("title: florfenicol dosing audit", out)

    def test_repo_scoped_gh_calls_carry_the_resolved_slug(self):
        # The exact mechanism of the cross-repo false CLEAN: gh resolving the
        # repo from the CWD instead of the intended target. Every
        # REPOSITORY-SCOPED gh surface must carry the explicit selector; the
        # REST path must be literal (gh api has no --repo flag, so
        # `{owner}/{repo}` placeholders would again resolve from the cwd).
        # The two deliberate non-repo-scoped calls are `gh repo view` (which
        # DISCOVERS the slug, so it cannot carry it) and `gh api user` (the
        # lane's account identity, not a repository); the claim in AGENTS.md is
        # scoped to repository-scoped calls precisely because of them.
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("test-owner/test-repo",
                      (self.gh_dir / "issue-argv.txt").read_text())
        self.assertIn("test-owner/test-repo",
                      (self.gh_dir / "pr-list-argv.txt").read_text())
        api = (self.gh_dir / "api-argv.txt").read_text()
        self.assertIn("repos/test-owner/test-repo/pulls", api)
        self.assertNotIn("{owner}", api)
        self.assertNotIn("{repo}", api)

    def test_absent_issue_fails_closed_exit_2_not_clean(self):
        # "Not found here" is NOT "no in-flight work". Before #4027 an absent
        # issue was indistinguishable from CLEAN.
        rc, out = self.run_tool(env_extra={"GH_STUB_ISSUE_ABSENT": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("issue-absent", out)
        self.assertIn("does not exist in test-owner/test-repo", out)

    def _sibling_repo(self, name: str, slug: str) -> Path:
        other = self.tmp / name
        other.mkdir()
        _git(other, "init", "-q", "-b", "main", "--template=")
        _git(other, "config", "user.email", "test@example.com")
        _git(other, "config", "user.name", "Test")
        _git(other, "remote", "add", "origin", f"https://github.com/{slug}.git")
        (other / "seed.txt").write_text("seed\n")
        _git(other, "add", "seed.txt")
        _git(other, "commit", "-qm", "seed")
        return other

    def test_owner_name_selector_targets_that_repo_and_its_local_clone(self):
        other = self._sibling_repo("other-repo", "other-owner/other-repo")
        rc, out = self.run_tool(repo_arg="other-owner/other-repo", cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: other-owner/other-repo", out)
        # The git surfaces describe the TARGET repo, never the cwd's repo.
        # (tmpdir paths are symlinked on macOS; compare real paths.)
        self.assertIn(f"local checkout: {os.path.realpath(other)}", out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_selector_without_a_local_clone_leaves_git_surfaces_incomplete(self):
        rc, out = self.run_tool(repo_arg="other-owner/no-such-clone", cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertIn("no-local-clone", out)

    def test_repo_selector_is_case_insensitive_for_the_clone(self):
        # GitHub and `gh --repo` accept `Owner/Name`, so a case-different
        # selector must still find the local clone rather than leaving every
        # git surface INCOMPLETE (a false exit 2 that blocks dispatch).
        other = self._sibling_repo("other-repo", "other-owner/other-repo")
        rc, out = self.run_tool(repo_arg="OTHER-OWNER/other-repo", cwd=self.repo)
        self.assertEqual(rc, 0, out)
        self.assertIn("repo: OTHER-OWNER/other-repo", out)
        self.assertIn(f"local checkout: {os.path.realpath(other)}", out)
        self.assertIn("VERDICT: CLEAN", out)

    def test_empty_repo_argument_is_usage_error(self):
        # Path("") IS a directory, so an empty --repo used to take the PATH
        # branch and run the git surfaces against the cwd while the report
        # printed "local checkout: (none — git surfaces INCOMPLETE)". An unset
        # shell variable must be a usage error, never the cwd.
        for bad in ("", "   "):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(repo_arg=bad)
                self.assertEqual(rc, 3, out)
                self.assertNotIn("VERDICT: CLEAN", out)
                self.assertIn("empty/whitespace", out)

    def test_omitted_repo_refuses_when_number_resolves_in_two_repos(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_holds.txt").write_text(
            "test-owner/test-repo\nother-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("refusing to guess", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_omitted_repo_refuses_when_number_resolves_elsewhere_only(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_holds.txt").write_text("other-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("does NOT exist in test-owner/test-repo", out)
        self.assertIn("re-run with --repo other-owner/other-repo", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    def test_omitted_repo_refuses_when_a_candidate_cannot_be_probed(self):
        self._sibling_repo("other-repo", "other-owner/other-repo")
        (self.gh_dir / "probe_unqueried.txt").write_text("other-owner/other-repo\n")
        rc, out = self.run_tool(repo_arg=None, cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("could not be probed", out)
        self.assertNotIn("VERDICT: CLEAN", out)

    # ── claims ALWAYS collide; there is no self-attribution (cycle 6) ───────

    def test_every_claim_shaped_comment_collides_including_our_own_marker(self):
        # The invariant the gate now rests on: identity-tied claim attribution
        # is REMOVED, so a comment matching `_CLAIM_RE` ALWAYS collides —
        # exactly origin/main's behaviour. That deliberately includes a comment
        # carrying OUR OWN lane marker or session id: a lane's own claim blocks
        # its own dispatch, because a false COLLISION costs one manual check
        # while a false CLEAN causes duplicate work. The bodies below are the
        # reproductions the removed tie rules mis-read as CLEAN, plus the
        # reader-vs-holder shapes (a pasted fleet board).
        our_session = "01a0b01d-ab9f-74d8-bbe1-1e218fc752b2"
        for body in (
            "I'll claim this — lane W0 is only cc'd.",
            "I'll claim #4027. lane W0 handles follow-up.",
            "I'll claim this. 'lane W0 owns it'",
            "I'll claim this on lane W3, not lane W0.",
            "I'll claim this, and lane W0 owns the follow-up.",
            f"Claiming this.\n\nFLEET BOARD — lane table W0 session `{our_session}`",
            "lane W0 is done here — I'll claim this",
            "Owner: lane W0 — claiming this.",
            f"claiming this (session {our_session}).",
        ):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("test-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertNotIn("VERDICT: CLEAN", out)
                self.assertIn("claim-style comment", out)

    def test_second_party_claim_still_collides(self):
        # A claim by a DIFFERENT party still blocks. Only the machine-unambiguous
        # claim arm is here; the terse fragments are WEAK by design (see
        # `test_terse_work_claim_phrases_are_weak_not_blocking`).
        for body in ("/claim", "Claiming this.", "I'll claim this",
                     "I will claim this", "we can claim this",
                     "claim #3061", "claiming the issue",
                     "I'm claiming it", "claim this one"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("claim-style comment", out)
                self.assertIn("other-agent", out)

    def test_terse_work_claim_phrases_are_weak_not_blocking(self):
        # #4368/F3: the non-`claim` fragments are ordinary English — a
        # complement or adverbial in prose, a predicate in a claim — and no
        # regex can separate the two readings. The whole arm is reported WEAK,
        # so a comment matching only it does not block.
        for body in ("I'll take this", "working on this now", "On it!",
                     "Handling this", "taking this", "will fix this",
                     "dispatching #3061", "assigned to me",
                     "I'm working on the collision preflight fix"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
                self.assertIn("work-claim phrase", out)
                self.assertIn("(weak)", out)

    def test_same_account_unmarked_claim_fails_closed(self):
        # Another lane shares our account; an unmarked claim by it must still
        # collide. With attribution removed there is no "whose is it" question
        # left to get wrong — every claim-shaped comment is a hit.
        self.gh_fixtures(issue=self.issue_payload(comments=[
            ("test-agent", "I will claim this."),
        ]))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("claim-style comment", out)

    def test_4368_prose_noun_does_not_arm_the_gate(self):
        # The false refusal this change fixes: `claim` as an ordinary NOUN is
        # core product vocabulary, and the mandated workflow posts evidence
        # comments, so the old bare `\bclaim(?:ing)?\b` made a commented issue
        # permanently undispatchable. These comments must NOT block.
        for body in (
            "This is a false claim to users.",
            "The claim that the surface is complete is wrong.",
            "We should be careful about claims like this.",
            "There is a claim of ownership over the docs.",
            # Punctuation between the NOUN and a following clause must not be
            # read as a verb+object separator. Nothing separates these from the
            # genuine `Claiming: #N` claim except the OBJECT (a determined noun
            # vs a number) — which is why the punctuation relaxation is
            # number-only.
            "This is a false claim. The issue is closed.",
            "There is a claim, the PR was merged.",
            "The claim — the issue is closed — is wrong.",
        ):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)

    def test_4368_claim_verb_arm_still_blocks_with_a_remedy(self):
        # The narrowing must not lose a genuine claim of work: the verb arm
        # still blocks and the refusal still names the comment.
        for body in ("I'll claim this.", "Claiming this one.",
                     "claim #4027", "I'm claiming the issue"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("REMEDY", out)
                self.assertIn("comment by other-agent", out)
                self.assertIn("NO dismissal switch", out)

    def test_4368_claim_punctuation_between_verb_and_object_still_blocks(self):
        # The object requirement must not demand ADJACENCY. Punctuation between
        # a claim verb and the issue number it governs is ordinary writing.
        # `main`'s bare-noun pattern matched these because it required nothing
        # at all; the narrowed arm required the object immediately after
        # whitespace / an attached `-`/`/`, so a `Claiming: #4027` handoff read
        # CLEAN — a FALSE CLEAN, the direction that duplicates work, and the
        # exact failure this gate exists to prevent.
        for body in ("Claiming: #4027", "Claiming - #4027",
                     "Claiming — #4027", "Claiming, #4027",
                     "Claiming (#4027)", "claim:#4027"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("claim-style comment", out)

    def test_4368_deictic_object_with_adverbial_still_blocks(self):
        # #4368/F1. The deictic `this` must not be guarded by the word that
        # FOLLOWS it. `_CLAIM_WORK_OBJECT_RE` accepted `this` only when a work
        # noun, punctuation, or end-of-string followed, so "Claiming this
        # now." — the most terse and most common handoff sentence in this
        # workflow — read fully CLEAN (not even advisory: `_TERSE_CLAIM_RE`
        # does not cover it). That is a FALSE CLEAN, the direction that
        # duplicates work. What separates the readings is the DETERMINER before
        # the verb: with one, `claim` is the NOUN ("a claim this strong");
        # without one, `claim this <any word>` is the verb governing the
        # deictic object.
        for body in ("Claiming this now.", "Claiming this as mine.",
                     "Claiming this for myself.", "Claiming this on lane W3.",
                     "claim this now", "Claiming this.", "Claiming it now."):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("claim-style comment", out)
        # The determiner reading stays CLEAN — the fix must not re-arm it.
        for body in ("a claim this strong", "the claim this strong",
                     "This is a false claim to users."):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)

    def test_4368_underscore_emphasis_between_verb_and_object_still_blocks(self):
        # #4368/F2. The number separator's punctuation class excluded `_`
        # because Python's `\w` includes it, yet the VERB pattern already models
        # `_` as markdown emphasis — so a markup-wrapped reference
        # (`Claiming: _#4027_`) read CLEAN. One character-class hole in the
        # tool's own declared rule: `_` is punctuation here.
        for body in ("Claiming: _#3061_", "Claiming: __#3061__",
                     "Claiming:_#3061", "Claiming _#3061_",
                     "Claiming: *#3061*"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)

    def test_4368_prose_noun_with_punctuated_number_does_not_arm_the_gate(self):
        # #4368/F4. The #4368 narrowing exists to stop a prose NOUN from
        # refusing a dispatch. The widened number separator re-armed it for a
        # NOUN followed by a number: "This is a false claim, #3061 is
        # unrelated." collided on this branch and was CLEAN on the parent. A
        # gate that always fires is a gate that gets worked around, and in this
        # repo the mandated workflow posts evidence comments — an issue whose
        # evidence reads "…claim, #N…" would become undispatchable. A bare
        # `claim` is the NOUN form too, so it gets the wide separator only for
        # a claim-scope marker; a gerund (`claiming`) keeps it.
        for body in ("This is a false claim, #3061 is unrelated.",
                     "This is a false claim, #4027 is unrelated.",
                     "There is a claim. #3061 is closed."):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: CLEAN", out)
                self.assertNotIn("do NOT dispatch", out)
        # ...but the punctuation that IS a claim still blocks.
        for body in ("Claiming, #4027", "claim:#4027", "claim #4027"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)

    def test_4368_other_number_notations_still_block(self):
        # #4368/F3 (residual). A genuine claim that carries the reference in
        # another notation must still block. The machine-unambiguous notations
        # are covered here; a BARE number with no `#`/`GH-`/URL marker
        # ("Claiming 3061") is a DOCUMENTED residual — asserted CLEAN below so
        # the residual is pinned rather than silently claimed as fixed.
        for body in ("Claiming issue #3061", "Claiming GH-3061",
                     "Claiming # 3061",
                     "Claiming https://github.com/owner/repo/issues/3061",
                     "Claiming: <b>#3061</b>"):
            with self.subTest(body=body):
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
        self.gh_fixtures(issue=self.issue_payload(comments=[
            ("other-agent", "Claiming 3061"),
        ]))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertIn("VERDICT: CLEAN", out)

    # ── claim classification: a blocking verb arm + a weak terse arm ────────

    def test_classification_is_claim_re_without_tiers(self):
        # Direct pattern contract. There is still no `classify_claim` / tier
        # machinery: `_CLAIM_RE` is the BLOCKING arm and `_TERSE_CLAIM_RE` is
        # the WEAK arm, and strength is decided by which one matched.
        cp = _tool_module()
        self.assertFalse(hasattr(cp, "classify_claim"))
        self.assertFalse(hasattr(cp, "_CLAIM_WEAK_RE"))
        self.assertFalse(hasattr(cp, "_CLAIM_STRONG_RES"))
        for body in (
            "/claim", "Claiming this.", "I'll claim this", "claim #4027",
            "claiming the issue", "I'm claiming it", "claim this one",
            "we can claim this", "I will claim this",
        ):
            with self.subTest(body=body):
                self.assertIsNotNone(cp._CLAIM_RE.search(body), body)
        for body in (
            "The PR claims that the surface is complete.",  # `claims` != `claim`
            "the PR claims it is complete",
            "a false claim to users",
            "the claim that X is true",
            "claims about Y",
            "I am not claiming this",
        ):
            with self.subTest(body=body):
                self.assertIsNone(cp._CLAIM_RE.search(body), body)
        for body in ("I'll take this", "working on this now", "On it!",
                     "Handling this", "will fix this", "dispatching #3061"):
            with self.subTest(body=body):
                self.assertIsNone(cp._CLAIM_RE.search(body), body)
                self.assertIsNotNone(cp._TERSE_CLAIM_RE.search(body), body)

    def test_escape_stripper_keeps_whitespace_and_still_removes_sequences(self):
        # Cycle-3 regression fixed: the `\x1b.` alternative consumed ESC plus
        # the FOLLOWING character, so a bare ESC before whitespace merged two
        # words ("I'll take this\x1b now" -> "I'll take thisnow"). Both
        # directions are proved: real sequences are still stripped whole (a CSI
        # sequence and an OSC 52 clipboard payload), and a bare ESC no longer
        # eats the space, so the claim still reads and matches.
        cp = _tool_module()
        self.assertEqual(cp._strip_control_sequences("a\x1b[2Kb"), "ab")
        self.assertEqual(cp._strip_control_sequences("a\x1b]52;c;AAAA\x07b"), "ab")
        cleaned = cp._strip_control_sequences("I'll claim this\x1b now")
        self.assertEqual(cleaned, "I'll claim this now")
        self.assertIsNotNone(cp._CLAIM_RE.search(cleaned))
        # A BARE ESC directly before a claim must not hide it: the pre-fix
        # fallback consumed the following character ("\x1bclaim this." ->
        # "laim this."), and the gate read CLEAN on a real claim.
        self.assertEqual(cp._strip_control_sequences("\x1bclaim this."), "claim this.")
        self.assertIsNotNone(cp._CLAIM_RE.search(cp._strip_control_sequences("\x1bclaim this.")))
        # The classification path applies the same stripper, so the end-to-end
        # run sees the claim too rather than a merged "thisnow".
        self.gh_fixtures(issue=self.issue_payload(comments=[
            ("other-agent", "I'll claim this\x1b now"),
        ]))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("VERDICT: COLLISION", out)

    def test_raw_body_or_stripped_body_matches_a_claim(self):
        # Classification matches the RAW body OR the de-sequenced text, so an
        # escape can never HIDE a claim the other variant would find. Each
        # body below matches on at least one variant.
        cp = _tool_module()
        for body in (
            "\x1bI'll claim this",    # Fe escape eats the I -> raw matches
            "I'll claim\x1b this",     # subject arm needs no object
            "claim\x1b this",          # stripped variant recovers the object
        ):
            with self.subTest(body=body):
                self.assertTrue(
                    cp._CLAIM_RE.search(body)
                    or cp._CLAIM_RE.search(cp._strip_control_sequences(body))
                    or cp._TERSE_CLAIM_RE.search(body)
                    or cp._TERSE_CLAIM_RE.search(
                        cp._strip_control_sequences(body)),
                    body)
                self.gh_fixtures(issue=self.issue_payload(comments=[
                    ("other-agent", body),
                ]))
                rc, out = self.run_tool()
                self.assertNotEqual(rc, 0, f"body={body!r}\n{out}")
                self.assertIn("VERDICT: COLLISION", out)
                self.assertIn("claim-style comment", out)

    def test_claim_hit_report_names_the_comment_id(self):
        # The REMEDY line tells the reader to open the comment that caused the
        # refusal, so the hit must be identified specifically — a bare author
        # login is not enough on a fleet sharing one account.
        self.gh_fixtures(issue=self.issue_payload(comments=[{
            "id": "IC_kwDOAAA123",
            "author": {"login": "other-agent"},
            "body": "I'll claim this.",
        }]))
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertIn("comment by other-agent [id IC_kwDOAAA123]", out)
        self.assertIn("REMEDY", out)

    # ── terminal-injection hardening (#4027 P2-J) ──────────────────────────

    def test_control_sequences_in_untrusted_fields_are_stripped(self):
        # The report IS the artifact a human reads to decide "do NOT dispatch".
        # ESC/CSI/OSC from a GitHub title or comment body can blank or overwrite
        # the VERDICT line (and OSC 52 rewrites the clipboard) — a fail-open
        # class. No control character may reach stdout.
        evil = "normal\x1b[2K\x1b[1A title"
        self.gh_fixtures(
            issue=self.issue_payload(title=evil, comments=[
                ("other-agent", "I'll claim this \x1b]52;c;AAAA\x07 now"),
            ]),
            open_prs=[{
                "number": 1, "title": "evil\x1b[2K pr", "body": "",
                "headRefName": "fix/evil",
            }],
        )
        rc, out = self.run_tool()
        self.assertNotEqual(rc, 0, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("VERDICT: COLLISION", out)
        self.assertIn("I'll claim this", out)

    def test_control_sequences_in_issue_state_are_stripped(self):
        # `state` is the one GitHub-sourced field that reached a note RAW
        # (`f"issue state={state} …"`), and `format_report` prints notes
        # verbatim — so `_sanitize` never saw it. The same fail-open class as
        # the title/comment fields: an ESC can blank the VERDICT line and OSC
        # 52 can rewrite the clipboard. Sanitised at CONSTRUCTION, so every
        # consumer of `surface.note` sees a clean string.
        evil = "CLOSED\x1b[2K\x1b]52;c;AAAA\x07"
        self.gh_fixtures(issue=self.issue_payload(state=evil))
        rc, out = self.run_tool()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)
        self.assertIn("issue state=CLOSED", out)

    def test_invalid_remote_slug_is_rejected_not_printed(self):
        # A git-remote-derived slug is untrusted AND printed. A slug that is not
        # exactly owner/name is rejected, leaving the surfaces INCOMPLETE rather
        # than spraying control bytes into the report.
        evil = self.tmp / "evil-repo"
        evil.mkdir()
        _git(evil, "init", "-q", "-b", "main", "--template=")
        _git(evil, "config", "user.email", "test@example.com")
        _git(evil, "config", "user.name", "Test")
        _git(evil, "remote", "add", "origin",
             "https://github.com/evil\x1b[2K/wat.git")
        rc, out = self.run_tool(repo_arg=str(evil), cwd=self.repo)
        self.assertEqual(rc, 2, out)
        self.assertIn("VERDICT: INCOMPLETE", out)
        self.assertNotIn("VERDICT: CLEAN", out)
        self.assertNotIn("\x1b", out)

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

    def test_usage_error_on_non_finite_closed_pr_timeout(self):
        # `nan` / `inf` defeat a bare positivity check (`nan <= 0` and
        # `inf <= 0` are BOTH False) and then raise inside
        # subprocess.run(timeout=…) — an uncaught ValueError/OverflowError, so
        # the run ends exit 1 (the COLLISION code) with a traceback and no
        # VERDICT at all. They must be EXIT_USAGE.
        # `--closed-pr-timeout -inf` (space-separated) is rejected by argparse
        # itself as an option-like token, so the `=` form is used to reach the
        # validator under test.
        for bad in ("nan", "inf", "-inf", "abc", "60s"):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(extra_args=[f"--closed-pr-timeout={bad}"])
                self.assertEqual(rc, 3, f"{bad}: {out}")
                self.assertIn("--closed-pr-timeout", out)
                self.assertNotIn("Traceback", out)

    def test_usage_error_on_bad_closed_pr_timeout_env(self):
        # The env seam is a documented input too. The old eager
        # `float(os.environ[...])` ran at add_argument time, so a typo'd or
        # empty variable raised an uncaught ValueError -> exit 1 + traceback,
        # i.e. a misconfiguration reported as a phantom COLLISION.
        for bad in ("abc", "nan", "inf", "0", ""):
            with self.subTest(bad=bad):
                rc, out = self.run_tool(
                    env_extra={"COLLISION_PREFLIGHT_CLOSED_PR_TIMEOUT": bad})
                self.assertEqual(rc, 3, f"{bad}: {out}")
                self.assertIn("--closed-pr-timeout", out)
                self.assertNotIn("Traceback", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
