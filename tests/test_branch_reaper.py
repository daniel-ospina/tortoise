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
  * a worktree created AFTER the initial enumeration is still seen by the delete
    phase (the held snapshot is re-read on every --apply path)
  * a missing worktree engine -> INCOMPLETE / exit 2 (fail closed, not exit 5)
  * a symlinked --report path -> refused, target untouched
  * --apply cannot be armed by an abbreviated flag (--ap)
  * an OPEN PR's base branch -> PRESERVE (base-ref veto)
  * an Incomplete AFTER a deletion -> distinct exit 6, not exit 2
"""
from __future__ import annotations

import json
import os
import shlex
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

    def add_pr(self, state: str, head: str, sha: str, base: str = "main") -> int:
        pr = {"number": self._next_pr(), "head": {"ref": head, "sha": sha},
              "base": {"ref": base},
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

    def rows(self, args=None, repo=None):
        rc, out, err = self.run_tool(["--json", *(args or [])], repo=repo)
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
        # The CURRENT branch of the checkout being classified is protected even
        # when it is not trunk: the driver worktree is on `driver/base`, which is
        # an ancestor of main and would otherwise classify SAFE.
        cur = self.rows(repo=self.driver)["driver/base"]
        self.assertEqual(cur["verdict"], "PRESERVE")
        self.assertEqual(cur["reason"], "trunk")

    def test_open_pr_base_branch_is_preserved(self):
        # P2-2: a local branch that is ONLY an open PR's base must not be deleted
        # by rule 3 (ancestry) — deleting it would break a live PR. Live shape in
        # this repo: PR #4181's base is the local branch `feat/2409-contact-form`.
        self.branch_at_main("feat/base-target")
        self.add_pr("open", "feature/child", self.main_sha, base="feat/base-target")
        self.write_fixtures()
        row = self.rows()["feat/base-target"]
        self.assertEqual(row["verdict"], "PRESERVE")
        self.assertEqual(row["reason"], "open-pr-base")

        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertIn("feat/base-target", self.branches())

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
        # The OPEN list is one page (< cap); the CLOSED list reaches the cap, so
        # the truncation must be detected on the CLOSED surface (a test that trips
        # on the open list first would pass even if the closed check were gone).
        filler = {"number": 1, "head": {"ref": "x", "sha": "y"}, "merged_at": None}
        self.write_fixtures(closed_pages=[[filler], [filler]])
        rc, out, err = self.run_tool(["--apply", "--max-pr-pages", "2"], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("closed", err)
        self.assertIn("merged/branch", self.branches())

    def test_unqueryable_gh_is_incomplete(self):
        self.commit_on("merged/branch", "merged work")
        self.write_fixtures()
        rc, _, err = self.run_tool(["--apply"], repo=self.driver,
                                   env_extra={"GH_STUB_FAIL": "1"})
        self.assertEqual(rc, 2)
        self.assertIn("INCOMPLETE", err)

    def test_missing_worktree_engine_is_incomplete_exit_2(self):
        # P2-4(b): the DOCUMENTED contract for a missing engine is INCOMPLETE /
        # exit 2 (fail closed, nothing deleted). Without the isfile guard, bash
        # exits 127, which the translation table maps to exit 5 (internal error)
        # — a contract change. This pins exit 2.
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        missing = self.tmp / "no-such-engine.sh"
        rc, out, err = self.run_tool(["--apply", "--reap-worktrees",
                                      "--worktree-engine", str(missing)], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("worktree engine not found", err)
        self.assertIn("merged/branch", self.branches())

    # ── worktree safety mutations ───────────────────────────────────────────

    def test_dirty_worktree_branch_is_kept(self):
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-held"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        (wt / "untracked.txt").write_text("uncommitted work the lane left behind\n")

        # The dirt is REPORTED (the issue requires it) and the branch is preserved.
        row = self.rows()["held/branch"]
        self.assertEqual(row["verdict"], "SAFE")
        self.assertTrue(row["worktree"])
        self.assertTrue(row["dirty"], "dirty worktree must be reported as dirty")

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

    def test_late_worktree_is_re_read_before_delete(self):
        # P1-1: enum_worktrees runs BEFORE the two paginated gh api calls. A
        # worktree created in that window must still be seen by the delete phase,
        # because `git update-ref -d` does NOT refuse a checked-out branch. The
        # stub creates the worktree while serving the CLOSED pages — i.e. after
        # the initial enumeration and before the hoisted re-read.
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        late_wt = self.tmp / "wt-late"
        late_stub = _write_exec(self.gh_dir / "gh-late", f"""#!/usr/bin/env bash
set -u
d="${{GH_STUB_DIR:?GH_STUB_DIR unset}}"
state=""
for a in "$@"; do
  case "$a" in
    *state=open*) state=open ;;
    *state=closed*) state=closed ;;
  esac
done
[ -n "$state" ] || exit 1
if [ "$state" = "closed" ]; then
  git -C {shlex.quote(str(self.repo))} worktree add {shlex.quote(str(late_wt))} merged/branch >/dev/null 2>&1 || true
fi
cat "$d/${{state}}_pages.json"
""")
        rc, out, err = self.run_tool(["--apply"], repo=self.driver,
                                     env_extra={"BRANCH_REAPER_GH": str(late_stub)})
        self.assertEqual(rc, 0, err + out)
        self.assertIn("merged/branch", self.branches())

    def test_apply_cannot_be_armed_by_abbreviation(self):
        # P2-1: argparse abbreviates by default, so `--ap` arms the destructive
        # path. A destructive arming flag must be exact.
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--ap"], repo=self.driver)
        self.assertNotEqual(rc, 0, out)
        self.assertIn("unrecognized arguments", err)
        self.assertIn("merged/branch", self.branches())

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

    def test_ref_moved_between_classify_and_delete_is_refused(self):
        # Import the module and call the delete phase directly with a stale OID,
        # simulating a lane committing after classification. The atomic
        # compare-and-delete must refuse rather than delete the moved ref.
        import importlib.util

        spec = importlib.util.spec_from_file_location("branch_reaper", TOOL)
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        sha = self.commit_on("merged/branch", "merged work")
        rows = [{"branch": "merged/branch", "oid": self.main_sha, "ts": 0,
                 "verdict": "SAFE", "reason": "pr-merged-tip"}]
        results = br.delete_branches(str(self.repo), rows, set(), backup_bundle=None)
        self.assertEqual(results[0]["result"], "refused")
        self.assertIn("merged/branch", self.branches())
        self.assertTrue(sha)  # branch still exists at its real tip

    def test_all_zero_expected_oid_is_refused_not_deleted(self):
        # git treats an all-zero old-oid as "no old value" and would delete
        # unconditionally — the compare-and-delete sentinel must never be zeros.
        import importlib.util

        spec = importlib.util.spec_from_file_location("branch_reaper", TOOL)
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        self.commit_on("merged/branch", "merged work")
        rows = [{"branch": "merged/branch", "oid": "0" * 40, "ts": 0,
                 "verdict": "SAFE", "reason": "pr-merged-tip"}]
        results = br.delete_branches(str(self.repo), rows, set(), backup_bundle=None)
        self.assertEqual(results[0]["result"], "refused")
        self.assertIn("merged/branch", self.branches())

    def test_report_written_before_deletion_contains_recovery_record(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        report = self.tmp / "report.md"
        rc, out, err = self.run_tool(["--apply", "--report", str(report)], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        body = report.read_text()
        self.assertIn("Recovery record", body)
        self.assertIn(sha, body)  # FULL 40-char tip, not the abbreviated table form
        self.assertIn("Post-apply results", body)
        # A durable, machine-readable recovery record is written unconditionally
        # before the delete phase.
        rec = report.parent / (report.name + ".recovery.json")
        self.assertTrue(rec.exists())
        payload = json.loads(rec.read_text())
        self.assertIn(sha, [b["oid"] for b in payload["branches"]])

    def test_apply_without_report_still_writes_recovery_json(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        rec = self.driver / "branch-reaper-recovery.json"
        self.assertTrue(rec.exists())
        self.assertIn(sha, [b["oid"] for b in json.loads(rec.read_text())["branches"]])

    def test_backup_bundle_written_before_deletion(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        bundle = self.tmp / "backup.bundle"
        rc, out, err = self.run_tool(
            ["--apply", "--backup-bundle", str(bundle)], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertTrue(bundle.exists() and bundle.stat().st_size > 0)
        self.assertNotIn("merged/branch", self.branches())
        # The bundle really carries the deleted tip.
        verify = _run(["git", "bundle", "verify", str(bundle)], cwd=self.repo, check=False)
        self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_backup_bundle_failure_aborts_deletion(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory\n")
        bad = blocker / "backup.bundle"  # parent is a FILE -> cannot be created
        report = self.tmp / "r.md"
        rc, out, err = self.run_tool(
            ["--apply", "--report", str(report), "--backup-bundle", str(bad)],
            repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("INCOMPLETE", err)
        # fail-closed: nothing deleted
        self.assertIn("merged/branch", self.branches())
        # and the recovery record was written BEFORE the (aborted) delete phase
        rec = report.parent / (report.name + ".recovery.json")
        self.assertTrue(rec.exists())
        self.assertIn(sha, [b["oid"] for b in json.loads(rec.read_text())["branches"]])

    def test_report_symlink_path_is_refused_and_target_untouched(self):
        # P2-4(a): `_write_text_safe` (the --report writer) must REFUSE a
        # symlinked target. Without the guard, os.replace silently REPLACES the
        # link rather than failing, so this asserts the documented refusal
        # (exit 2 + message) — which fails the moment the guard is removed.
        self.write_fixtures()
        victim = self.tmp / "victim.md"
        victim.write_text("do not clobber\n")
        link = self.tmp / "report.md"
        os.symlink(victim, link)
        rc, out, err = self.run_tool(["--report", str(link)])
        self.assertEqual(rc, 2, err + out)
        self.assertIn("symlink", err)
        self.assertEqual(victim.read_text(), "do not clobber\n")
        self.assertTrue(link.is_symlink())

    def test_incomplete_after_a_deletion_returns_distinct_exit(self):
        # P1-2: exit 2 documents "Nothing was deleted". Once a deletion has
        # landed, a later Incomplete must surface a DISTINCT code so automation
        # does not read a false "nothing deleted".
        import importlib.util

        spec = importlib.util.spec_from_file_location("branch_reaper", TOOL)
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        real_delete = br.delete_branches
        target = {"branch": "merged/branch", "oid": sha, "ts": 0,
                  "verdict": "SAFE", "reason": "pr-merged-tip"}

        def partial_delete(repo_root, rows, held, *, backup_bundle, results=None):
            real_delete(repo_root, [target], held, backup_bundle=None, results=results)
            raise br.Incomplete("simulated timeout on a later ref")

        br.delete_branches = partial_delete
        saved = {k: os.environ.get(k) for k in ("BRANCH_REAPER_GH", "GH_STUB_DIR")}
        os.environ["BRANCH_REAPER_GH"] = str(self.gh)
        os.environ["GH_STUB_DIR"] = str(self.gh_dir)
        try:
            rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo", "--apply"])
        finally:
            br.delete_branches = real_delete
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(rc, br.EXIT_INCOMPLETE_AFTER_DELETE, rc)
        self.assertNotIn("merged/branch", self.branches())

    def test_backup_bundle_symlink_path_is_refused(self):
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        victim = self.tmp / "victim.txt"
        victim.write_text("do not clobber\n")
        link = self.tmp / "backup.bundle"
        os.symlink(victim, link)
        rc, out, err = self.run_tool(
            ["--apply", "--backup-bundle", str(link)], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("symlink", err)
        self.assertIn("merged/branch", self.branches())
        self.assertEqual(victim.read_text(), "do not clobber\n")

    def test_engine_that_frees_a_held_branch_deletes_and_records_it(self):
        # The recovery record must be derived from the POST-teardown held set:
        # a branch the delegate frees and the reaper then deletes must be recorded.
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-clean"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")  # clean
        engine = _write_exec(
            self.driver / "scripts" / "pi-reap-worktrees.sh",
            "#!/usr/bin/env bash\nset -e\n"
            f"git -C {shlex.quote(str(self.driver))} worktree remove {shlex.quote(str(wt))}\n"
            "echo 'REMOVED=1'\nexit 0\n")
        report = self.tmp / "r.md"
        rc, out, err = self.run_tool(
            ["--apply", "--report", str(report), "--reap-worktrees",
             "--worktree-engine", str(engine)], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertNotIn("held/branch", self.branches())  # freed, then deleted
        rec = report.parent / (report.name + ".recovery.json")
        self.assertIn(sha, [b["oid"] for b in json.loads(rec.read_text())["branches"]])

    def test_clean_worktree_reports_not_dirty(self):
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-clean"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        self.assertFalse(self.rows()["held/branch"]["dirty"])

    def test_ignored_only_worktree_is_not_dirty(self):
        # An ignored-only checkout is NOT dirt: `git worktree remove` does not
        # refuse ignored files, so treating a regenerable .venv as dirt would
        # make every worktree unreapable.
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        _git(self.repo, "add", ".gitignore")
        _git(self.repo, "commit", "-q", "-m", "ignore")
        self.main_sha = _git_out(self.repo, "rev-parse", "HEAD")
        _git(self.repo, "update-ref", "refs/remotes/origin/main", self.main_sha)
        sha = self.commit_on("held/branch", "safe work")
        self.add_pr("merged", "held/branch", sha)
        self.write_fixtures()
        wt = self.tmp / "wt-ignored"
        _git(self.repo, "worktree", "add", str(wt), "held/branch")
        (wt / "ignored.txt").write_text("regenerable\n")
        self.assertFalse(self.rows()["held/branch"]["dirty"])

    def test_tool_carveout_pins_the_reaper_tests(self):
        # A tools/branch_reaper.py-only diff must still run this file (the flat
        # tools/ prefix otherwise drops it to tier-1 smoke).
        import importlib.util

        spec = importlib.util.spec_from_file_location("ci_selection", ROOT / "tools" / "ci_selection.py")
        cs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cs)
        self.assertIn("tools/branch_reaper.py", cs.TOOL_CARVEOUTS)
        sel = cs.select(["tools/branch_reaper.py"], "pull_request", cs.load_manifest())
        self.assertTrue(sel["full"], "a reaper-only diff must fail closed to the full matrix")


if __name__ == "__main__":
    unittest.main(verbosity=2)
