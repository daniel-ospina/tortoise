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
  * an aborted-FIRST delete (a killed update-ref) -> distinct exit 6, not exit 2
  * a worktree created DURING `git bundle create` -> branch kept, worktree intact
  * a worktree created between the guard read and its own delete -> ref RESTORED
  * the POST-delete report write failing after a real deletion -> exit 6
  * a late worktree's dirt is refreshed from the re-read, not the stale snapshot
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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

    def _load_tool(self):
        """Import tools/branch_reaper.py in-process (the monkeypatchable path)."""
        spec = importlib.util.spec_from_file_location("branch_reaper", TOOL)
        br = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(br)
        return br

    @contextlib.contextmanager
    def _stub_env(self):
        saved = {k: os.environ.get(k) for k in ("BRANCH_REAPER_GH", "GH_STUB_DIR")}
        os.environ["BRANCH_REAPER_GH"] = str(self.gh)
        os.environ["GH_STUB_DIR"] = str(self.gh_dir)
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

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

    def test_absent_gh_binary_is_incomplete_exit_2(self):
        # The sibling of test_missing_worktree_engine_is_incomplete_exit_2, for
        # the `gh` surface. An ABSENT gh is an UNQUERYABLE surface, so it must
        # take the documented INCOMPLETE / exit 2. Without the OSError arm in
        # _run it escaped as FileNotFoundError -> exit 1 + a traceback, a
        # contract the exit-code table does not contain.
        self.commit_on("merged/branch", "merged work")
        self.write_fixtures()
        rc, out, err = self.run_tool(
            ["--apply"], repo=self.driver,
            env_extra={"BRANCH_REAPER_GH": str(self.tmp / "no-such-gh")})
        self.assertEqual(rc, 2, err + out)
        self.assertIn("INCOMPLETE", err)
        self.assertIn("cannot execute", err)
        self.assertIn("merged/branch", self.branches())

    def test_toplevel_oserror_is_incomplete_exit_2(self):
        # The true top-level boundary (`main`) with NOTHING deleted: an OSError
        # raised anywhere inside `_run_main` that the two richer handlers do not
        # wrap — `resolve_repo`'s `os.getcwd()`, or a `build_report` failure —
        # must be the documented exit 2, never a traceback with an undocumented 1.
        # (The out-of-range clock that used to drive this now formats defensively
        # instead of raising, so it is forced here directly.)
        br = self._load_tool()
        real_run = br._run_main
        br._LANDED = False

        def boom(_args):
            raise OSError(2, "No such file or directory")

        br._run_main = boom
        try:
            rc = br.main(["--repo", str(self.repo), "--slug", "owner/repo"])
        finally:
            br._run_main = real_run
        self.assertEqual(rc, 2, rc)

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

    def test_a_refused_deletion_returns_exit_4(self):
        # Pins the CLI contract for EXIT_PARTIAL. Nothing pinned it, so a
        # regression that left the refused->EXIT_PARTIAL tail unreachable turned
        # the documented exit 4 into a silent 0 with the suite still green.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        real_delete = br.delete_branches

        def refuse(repo_root, rows, *, backup_bundle, results=None, **kwargs):
            out = real_delete(repo_root, rows, backup_bundle=backup_bundle,
                              results=results)
            for r in results or []:
                r["result"] = "refused"
            return out

        br.delete_branches = refuse
        try:
            with self._stub_env():
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply"])
        finally:
            br.delete_branches = real_delete
        self.assertEqual(rc, 4, rc)

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
        results = br.delete_branches(str(self.repo), rows, backup_bundle=None)
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
        results = br.delete_branches(str(self.repo), rows, backup_bundle=None)
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

    def test_a_tag_colliding_with_a_branch_keeps_the_plain_branch_key(self):
        # `%(refname:short)` is the shortest UNAMBIGUOUS name across ALL refs, so a
        # tag named like a branch turns the key into `heads/release/1.0`: the PR
        # match then misses (skipping the open-PR veto) and the delete builds a
        # nonexistent `refs/heads/heads/...`, refusing every delete.
        br = self._load_tool()
        sha = self.commit_on("release/1.0", "release work")
        _git(self.repo, "tag", "release/1.0", sha)
        got = br.enum_branches(str(self.repo))
        self.assertIn("release/1.0", got, sorted(got))
        self.assertNotIn("heads/release/1.0", got, sorted(got))

    def test_recovery_record_covers_held_safe_branches_too(self):
        # A worktree holding a SAFE branch can be released between the recovery
        # write and `delete_branches`' own held re-read — the branch IS then
        # deleted. Excluding held rows can therefore leave a DELETED tip out of
        # the only durable record (`update-ref -d` also removes its reflog).
        sha = self.commit_on("merged/held", "held work")
        self.add_pr("merged", "merged/held", sha)
        _git(self.repo, "worktree", "add", str(self.tmp / "held-wt"), "merged/held")
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        rec = self.driver / "branch-reaper-recovery.json"
        names = [b["branch"] for b in json.loads(rec.read_text())["branches"]]
        self.assertIn("merged/held", names, names)

    def test_a_malformed_slug_is_a_usage_error(self):
        br = self._load_tool()
        with self._stub_env():
            rc = br.main(["--repo", str(self.driver), "--slug", "a b/c"])
        self.assertEqual(rc, 3, rc)

    def test_a_ported_github_remote_still_yields_owner_name(self):
        br = self._load_tool()
        _git(self.repo, "remote", "add", "origin", "ssh://git@github.com:22/o/n.git")
        self.assertEqual(br._slug_from_remote(str(self.repo)), "o/n")

    def test_apply_writes_a_backup_bundle_by_default(self):
        sha = self.commit_on("merged/b", "work")
        self.add_pr("merged", "merged/b", sha)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertTrue(list(self.driver.glob("branch-reaper-backup-*.bundle")),
                        "no backup bundle written by default")

    def test_no_backup_skips_the_bundle(self):
        sha = self.commit_on("merged/b", "work")
        self.add_pr("merged", "merged/b", sha)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply", "--no-backup"], repo=self.driver)
        self.assertEqual(rc, 0, err + out)
        self.assertEqual(list(self.driver.glob("branch-reaper-backup-*.bundle")), [])

    def test_markdown_cells_escape_pipes_and_backticks(self):
        # A refname may legally contain both, so a crafted branch name would break
        # the report's cell boundary and its surrounding code span.
        br = self._load_tool()
        self.assertEqual(br._md("a|b"), "a\\|b")
        self.assertEqual(br._md("a`b"), "a&#96;b")

    def test_current_branch_is_unambiguous_under_a_tag_collision(self):
        # `rev-parse --abbrev-ref HEAD` returns `heads/release/1.0` when a tag
        # collides, which matches no `enum_branches` key — so the "current branch
        # is PRESERVE" rule would silently protect nothing.
        br = self._load_tool()
        sha = self.commit_on("release/1.0", "release work")
        _git(self.repo, "tag", "release/1.0", sha)
        _git(self.repo, "checkout", "release/1.0")
        self.assertEqual(br.current_branch(str(self.repo)), "release/1.0")

    def test_a_report_directly_in_the_system_tmp_is_allowed(self):
        # macOS makes /tmp a symlink to /private/tmp. Refusing EVERY symlinked
        # ancestor would refuse an ordinary temp path — and `--apply
        # --report /tmp/r.md` would abort before deleting anything.
        br = self._load_tool()
        target = Path("/tmp") / f"branch-reaper-{os.urandom(4).hex()}.md"
        br._write_text_safe(str(target), "hello\n", str(self.repo))
        self.assertTrue(target.read_text().startswith("hello"))
        target.unlink()

    def test_a_repo_planted_symlinked_directory_is_still_refused(self):
        br = self._load_tool()
        outside = self.tmp / "outside"
        outside.mkdir()
        os.symlink(str(outside), str(self.repo / "planted"))
        with self.assertRaises(br.Incomplete):
            br._write_text_safe(str(self.repo / "planted" / "r.md"), "x\n", str(self.repo))
        self.assertEqual(list(outside.iterdir()), [])

    def test_pr_ref_rejects_a_malformed_nested_shape(self):
        # A string `head` would otherwise raise AttributeError and escape as an
        # internal fault (exit 5) instead of the documented INCOMPLETE; skipping
        # it instead would drop the open-PR veto, which is fail-OPEN.
        br = self._load_tool()
        with self.assertRaises(br.Incomplete):
            br._pr_ref({"number": 1, "head": "oops"}, "head")
        with self.assertRaises(br.Incomplete):
            br._pr_ref({"number": 1, "head": {"ref": 7}}, "head")
        self.assertEqual(br._pr_ref({"number": 1, "head": {"ref": "x"}}, "head"), "x")
        self.assertIsNone(br._pr_ref({"number": 1}, "head"))

    def test_the_repo_owner_name_form_is_slug_validated_too(self):
        br = self._load_tool()
        with self._stub_env():
            rc = br.main(["--repo", "a b/c"])
        self.assertEqual(rc, 3, rc)

    def test_overwriting_an_existing_backup_bundle_is_refused(self):
        # The bundle is the DURABLE artifact; `os.replace` over an existing one
        # would discard the only durable copy of an earlier run's tips.
        br = self._load_tool()
        dest = self.tmp / "existing.bundle"
        dest.write_text("already here")
        with self.assertRaises(br.Incomplete):
            br._make_backup_bundle(str(self.repo), str(dest), [])
        self.assertEqual(dest.read_text(), "already here")

    def test_a_pr_without_a_head_ref_aborts_instead_of_dropping_the_veto(self):
        # `continue` here silently drops the PR, and an unattributable OPEN PR is
        # the veto that stops its branch being deleted by ancestry — a fail-open
        # that deleted the branch in practice.
        _git(self.repo, "branch", "victim", self.main_sha)
        (self.repo / "g.txt").write_text("g\n")
        _git(self.repo, "add", "g.txt")
        _git(self.repo, "commit", "-m", "advance")
        _git(self.repo, "update-ref", "refs/remotes/origin/main",
             _git_out(self.repo, "rev-parse", "HEAD"))
        self.write_fixtures(open_pages=[[{"number": 7, "base": {"ref": "main"}}]])
        rc, out, err = self.run_tool(["--apply", "--no-backup"], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("victim", self.branches())

    def test_an_open_pr_without_a_base_ref_aborts(self):
        self.write_fixtures(open_pages=[[{"number": 8, "head": {"ref": "x"}}]])
        rc, out, err = self.run_tool([], repo=self.driver)
        self.assertEqual(rc, 2, err + out)

    def test_a_dotdot_write_path_is_refused(self):
        # abspath collapses `..` LEXICALLY while the OS resolves `symlink/..`
        # against the link target, so a `..` past a planted link escapes the
        # repo-planted-symlink guard.
        br = self._load_tool()
        with self.assertRaises(br.Incomplete):
            br._write_text_safe(str(self.repo / "a" / ".." / "b.md"), "x\n", str(self.repo))

    def test_slug_rejects_dot_and_dotdot_components(self):
        # `.`/`..` are the only allowed-charset values that change which path
        # `repos/{slug}/pulls` resolves to.
        br = self._load_tool()
        for bad in ("../x", "a/.", "a/..", "../.."):
            self.assertFalse(br._valid_slug(bad), bad)
        self.assertTrue(br._valid_slug("owner/repo"))

    def test_an_empty_gh_body_aborts_instead_of_dropping_every_veto(self):
        # An exit-0 EMPTY body is not "zero PRs" — a valid `--paginate --slurp`
        # payload is never empty. Treating it as such drops every open-PR and
        # open-base veto and lets ancestry delete an open-PR branch at exit 0.
        _git(self.repo, "branch", "victim", self.main_sha)
        (self.repo / "g.txt").write_text("g\n")
        _git(self.repo, "add", "g.txt")
        _git(self.repo, "commit", "-m", "advance")
        _git(self.repo, "update-ref", "refs/remotes/origin/main",
             _git_out(self.repo, "rev-parse", "HEAD"))
        empty = self.gh_dir / "gh-empty"
        empty.write_text("#!/bin/sh\nexit 0\n")
        empty.chmod(0o755)
        self.write_fixtures()
        rc, out, err = self.run_tool(["--apply", "--no-backup"], repo=self.driver,
                                     env_extra={"BRANCH_REAPER_GH": str(empty)})
        self.assertEqual(rc, 2, err + out)
        self.assertIn("victim", self.branches())

    def test_the_bundle_path_rejects_dotdot_too(self):
        # Both write paths must refuse `..`, not just the report path.
        br = self._load_tool()
        with self.assertRaises(br.Incomplete):
            br._make_backup_bundle(
                str(self.repo), str(self.repo / "a" / ".." / "b.bundle"), [])

    def test_a_bare_empty_page_list_aborts(self):
        # A valid `--paginate --slurp` payload always has at least one page, so a
        # bare `[]` is malformed — reading it as "zero PRs" drops every veto.
        _git(self.repo, "branch", "victim", self.main_sha)
        (self.repo / "g.txt").write_text("g\n")
        _git(self.repo, "add", "g.txt")
        _git(self.repo, "commit", "-m", "advance")
        _git(self.repo, "update-ref", "refs/remotes/origin/main",
             _git_out(self.repo, "rev-parse", "HEAD"))
        self.write_fixtures(open_pages=[])
        rc, out, err = self.run_tool(["--apply", "--no-backup"], repo=self.driver)
        self.assertEqual(rc, 2, err + out)
        self.assertIn("victim", self.branches())

    def test_no_backup_conflicts_with_an_explicit_bundle_path(self):
        # `--no-backup` means "write no bundle"; a contradictory explicit path is
        # a usage error. The check must fire BEFORE any write: the recovery write
        # precedes the delete phase by design, so a late check returned 3 having
        # already overwritten a previous run's recovery record — its only durable
        # record when that run used `--no-backup`.
        sha = self.commit_on("merged/b", "work")
        self.add_pr("merged", "merged/b", sha)
        self.write_fixtures()
        report = self.driver / "r.md"
        report.write_text("ORIGINAL")
        # With `--report`, the recovery record is written to `<report>.recovery.json`,
        # so the sentinel must be planted THERE — on the other path the assertion
        # would be vacuous.
        rec = self.driver / "r.md.recovery.json"
        rec.write_text('{"sentinel": true}')
        dest = self.tmp / "b.bundle"
        rc, out, err = self.run_tool(
            ["--apply", "--no-backup", "--backup-bundle", str(dest),
             "--report", str(report)], repo=self.driver)
        self.assertEqual(rc, 3, err + out)
        self.assertFalse(dest.exists())
        self.assertIn("merged/b", self.branches())
        self.assertIn("sentinel", rec.read_text())   # not clobbered
        self.assertEqual(report.read_text(), "ORIGINAL")

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

        def partial_delete(repo_root, rows, *, backup_bundle, results=None, **kwargs):
            real_delete(repo_root, [target], backup_bundle=None, results=results)
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

    def test_worktree_created_during_bundle_create_is_not_deleted(self):
        # P1 — the demonstrated case. `held` used to be read by the caller BEFORE
        # `_make_backup_bundle`, whose `git bundle create` has a 900 s timeout, so
        # a worktree created during that write was invisible to the delete; and
        # `git update-ref -d` does NOT refuse a checked-out branch, so the branch
        # was deleted out from under a live checkout, reported clean at exit 0.
        # The guard is now read INSIDE delete_branches, after the bundle.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        late_wt = self.tmp / "wt-during-bundle"
        bundle = self.tmp / "backup.bundle"
        real_run = br._run

        def run_with_late_worktree(cmd, **kwargs):
            if "bundle" in cmd and "create" in cmd and not late_wt.exists():
                _git(self.repo, "worktree", "add", str(late_wt), "merged/branch")
            return real_run(cmd, **kwargs)

        br._run = run_with_late_worktree
        try:
            with self._stub_env():
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply", "--backup-bundle", str(bundle)])
        finally:
            br._run = real_run
        self.assertEqual(rc, 0, rc)
        self.assertTrue(bundle.exists() and bundle.stat().st_size > 0)
        self.assertIn("merged/branch", self.branches())
        self.assertTrue(late_wt.exists())

    def test_held_at_bundle_time_tip_is_still_in_the_bundle(self):
        # The mirror of the test above. The bundle used to be built from
        # `targets` MINUS the branches held at that instant, while the delete
        # phase re-reads held branches AFTERWARDS. A branch held at bundle time
        # whose worktree is released during the (up to 900 s) `git bundle create`
        # was therefore deleted while ABSENT from the bundle — the one case the
        # bundle exists to prevent. All targets are now bundled; bundling a
        # branch that ends up preserved is harmless.
        br = self._load_tool()
        sha_a = self.commit_on("merged/a", "work a")
        sha_b = self.commit_on("merged/b", "work b")
        self.add_pr("merged", "merged/a", sha_a)
        self.add_pr("merged", "merged/b", sha_b)
        self.write_fixtures()
        held_wt = self.tmp / "wt-held-at-bundle-time"
        _git(self.repo, "worktree", "add", str(held_wt), "merged/b")
        bundle = self.tmp / "backup.bundle"
        real_run = br._run

        def run_releasing_worktree(cmd, **kwargs):
            if "bundle" in cmd and "create" in cmd and held_wt.exists():
                _git(self.repo, "worktree", "remove", "--force", str(held_wt))
            return real_run(cmd, **kwargs)

        br._run = run_releasing_worktree
        try:
            with self._stub_env():
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply", "--backup-bundle", str(bundle)])
        finally:
            br._run = real_run
        self.assertEqual(rc, 0, rc)
        self.assertTrue(bundle.exists() and bundle.stat().st_size > 0)
        # merged/b became unheld during the bundle write, so the delete phase
        # deleted it — and its tip must therefore be IN the bundle.
        self.assertNotIn("merged/b", self.branches())
        heads = _run(["git", "-C", str(self.repo), "bundle", "list-heads",
                      str(bundle)]).stdout
        self.assertIn(sha_b, heads)

    def test_worktree_created_in_the_delete_window_is_restored(self):
        # The residual the batch re-read cannot cover: a worktree created between
        # the guard read and its branch's `update-ref -d`. The post-delete
        # reconcile must restore the ref — a worktree created AFTER the delete
        # cannot name a deleted branch, so one re-read after the loop is complete.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.write_fixtures()
        late_wt = self.tmp / "wt-during-delete"
        real_run = br._run

        def run_creating_worktree(cmd, **kwargs):
            if ("update-ref" in cmd and "-d" in cmd and cmd[-1] == sha
                    and not late_wt.exists()):
                _git(self.repo, "worktree", "add", str(late_wt), "merged/branch")
            return real_run(cmd, **kwargs)

        rows = [{"branch": "merged/branch", "oid": sha, "ts": 0,
                 "verdict": "SAFE", "reason": "pr-merged-tip"}]
        br._run = run_creating_worktree
        try:
            results = br.delete_branches(str(self.repo), rows, backup_bundle=None)
        finally:
            br._run = real_run
        self.assertEqual(results[0]["result"], "restored", results)
        self.assertIn("merged/branch", self.branches())
        self.assertEqual(_git_out(self.repo, "rev-parse", "refs/heads/merged/branch"), sha)
        self.assertTrue(late_wt.exists())

    def test_aborted_first_delete_returns_exit_6_not_2(self):
        # P2 — a timed-out `update-ref -d` is recorded `aborted` and re-raised.
        # When that is the FIRST target, deciding on `deleted` alone returned exit
        # 2 ("Nothing was deleted") even though the killed update-ref may already
        # have landed. `aborted` is deletion-or-unknown and must return exit 6.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        real_run = br._run

        def timeout_on_delete(cmd, **kwargs):
            if "update-ref" in cmd and "-d" in cmd:
                raise br.Incomplete("simulated 120 s timeout on update-ref -d")
            return real_run(cmd, **kwargs)

        br._run = timeout_on_delete
        try:
            with self._stub_env(), contextlib.redirect_stderr(io.StringIO()) as err:
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply"])
        finally:
            br._run = real_run
        self.assertEqual(rc, br.EXIT_INCOMPLETE_AFTER_DELETE, err.getvalue())
        self.assertIn("AFTER DELETION", err.getvalue())
        self.assertIn("merged/branch", self.branches())

    def test_toplevel_handler_applies_the_after_deletion_distinction(self):
        # The BOUNDARY's own distinction, not the two inner handlers'. An
        # exception raised AFTER both of them have returned — the post-delete
        # output block, e.g. a broken pipe on `--apply --json | head` — reaches
        # only `main`, which must still say 6 and not 2 ("Nothing was deleted")
        # over a real deletion.
        br = self._load_tool()
        real_run = br._run_main

        def boom(_args):
            # Set DURING the run: `main` resets `_LANDED` at its top now, so the
            # flag has to be raised by the run itself, as a real deletion does.
            br._LANDED = True
            raise OSError(32, "Broken pipe")

        br._run_main = boom
        try:
            rc = br.main(["--repo", str(self.repo), "--slug", "owner/repo"])
        finally:
            br._run_main = real_run
            br._LANDED = False
        self.assertEqual(rc, 6, rc)

    def test_post_delete_build_report_oserror_returns_exit_6(self):
        # `--apply` with NO `--report`. `build_report` used to sit BETWEEN the two
        # after-deletion handlers, so an OSError from it following a real deletion
        # reached only the top-level catch and returned exit 2 — whose documented
        # meaning, "Nothing was deleted", is false at that point. It must be 6.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        real_build = br.build_report

        def boom(*_a, **_k):
            raise OSError(84, "Value too large to be stored in data type")

        br.build_report = boom
        try:
            with self._stub_env():
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply"])
        finally:
            br.build_report = real_build
        self.assertEqual(rc, 6, rc)
        self.assertNotIn("merged/branch", self.branches())

    def test_post_delete_report_write_oserror_returns_exit_6(self):
        # The OSError arm of the same POST-delete window as the symlink test
        # below, not the symlink guard. Before the boundary catch, an unwritable
        # surface here exited 1 with a traceback and dropped the
        # after-deletion signal entirely — for a tool that has just deleted
        # branches, that is the signal that must not be lost.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        report_dir = self.tmp / "rpt-dir"
        report_dir.mkdir()
        report = report_dir / "r.md"
        real_delete = br.delete_branches

        def delete_then_lock(repo_root, rows, *, backup_bundle, results=None, **kwargs):
            out = real_delete(repo_root, rows, backup_bundle=backup_bundle,
                              results=results)
            os.chmod(report_dir, 0o555)
            return out

        br.delete_branches = delete_then_lock
        try:
            with self._stub_env():
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply", "--report", str(report)])
        finally:
            br.delete_branches = real_delete
            os.chmod(report_dir, 0o755)
        self.assertEqual(rc, 6, rc)
        self.assertNotIn("merged/branch", self.branches())

    def test_post_delete_report_write_failure_returns_exit_6(self):
        # The POST-delete report-write handler, distinct from the mid-loop one:
        # the pre-delete write succeeded, a real deletion landed, and only THEN
        # does the report path become a symlink. Without the exit-6 decision here
        # the run would report the false "nothing was deleted" exit 2.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        report = self.tmp / "r.md"
        victim = self.tmp / "victim.md"
        victim.write_text("do not clobber\n")
        real_delete = br.delete_branches

        def delete_then_symlink(repo_root, rows, *, backup_bundle, results=None, **kwargs):
            out = real_delete(repo_root, rows, backup_bundle=backup_bundle, results=results)
            if report.exists() and not report.is_symlink():
                os.unlink(report)
                os.symlink(victim, report)
            return out

        br.delete_branches = delete_then_symlink
        try:
            with self._stub_env(), contextlib.redirect_stderr(io.StringIO()):
                rc = br.main(["--repo", str(self.driver), "--slug", "owner/repo",
                              "--apply", "--report", str(report)])
        finally:
            br.delete_branches = real_delete
        self.assertEqual(rc, br.EXIT_INCOMPLETE_AFTER_DELETE, rc)
        self.assertEqual(victim.read_text(), "do not clobber\n")
        self.assertNotIn("merged/branch", self.branches())

    def test_aborted_delete_that_landed_while_held_is_restored_on_the_abort_path(self):
        # The kill-after-landing case: the update-ref deleted the ref and THEN the
        # process was killed; the post-kill probe was unavailable, so the row stayed
        # `aborted` (deletion-or-unknown) while a worktree held the branch. The
        # abort-path reconcile must probe and restore it, never leave the checkout
        # on a missing ref.
        br = self._load_tool()
        sha = self.commit_on("merged/branch", "merged work")
        self.write_fixtures()
        wt = self.tmp / "wt-held-aborted"
        real_run = br._run
        state = {"probes": 0}

        def land_then_timeout(cmd, **kwargs):
            if "update-ref" in cmd and "-d" in cmd and cmd[-1] == sha:
                _git(self.repo, "worktree", "add", str(wt), "merged/branch")
                real_run(cmd, **kwargs)  # the ref transaction COMMITS first
                raise br.Incomplete("killed after the ref transaction committed")
            if "rev-parse" in cmd and "--verify" in cmd:
                state["probes"] += 1
                if state["probes"] == 1:
                    raise br.Incomplete("probe unavailable")
            return real_run(cmd, **kwargs)

        rows = [{"branch": "merged/branch", "oid": sha, "ts": 0,
                 "verdict": "SAFE", "reason": "pr-merged-tip"}]
        br._run = land_then_timeout
        try:
            with self.assertRaises(br.Incomplete):
                br.delete_branches(str(self.repo), rows, backup_bundle=None)
        finally:
            br._run = real_run
        self.assertIn("merged/branch", self.branches())
        self.assertEqual(_git_out(self.repo, "rev-parse", "refs/heads/merged/branch"), sha)

    def test_late_worktree_dirty_is_refreshed_for_the_report(self):
        # P3 — the re-read refreshed `worktree` but left `dirty` (and the detached
        # age map) from the stale first snapshot, so a genuinely dirty late
        # worktree could be reported dirty=no.
        sha = self.commit_on("merged/branch", "merged work")
        self.add_pr("merged", "merged/branch", sha)
        self.write_fixtures()
        late_wt = self.tmp / "wt-late-dirty"
        late_stub = _write_exec(self.gh_dir / "gh-late-dirty", f"""#!/usr/bin/env bash
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
  echo dirty > {shlex.quote(str(late_wt))}/untracked.txt
fi
cat "$d/${{state}}_pages.json"
""")
        rc, out, err = self.run_tool(["--json", "--apply"], repo=self.driver,
                                     env_extra={"BRANCH_REAPER_GH": str(late_stub)})
        self.assertEqual(rc, 0, err + out)
        row = next(r for r in json.loads(out)["rows"] if r["branch"] == "merged/branch")
        self.assertTrue(row["worktree"], "the late worktree must be reported")
        self.assertEqual(os.path.realpath(row["worktree"]), os.path.realpath(str(late_wt)))
        self.assertTrue(row["dirty"],
                        "dirty must come from the re-read, not the stale snapshot")
        self.assertIn("merged/branch", self.branches())

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
