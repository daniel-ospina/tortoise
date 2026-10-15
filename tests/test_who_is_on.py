"""Hermetic tests for tools/who_is_on.py — the "who is on #N?" verb (#4256).

No network, no Docker, no FalkorDB. The GitHub surfaces are driven by a stub
`gh` binary reading JSON fixtures from $GH_STUB_DIR; the LOCAL surfaces (the
ones this tool exists for) use a real temp git repo with real branches and a
real `git worktree add`. Git is isolated from the host (`GIT_CONFIG_GLOBAL` /
`GIT_CONFIG_SYSTEM` -> /dev/null, HOME -> the temp dir) so a global
`commit.gpgsign` or `init.defaultBranch` cannot change the result.

The load-bearing property: the SAME input that makes the dispatch gate
(`collision_preflight.py`) exit 1 must make this QUESTION exit 0 — a holder is
information, not a collision. The local-only branch is the surface a remote-only
`gh` check cannot see, so it is asserted directly. The other load-bearing
property is fail-closed: an unreadable uncommitted state is NEVER reported as
clean, and so is never dropped from `--dirty-only`.

Run under pytest:  python3 -m pytest tests/test_who_is_on.py -q
Run standalone:    python3 tests/test_who_is_on.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "who_is_on.py"
WRAPPER = ROOT / "tools" / "who-is-on.sh"
GATE = ROOT / "tools" / "collision_preflight.py"
PYTHON = sys.executable
ISSUE = 4242
#: The slug the fixture's git remote resolves to; the board fixture stamps it.
SLUG = "acme/example"

_GATE = None


def _gate_module():
    """The pre-flight module, imported once — for the SHARED constants the parity
    assertions pin (`WEAK_HIT_KINDS`). Importing it appends the repo root to
    `sys.path` and imports `fleet_state`, which is why it is loaded once here and
    not per test."""
    global _GATE
    if _GATE is None:
        sys.path.insert(0, str(ROOT / "tools"))
        import collision_preflight as _cp
        _GATE = _cp
    return _GATE

# Minimal gh stub: the calls the pre-flight's GitHub surfaces make.
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
  "pr list") f="$d/${state:-open}_prs.json" ;;
  "api --paginate")
    # #8560: the landed surface's TARGETED, PAGINATED timeline query
    # (`gh api --paginate --slurp repos/<slug>/issues/<N>/timeline?...`). It is
    # served from its OWN fixture — a slurped ARRAY OF PAGES — so it cannot be
    # confused with the ADVISORY closed-PR sample (the `api -i` request below).
    # `GH_STUB_TIMELINE_FAIL=1` makes this the UNQUERYABLE case: the landed
    # surface is BLOCKING, so the verb must answer INCOMPLETE naming it, never
    # "nobody".
    if [ "${GH_STUB_TIMELINE_FAIL:-0}" = "1" ]; then
      echo "gh-stub: read tcp 127.0.0.1:1->20.26.156.210:443: read: connection reset by peer" >&2
      exit 1
    fi
    f="$d/timeline.json" ;;
  # #5251: the closed-PR surface is ONE bounded request with `-i`, whose own
  # `Link` header supplies the total. `-i` prints the status line and headers,
  # then a blank line, then the body — and the pre-flight REQUIRES that
  # separator (it fails with "produced no header/body separator" without it),
  # so the stub must emit it. No `Link` header is sent, which is the
  # "no further pages exist" case.
  "api -i") f="$d/closed_prs.json"; hdr=1 ;;
  "issue view") f="$d/issue.json" ;;
  *) echo "gh-stub: unexpected argv: $*" >&2; exit 64 ;;
esac
if [ ! -f "$f" ]; then echo "gh-stub: no fixture: $f" >&2; exit 1; fi
# `-i` prints the status line, then headers, then a BLANK line, then the body.
# GH_STUB_LINK=1 also sends a Link header — that is how the pre-flight learns a
# page was truncated (it cannot see >100 closed PRs in one request), which is
# the ADVISORY-partiality case production ALWAYS hits on a repo this size while
# a fixture with no Link never does.
if [ "${hdr:-}" = "1" ]; then
  printf 'HTTP/2.0 200 OK\n'
  if [ "${GH_STUB_LINK:-}" = "1" ]; then
    printf 'Link: <https://api.github.invalid/repos/acme/example/pulls?page=2>; rel="next", <https://api.github.invalid/repos/acme/example/pulls?page=99>; rel="last"\n'
  fi
  printf '\n'
fi
cat "$f"
"""


def _git_env(tmp: Path, gh_dir: Path | None = None) -> dict:
    """Host-independent env for child processes: no COLLISION_PREFLIGHT_* leak
    (a bad inherited value would change every invocation), no global git config
    (a global commit.gpgsign/init.defaultBranch must not affect the fixture),
    HOME inside the temp dir."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("COLLISION_PREFLIGHT_")}
    if gh_dir is not None:
        env["GH_STUB_DIR"] = str(gh_dir)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["HOME"] = str(tmp)
    return env


class WhoIsOnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="who-is-on-")
        self.tmp = Path(self._tmp.name)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self._git(self.repo, "init", "-q", "-b", "main", "--template=")
        self._git(self.repo, "config", "user.email", "test@example.com")
        self._git(self.repo, "config", "user.name", "Test")
        # An offline origin URL lets the tool resolve a slug with no network.
        self._git(self.repo, "remote", "add", "origin", "https://github.com/acme/example.git")
        (self.repo / "seed.txt").write_text("seed\n")
        self._git(self.repo, "add", "seed.txt")
        self._git(self.repo, "commit", "-qm", "seed")
        # A remote-tracking base ref, so ahead counts have something to measure.
        self._git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")

        self.gh_dir = self.tmp / "ghstub"
        self.gh_dir.mkdir()
        self.gh = self._write_exec(self.gh_dir / "gh", GH_STUB)
        (self.gh_dir / "open_prs.json").write_text("[]")
        (self.gh_dir / "closed_prs.json").write_text("[]")
        # #8560: one empty timeline page — what `gh api --paginate --slurp`
        # returns for an issue with no events, so the landed surface reads
        # CLEAN by default. A test that wants landed evidence overwrites it.
        (self.gh_dir / "timeline.json").write_text("[[]]")
        (self.gh_dir / "issue.json").write_text(json.dumps({
            "number": ISSUE,
            "title": "florfenicol dosing audit",
            "state": "OPEN",
            "stateReason": None,
            "url": f"https://example.invalid/issues/{ISSUE}",
            "assignees": [],
            "comments": [],
        }))
        # ⛔ THE CLAIM GATE READS A FLEET BOARD, AND `HOME` IS THIS TEMP DIR.
        # Without a fresh, answerable board (`$HOME/.pi/agent/state/
        # fleet-state.json`) every run REBUILDS — and this fixture has no lane
        # registry to read, so the gate would answer INCOMPLETE and the whole
        # "nobody holds it" family of answers would be unreachable. The default
        # board here answers "no lane is attributed to this issue".
        self._write_fleet_board()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _write_fleet_board(self, issues=None, lanes=None, repo=SLUG) -> Path:
        """Write the claim gate's board at `$HOME/.pi/agent/state`.

        Only the fields `collision_preflight._read_board` / `_holders_from_board`
        consume: `repo` (the stamp `_board_is_for_repo` compares against the
        queried slug — a mismatch forces a rebuild), `reconciliation` (both reads
        measured), `index.issues` (attribution) and `lanes` (liveness).
        `generated_at` is NOW, so the board is fresh inside
        `fleet_state.STATE_FRESH_S` and no run pays a REBUILD.
        """
        path = self.tmp / ".pi" / "agent" / "state" / "fleet-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "generated_at": datetime.now(UTC).isoformat(),
            "repo": repo,
            # `orphans` is REQUIRED by `_board_can_answer` (an absent orphan report
            # would leave the board's liveness provenance unknown): `build_state`
            # always writes it, and an empty list is what a readable live-session
            # store produces.
            "orphans": [],
            "reconciliation": {"registry_measured": True,
                               "github_measured": True,
                               # The board must also stamp its OPEN-ISSUE coverage:
                               # `_board_can_answer` refuses an unstamped board
                               # (a pre-#8175 cache cannot prove it saw every
                               # open issue) and this fixture's boards are meant
                               # to be SERVED, not rebuilt.
                               "open_issues_truncated": False,
                               "open_issues_read": 1,
                               "open_issues_limit": 2000},
            "index": {"issues": {
                str(number): {
                    "lanes": [label for label, _ in entries],
                    "evidence": {label: evidence for label, evidence in entries},
                }
                for number, entries in (issues or {}).items()
            }},
            "lanes": [
                {
                    "identity": {"lane": spec["label"]},
                    "terminal": ({"state": spec["terminal"],
                                  "reason": spec.get("reason", "")}
                                 if spec.get("terminal") else {}),
                    "liveness": {
                        "liveness_measured": spec.get("live") is not None},
                    # A measured lane is a RESOLVED session (`bound` True with a
                    # sid); a lane with no record is the real sessionless shape
                    # (`pid_alive: False` by default, `bound: False`, `sid: None`),
                    # matching `fleet_state.build_state`.
                    "session": ({"pi_pid": 1, "pid_alive": bool(spec["live"]),
                                 "sid": f"sid-{spec['label']}", "bound": True}
                                if spec.get("live") is not None else
                                {"pi_pid": None, "pid_alive": False,
                                 "sid": None, "bound": False}),
                }
                for spec in (lanes or ())
            ],
        }))
        return path

    def _env(self) -> dict:
        """Host-independent child env: no COLLISION_PREFLIGHT_* leak, no global
        git config, HOME inside the temp dir."""
        return _git_env(self.tmp, self.gh_dir)

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                       text=True, env=_git_env(self.tmp))

    @staticmethod
    def _write_exec(path: Path, body: str) -> Path:
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    # ── fixtures ─────────────────────────────────────────────────────────────

    def local_branch(self, name: str, ahead: int = 1) -> None:
        """A local-only branch (no remote-tracking counterpart), `ahead` commits
        past main."""
        self._git(self.repo, "checkout", "-q", "-b", name, "main")
        for i in range(ahead):
            (self.repo / f"{name.replace('/', '-')}-{i}.txt").write_text("wip\n")
            self._git(self.repo, "add", "-A")
            self._git(self.repo, "commit", "-qm", f"wip {i} on {name}")
        self._git(self.repo, "checkout", "-q", "main")

    def remote_branch(self, name: str) -> None:
        """A branch whose remote-tracking ref exists — NOT local-only."""
        self.local_branch(name, ahead=0)
        self._git(self.repo, "update-ref", f"refs/remotes/origin/{name}", "HEAD")

    def add_nested_remote(self, name: str, url: str) -> None:
        """Register a remote by writing git config directly — NOT `git remote add`.

        The ambiguity these two tests need is a remote named `foo/bar` ALONGSIDE
        `foo`, and `git remote add foo/bar` exits 128 on the CI runner's git
        while succeeding on git 2.50.1 locally, so the fixture cannot depend on
        that name passing git's validation (the exact CI stderr is not surfaced
        by the short traceback, and the failure was not reproducible here).
        These config keys are precisely what `git remote` enumerates, with no
        name-validation step in the way — and `git remote` reading them IS the
        behaviour under test."""
        self._git(self.repo, "config", f"remote.{name}.url", url)
        self._git(self.repo, "config", f"remote.{name}.fetch",
                  f"+refs/heads/*:refs/remotes/{name}/*")

    def add_worktree(self, dirname: str, branch: str) -> Path:
        path = self.tmp / "wt" / dirname
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-q", str(path), branch],
                       cwd=self.repo, check=True, capture_output=True, text=True,
                       env=self._env())
        return path

    def add_detached_worktree(self, dirname: str) -> Path:
        path = self.tmp / "wt" / dirname
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(path), "HEAD"],
                       cwd=self.repo, check=True, capture_output=True, text=True,
                       env=self._env())
        return path

    # ── runners ──────────────────────────────────────────────────────────────

    def run_tool(self, *args, tool: Path = TOOL, gh: Path | None = None,
                 git_bin: Path | None = None, env_extra: dict | None = None):
        cmd = [PYTHON, str(tool), *[str(a) for a in args],
               "--repo", str(self.repo), "--gh", str(gh or self.gh)]
        if git_bin is not None:
            cmd += ["--git", str(git_bin)]
        env = self._env()
        if env_extra:
            env.update(env_extra)
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                              timeout=120)
        return proc.returncode, proc.stdout + proc.stderr

    def failing_worktree_git(self) -> Path:
        """A git shim that fails only `worktree` — exercises the map-failure path."""
        real = shutil.which("git")
        stub = self.tmp / "gitshim" / "git"
        stub.parent.mkdir(parents=True, exist_ok=True)
        return self._write_exec(stub, f'''#!/usr/bin/env bash
if [ "${{1:-}}" = "worktree" ]; then echo "git-shim: refusing worktree" >&2; exit 1; fi
exec {real} "$@"
''')

    @staticmethod
    def _branch_refs(out: str) -> set[str]:
        return set(re.findall(r"refs/heads/[^\s\]\[]+", out))

    # ── the load-bearing divergence ──────────────────────────────────────────

    def test_local_only_branch_is_reported_and_question_exits_zero(self):
        self.local_branch(f"fix/{ISSUE}-strand")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn(f"fix/{ISSUE}-strand", out)
        self.assertIn("LOCAL", out)
        self.assertIn("local-only branches and worktrees WERE enumerated", out)

    def test_question_exits_zero_where_the_gate_exits_one(self):
        """Same input, two verbs: the gate says COLLISION (1); the question,
        asked as a question, answers (0)."""
        self.local_branch(f"fix/{ISSUE}-strand")
        rc_who, out_who = self.run_tool(ISSUE)
        rc_gate, out_gate = self.run_tool(ISSUE, tool=GATE)
        self.assertEqual(rc_gate, 1, out_gate)   # gate: COLLISION
        self.assertEqual(rc_who, 0, out_who)     # question: answered
        self.assertIn("LOCAL", out_who)

    def test_holder_set_agrees_with_the_gate(self):
        """Parity: both tools derive the SAME local-branch holder set from the
        same fixture — the reason the tool reuses the gate's enumeration."""
        self.local_branch(f"fix/{ISSUE}-strand")
        self.local_branch(f"fix/{ISSUE}-other")
        self.remote_branch(f"fix/{ISSUE}-pushed")
        _rc_who, out_who = self.run_tool(ISSUE)
        _rc_gate, out_gate = self.run_tool(ISSUE, tool=GATE)
        self.assertEqual(self._branch_refs(out_who), self._branch_refs(out_gate),
                         f"holder sets differ:\nwho={out_who}\ngate={out_gate}")

    def test_worktree_uncommitted_state_is_shown_for_local_holder(self):
        self.local_branch(f"fix/{ISSUE}-health")
        wt = self.add_worktree("health", f"fix/{ISSUE}-health")
        (wt / "hosted_api.py").write_text("uncommitted wip\n")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("uncommitted file(s)", out)
        self.assertIn(str(wt), out)

    def test_tag_shadowing_a_branch_name_does_not_hide_it(self):
        """A tag whose name equals a branch makes `%(refname:short)` emit
        `heads/<name>`; keying on the full ref keeps the branch visible AND its
        metadata (ahead/age) resolved. The `?` assertion is what actually guards
        it — short keying left the branch DIRTY but its AHEAD/AGE unresolved."""
        self.local_branch(f"fix/{ISSUE}-strand", ahead=2)
        wt = self.add_worktree("strand", f"fix/{ISSUE}-strand")
        (wt / "wip.py").write_text("uncommitted\n")
        self._git(self.repo, "tag", f"fix/{ISSUE}-strand")
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        row = next((ln for ln in out.splitlines() if f"fix/{ISSUE}-strand" in ln), "")
        self.assertIn("DIRTY", row, out)
        self.assertNotIn("heads/", row, out)
        self.assertNotIn("?", row, f"metadata did not resolve under a shadowing tag: {row}")

    def test_detached_worktree_with_uncommitted_work_is_counted(self):
        """`--detached` must not be a no-op: `_worktree_blocks` emits the truthy
        literal `(detached)`, so a detached worktree was skipped and reported as
        `0 uncommitted`."""
        wt = self.add_detached_worktree("detached-only-copy")
        (wt / "only-copy.txt").write_text("the only copy\n")
        rc, out = self.run_tool("--inventory", "--detached")
        self.assertEqual(rc, 0, out)
        self.assertIn("detached worktrees: 1 dirty", out)

    def test_detached_worktree_not_counted_without_the_flag(self):
        wt = self.add_detached_worktree("detached-only-copy")
        (wt / "only-copy.txt").write_text("the only copy\n")
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("detached worktrees", out)

    def test_no_holders_answers_nobody_and_exits_zero(self):
        rc, out = self.run_tool(9999)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)
        self.assertIn("Local-only branches and worktrees WERE enumerated", out)

    # ── fail-closed: could-not-check is never "nobody" ───────────────────────

    def test_unqueryable_gh_is_exit_two_and_never_nobody(self):
        self.local_branch(f"fix/{ISSUE}-strand")
        rc, out = self.run_tool(ISSUE, gh=Path("/nonexistent-gh-binary"))
        self.assertEqual(rc, 2, out)
        self.assertIn("ANSWER INCOMPLETE", out)
        self.assertNotIn("ANSWER: nobody", out)
        # The local holder is still reported even though gh is down.
        self.assertIn(f"fix/{ISSUE}-strand", out)

    def test_non_finite_timeout_is_usage_not_collision(self):
        """`--timeout nan` must be EXIT_USAGE (3). It must NEVER be 1 — the one
        code this tool promises not to return."""
        for bad in ("nan", "inf"):
            rc, out = self.run_tool("--inventory", "--timeout", bad)
            self.assertEqual(rc, 3, f"--timeout {bad} -> {rc}: {out}")

    # ── the inventory ────────────────────────────────────────────────────────

    def test_inventory_lists_local_only_branch_with_uncommitted_work(self):
        self.local_branch("fix/2924-health")
        wt = self.add_worktree("2924", "fix/2924-health")
        (wt / "hosted_api.py").write_text("+60/-19 wip\n")
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        self.assertIn("fix/2924-health", out)
        self.assertIn("1 local-only branch(es); 1 carry uncommitted work", out)
        self.assertIn("DIRTY", out)

    def test_inventory_excludes_pushed_branch(self):
        self.remote_branch("pushed/thing")
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("pushed/thing", out)

    def test_inventory_dirty_only_excludes_clean_local_only_branch(self):
        self.local_branch("fix/clean-but-unpushed")
        rc_all, out_all = self.run_tool("--inventory")
        rc_dirty, out_dirty = self.run_tool("--inventory", "--dirty-only")
        self.assertEqual(rc_all, 0, out_all)
        self.assertEqual(rc_dirty, 0, out_dirty)
        self.assertIn("fix/clean-but-unpushed", out_all)       # listed by default
        self.assertNotIn("fix/clean-but-unpushed", out_dirty)  # excluded by --dirty-only

    def test_inventory_ahead_count_is_reported(self):
        self.local_branch("fix/two-ahead", ahead=2)
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        row = next(ln for ln in out.splitlines() if "fix/two-ahead" in ln)
        self.assertIn(" 2 ", f" {row} ")

    def test_unreadable_worktree_is_unknown_never_clean_and_not_dropped(self):
        """The fail-open guard: if `git status` fails, the branch is UNKNOWN —
        kept in `--dirty-only`, counted as unread, and the run is INCOMPLETE."""
        self.local_branch("fix/unreadable")
        wt = self.add_worktree("unreadable", "fix/unreadable")
        # Corrupt the worktree's gitdir pointer so `git status` fails there.
        (wt / ".git").write_text("not a gitdir\n")
        rc_all, out_all = self.run_tool("--inventory")
        self.assertIn("fix/unreadable", out_all)
        self.assertIn("UNKNOWN", out_all)
        self.assertIn("NOT known-clean", out_all)
        self.assertEqual(rc_all, 2, out_all)          # incomplete, not clean
        rc_dirty, out_dirty = self.run_tool("--inventory", "--dirty-only")
        self.assertIn("fix/unreadable", out_dirty)    # never dropped from the subset
        self.assertEqual(rc_dirty, 2, out_dirty)

    def test_inventory_rejects_an_issue_number(self):
        rc, out = self.run_tool("--inventory", ISSUE)
        self.assertEqual(rc, 3, out)

    def test_inventory_only_flags_are_usage_errors_in_question_mode(self):
        self.local_branch(f"fix/{ISSUE}-strand")
        for flag in ("--dirty-only", "--detached"):
            rc, out = self.run_tool(ISSUE, flag)
            self.assertEqual(rc, 3, f"{flag} in question mode -> {rc}: {out}")

    def test_nonpositive_issue_is_a_usage_error(self):
        # `number_present(ref, 0)` would match any standalone `0`, fabricating
        # holders; the gate rejects non-positive numbers and so must this tool.
        for bad in ("0", "-1"):
            rc, out = self.run_tool(bad)
            self.assertEqual(rc, 3, f"issue {bad} -> {rc}: {out}")

    def test_issue_title_is_sanitized(self):
        """The issue title is untrusted (GitHub) and the report is what a human
        reads — a raw CSI/OSC sequence must never reach stdout."""
        (self.gh_dir / "issue.json").write_text(json.dumps({
            "number": ISSUE,
            "title": "evil\x1b]52;c;payload\x07 title\x1b[2J",
            "state": "OPEN",
            "url": "https://example.invalid/x",
            "assignees": [],
            "comments": [],
        }))
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("payload", out)

    def test_no_worktree_row_is_not_labelled_clean(self):
        self.local_branch("fix/no-worktree-here")
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        row = next(ln for ln in out.splitlines() if "fix/no-worktree-here" in ln)
        self.assertNotIn("clean", row, row)   # never checked -> not "clean"
        self.assertIn("—", row, row)

    def test_hit_display_is_capped_like_the_gate(self):
        for i in range(25):
            self.local_branch(f"fix/{ISSUE}-bulk-{i:02d}", ahead=0)
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("more hit(s) on this surface", out)
        # the detail block must be capped too (it printed one line per holder)
        self.assertIn("more local holder(s)", out)

    def test_longest_remote_prefix_is_stripped(self):
        """With remotes `foo` and `foo/bar`, `refs/remotes/foo/bar/qux` must strip
        `foo/bar` (-> qux), not `foo` (-> bar/qux) — a shortest-first match hid a
        never-pushed branch named `bar/qux` from every surface."""
        self.add_nested_remote("foo", "https://github.com/acme/foo.git")
        self.add_nested_remote("foo/bar", "https://github.com/acme/bar.git")
        self._git(self.repo, "update-ref", "refs/remotes/foo/bar/qux", "HEAD")
        self.local_branch("bar/qux")  # never pushed; collides with a remote prefix
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        self.assertIn("bar/qux", out)

    def test_missing_worktree_is_flagged_and_kept_in_dirty_only(self):
        """A confirmed-absent worktree dir is MISSING (fully determined, not
        unknown), is never dropped from `--dirty-only`, and is counted in the
        truncation total."""
        self.local_branch("fix/gone")
        self.local_branch("fix/gone-too")
        wt = self.add_worktree("gone", "fix/gone")
        wt2 = self.add_worktree("gone-too", "fix/gone-too")
        shutil.rmtree(wt)   # the worktree registration remains; the dir is gone
        shutil.rmtree(wt2)
        rc, out = self.run_tool("--inventory", "--dirty-only")
        self.assertEqual(rc, 0, out)
        self.assertIn("fix/gone", out)
        self.assertIn("MISSING", out)
        # The at-risk total must include MISSING (it is not dropped silently).
        rc_limited, out_limited = self.run_tool("--inventory", "--dirty-only", "--limit", "1")
        self.assertEqual(rc_limited, 0, out_limited)
        self.assertIn("showing 1 of 2", out_limited)

    def test_worktree_list_failure_is_incomplete_never_clean(self):
        """If `git worktree list` itself fails, local holders cannot be judged —
        the answer is INCOMPLETE (rc 2), never a bare 'no worktree' at rc 0."""
        self.local_branch(f"fix/{ISSUE}-strand")
        self.add_worktree("strand", f"fix/{ISSUE}-strand")
        rc, out = self.run_tool(ISSUE, git_bin=self.failing_worktree_git())
        self.assertEqual(rc, 2, out)
        self.assertIn("could not read the worktree list", out)
        self.assertNotIn("ANSWER: nobody", out)

    def test_non_utf8_refname_never_exits_one(self):
        """A non-UTF-8 refname makes the pre-flight's `subprocess.run(text=True)`
        raise UnicodeDecodeError (a ValueError, not caught by the gate's `_run`).
        It must surface as an INCOMPLETE answer (rc 2), never a traceback/rc 1."""
        self.local_branch(f"fix/{ISSUE}-strand")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                             capture_output=True, text=True, env=self._env()).stdout.strip()
        with open(self.repo / ".git" / "packed-refs", "ab") as fh:
            fh.write(b"# pack-refs with: peeled fully-peeled sorted \n")
            fh.write(sha.encode() + b" refs/heads/bad\xffname\n")
        rc, out = self.run_tool(ISSUE)
        self.assertNotEqual(rc, 1, out)
        self.assertEqual(rc, 2, out)

    def test_question_mode_unreadable_holder_state_is_incomplete(self):
        """A holder whose worktree state could not be read is NOT a complete
        answer — question mode must exit 2, never 0 (and never 1)."""
        self.local_branch(f"fix/{ISSUE}-strand")
        wt = self.add_worktree("strand", f"fix/{ISSUE}-strand")
        (wt / ".git").write_text("not a gitdir\n")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 2, out)
        self.assertIn("UNREADABLE LOCAL STATE", out)
        self.assertNotIn("ANSWER: nobody", out)

    # ── the wrapper is the documented verb ───────────────────────────────────

    def test_inventory_unexpected_failure_is_incomplete_never_exit_one(self):
        """The inventory branch must keep the same promise the question branch
        keeps. A non-UTF-8 byte from git makes the gate's
        `subprocess.run(text=True)` raise UnicodeDecodeError (a ValueError, which
        `_run` does not catch); escaping `main` exits 1 — the COLLISION code — so
        a caller would read a crash as "a holder". It must be rc 2.

        The shim answers `git remote -v` — the one call the gate's `_remote_slug`
        makes through ITS `_run`, which catches only OSError — with an
        undecodable byte, and delegates everything else. That call sits in
        `_resolve_inventory_path`, which had no guard at all."""
        real = shutil.which("git")
        stub = self.tmp / "gitbad" / "git"
        stub.parent.mkdir(parents=True, exist_ok=True)
        self._write_exec(stub, f'''#!/usr/bin/env bash
if [ "${{1:-}}" = "remote" ] && [ "${{2:-}}" = "-v" ]; then
  printf 'origin\\thttps://github.com/acme/\\xffrepo.git (fetch)\\n'
  printf 'origin\\thttps://github.com/acme/\\xffrepo.git (push)\\n'
  exit 0
fi
exec {real} "$@"
''')
        rc, out = self.run_tool("--inventory", git_bin=stub)
        self.assertNotEqual(rc, 1, out)
        self.assertEqual(rc, 2, out)

    def test_advisory_partiality_does_not_make_the_answer_incomplete(self):
        """#5251: the bounded closed-PR sample is ADVISORY — the gate is
        explicit that "its partiality does NOT make the run INCOMPLETE". On any
        repo with more closed PRs than one page it is PERMANENTLY partial, so
        counting it as INCOMPLETE would make rc 0 — the complete answer —
        unreachable in this tool's primary use case, and would print "could not
        be queried" for a surface that answered."""
        self.local_branch(f"fix/{ISSUE}-strand")
        env = self._env()
        env["GH_STUB_LINK"] = "1"
        proc = subprocess.run(
            [PYTHON, str(TOOL), "9999", "--repo", str(self.repo),
             "--gh", str(self.gh)], capture_output=True, text=True, env=env,
            timeout=120,
        )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("ANSWER: nobody", out)
        self.assertNotIn("INCOMPLETE SURFACES", out)
        # The control: if the Link header had not been parsed the surface would
        # not be partial and this note would be absent, so this assertion is
        # what proves the partial case was actually exercised.
        self.assertIn("ADVISORY", out)

    def test_git_status_warning_that_omits_a_subtree_is_unknown_not_clean(self):
        """`git status` exits 0 while warning it could not open a directory, and
        then OMITS that subtree's uncommitted files. Treating rc 0 as CLEAN
        reports a directory it could not read as clean — the same fail-open this
        tool exists to close, one level down."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("who_is_on_under_test", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        warn = self._write_exec(self.tmp / "git-warn", '''#!/usr/bin/env bash
printf '%s\\n' "warning: could not open directory 'sub/': Permission denied" >&2
exit 0
''')
        self.assertEqual(mod.worktree_state(str(warn), str(self.repo))[0], mod.DS_UNKNOWN)
        # Control: the same shim WITHOUT the warning is CLEAN, so the assertion
        # above is not satisfied by every possible shim.
        quiet = self._write_exec(self.tmp / "git-quiet", "#!/usr/bin/env bash\nexit 0\n")
        self.assertEqual(mod.worktree_state(str(quiet), str(self.repo))[0], mod.DS_CLEAN)

    def test_an_unenumerated_local_surface_is_not_printed_as_none(self):
        """A local surface that could not be read must not render as `none`:
        that asserts absence for a read that never happened, and the LOCAL block
        is the section this tool exists for, so a reader who skims it would take
        "nobody" from it."""
        # The board must be stamped for the SAME slug, or the claim gate rebuilds
        # it with live gh/cmux — a real 12 s rebuild inside a unit test.
        self._write_fleet_board(repo="acme/other")
        proc = subprocess.run(
            [PYTHON, str(TOOL), str(ISSUE), "--repo", "acme/other",
             "--gh", str(self.gh)], capture_output=True, text=True,
            env=self._env(), timeout=120,
        )
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 2, out)
        self.assertIn("no-local-clone", out)
        self.assertNotIn("[local branches] none", out)
        self.assertNotIn("[local worktrees] none", out)

    def test_an_ambiguous_remote_prefix_never_hides_a_local_branch(self):
        """With remotes `foo` and `foo/bar`, `refs/remotes/foo/bar/qux` is either
        remote `foo`'s branch `bar/qux` or remote `foo/bar`'s branch `qux`, and
        `git remote` alone cannot say which. Crediting the longest prefix
        declared a never-pushed local branch named `qux` PUSHED and hid it from
        every surface — stranded work reported FREE."""
        self.add_nested_remote("foo", "https://github.com/acme/foo.git")
        self.add_nested_remote("foo/bar", "https://github.com/acme/bar.git")
        self._git(self.repo, "update-ref", "refs/remotes/foo/bar/qux", "HEAD")
        self.local_branch("qux")          # never pushed; the ambiguous ref is not it
        rc, out = self.run_tool("--inventory")
        self.assertEqual(rc, 0, out)
        self.assertIn("1 local-only branch", out)
        self.assertIn("qux", out)

    def test_asking_about_a_pr_number_is_a_designed_refusal_not_a_crash(self):
        """Asking "who is on #N?" with a PR number is a likely human invocation.
        The gate refuses a number that resolves to a PR rather than an issue
        (NotAWorkItem). That is a DESIGNED answer, so it must not read as an
        unexpected failure — and the handler has to sit in `who_holds`: that
        function's own generic fail-closed clause swallows the exception before
        `main()` could ever see it, so a handler there is dead code."""
        (self.gh_dir / "open_prs.json").write_text(json.dumps([{
            "number": ISSUE,
            "title": "an open pull request",
            "body": "",
            "state": "open",
            "url": f"https://example.invalid/pulls/{ISSUE}",
            "headRefName": "feat/some-pr",
            "mergedAt": None,
            "closingIssuesReferences": [],
        }]))
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 2, out)
        self.assertIn("OPEN PULL REQUEST", out)
        self.assertNotIn("unexpected", out)

    def test_a_control_sequence_in_a_refname_cannot_reach_the_output(self):
        """Ref names are untrusted input and this report NAMES them, so a
        control sequence in one can rewrite the ANSWER line the tool exists to
        print. ESC is a C0 control and git rejects it in a ref name — but git
        does not UTF-8-decode when validating ref names, so a C1 control
        (U+0080-U+009F, here CSI U+009B) is accepted, enumerated, and reaches
        this report raw. That is exactly what `_clean` is for."""
        self.local_branch(f"fix/{ISSUE}-strand")
        self.add_worktree("strand", f"fix/{ISSUE}-strand")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                             capture_output=True, text=True,
                             env=self._env()).stdout.strip()
        with open(self.repo / ".git" / "packed-refs", "ab") as fh:
            fh.write(sha.encode() + b" refs/heads/fix/" + str(ISSUE).encode()
                     + "\u009b31mstrand\u009b0m\n".encode("utf-8"))
        rc, out = self.run_tool(ISSUE, git_bin=self.failing_worktree_git())
        self.assertEqual(rc, 2, out)
        # Control: the C1-control refname really did reach the printed list
        # (only the control bytes were stripped) …
        self.assertIn(f"fix/{ISSUE}31mstrand", out)
        # … and no raw C1 control byte survives into the report.
        self.assertNotIn("\u009b", out)

    def test_an_unread_remote_surface_is_not_rendered_as_absent(self):
        """`(no remote branch, open/closed PR, assignee or live holder on the
        fleet board)` asserts
        ABSENCE for the whole remote half. If a BLOCKING remote surface could not
        be queried, that line is exactly the fail-open the LOCAL block guards
        against: a reader skimming it takes "nobody" from a read that never
        happened."""
        self.local_branch(f"fix/{ISSUE}-strand")
        rc, out = self.run_tool(ISSUE, gh=self.tmp / "nonexistent-gh")
        self.assertEqual(rc, 2, out)
        self.assertIn("could NOT be read", out)
        self.assertNotIn("(no remote branch, open/closed PR", out)

    def test_an_advisory_closed_pr_hit_is_not_a_holder(self):
        """A closed PR is an ADVISORY surface — the gate filters advisory out of
        its blocking set ("advisory — cannot block"), so it must not count as a
        holder here either. Counting one made this tool answer "1 holder" at
        rc 0 on a fixture where the gate answers CLEAN, which is the one thing
        this tool promises cannot happen."""
        (self.gh_dir / "closed_prs.json").write_text(json.dumps([{
            "number": 777,
            "title": f"fix: something (#{ISSUE})",
            "body": "",
            "state": "open",
            "url": "https://example.invalid/pulls/777",
            "headRefName": "feat/some-pr",
            "headSha": "deadbeef",
            "mergedAt": None,
            "closingIssuesReferences": [],
        }]))
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)

    def test_a_pr_body_prose_mention_is_reported_as_the_prose_bucket(self):
        """The POSITIVE arm of the kind split (#8175 review): a run whose only
        weak hit is a PR body that merely cross-references the issue must name the
        PROSE bucket and not the non-prose kinds. Without this, the bucket's
        comparison could be dead or inverted and every test here would still pass."""
        (self.gh_dir / "closed_prs.json").write_text(json.dumps([{
            "number": 778,
            "title": "chore: unrelated",
            "body": f"(restored in #{ISSUE})",
            "state": "closed",
            "url": "https://example.invalid/pulls/778",
            "headRefName": "chore/unrelated",
            "headSha": "deadbeef",
            "mergedAt": None,
            "closingIssuesReferences": [],
        }]))
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)
        self.assertIn(_gate_module().WEAK_KIND_PROSE_ONLY, out)
        self.assertNotIn(_gate_module().WEAK_HIT_KINDS, out)

    def test_a_stopped_holder_is_not_reported_as_prose(self):
        """#8175: a board-verified holder whose writer has STOPPED is a WEAK,
        non-blocking hit, so this verb must NOT describe it as a "prose-only
        cross-reference" — that is a different thing and misdescribes what the
        board measured. A reader who sees "prose-only" and a stopped holder in
        the same sentence cannot tell whether real work was detected."""
        self._write_fleet_board(
            issues={ISSUE: [("lane-parked", "branch slug fix/4242-x")]},
            lanes=[{"label": "lane-parked", "live": False}],
        )
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)
        self.assertIn("WEAK SIGNALS", out)
        # The KIND PHRASES are shared with `format_report` (`cp.WEAK_HIT_KINDS`,
        # `cp.WEAK_KIND_PROSE_ONLY`), so asserting the constants is what makes
        # this a parity test rather than a second copy of the prose that can
        # drift from it — and asserting the PROSE kind is ABSENT is what makes
        # the assertion able to fail: the heading must name only the kinds the
        # run actually hit, or a stopped holder reads as prose-only.
        self.assertIn(_gate_module().WEAK_HIT_KINDS, out)
        self.assertIn("has STOPPED", out)
        self.assertNotIn(_gate_module().WEAK_KIND_PROSE_ONLY, out)

    def test_a_lane_label_containing_prose_is_not_reported_as_prose_only(self):
        """The KIND is structured (`Hit.kind`), never a substring of the detail —
        and the detail embeds the lane label, which is free-form registry text in
        this fleet ("1 Capture write doors"). A stopped holder on a lane named
        "12 Prose naturalisation" used to print as a "prose-only cross-reference"
        and never mention the non-prose kinds at all."""
        self._write_fleet_board(
            issues={ISSUE: [("12 Prose naturalisation",
                             "branch slug fix/1-prose")]},
            lanes=[{"label": "12 Prose naturalisation", "live": False}],
        )
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("has STOPPED", out)
        self.assertIn(_gate_module().WEAK_HIT_KINDS, out)
        self.assertNotIn(_gate_module().WEAK_KIND_PROSE_ONLY, out)

    def test_a_live_board_holder_is_answered_as_a_holder(self):
        """The other half of the claim gate the parity contract depends on: a
        board-verified holder whose writer is still running IS a holder here, and
        this verb must count it — the gate exits 1 on the same input."""
        self._write_fleet_board(
            issues={ISSUE: [("lane-live", "branch slug fix/4242-x")]},
            lanes=[{"label": "lane-live", "live": True}],
        )
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: 1 holder(s)", out)
        self.assertIn("lane-live", out)

    def test_an_unmeasured_holder_makes_the_answer_incomplete(self):
        """A lane the board attributed but could not measure is NOT "nobody": the
        gate answers INCOMPLETE (exit 2) and this verb must not answer "nobody"
        at rc 0 for the same input."""
        self._write_fleet_board(
            issues={ISSUE: [("lane-unknown", "branch slug fix/4242-x")]},
            lanes=[{"label": "lane-unknown", "live": None}],
        )
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 2, out)
        self.assertIn("ANSWER INCOMPLETE", out)
        self.assertNotIn("ANSWER: nobody", out)

    def test_the_answer_does_not_claim_completeness_it_does_not_have(self):
        """The final ANSWER line must not assert completeness it does not have.
        The gate prints `N/total surfaces queried` plus an advisory note rather
        than a hardcoded total; its own comment calls that a false completeness
        statement in the artifact a human reads to authorize a dispatch. Here the
        closed-PR surface is never read while every blocking surface answers, so
        the run is legitimately rc 0 — and the count must say so."""
        failing_api = self._write_exec(self.tmp / "gh-noapi", f'''#!/usr/bin/env bash
if [ "${{1:-}}" = "api" ] && [ "${{2:-}}" = "-i" ]; then
  echo "gh-stub: api -i forced to fail" >&2; exit 1
fi
exec {self.gh} "$@"
''')
        rc, out = self.run_tool(ISSUE, gh=failing_api)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)
        ratio = re.search(r"ANSWER: nobody — (\d+)/(\d+) surfaces queried", out)
        self.assertIsNotNone(ratio, out)
        self.assertLess(int(ratio.group(1)), int(ratio.group(2)), out)
        self.assertIn("advisory surface(s) partial or unqueried", out)
        # …and the remote half must not assert absence for that unread surface.
        self.assertNotIn("(no remote branch, open/closed PR", out)
        self.assertIn("could NOT be read in full", out)

    # ── #8560: the landed fact, mirrored from the gate ───────────────────────

    def _closed_issue(self, state_reason: str | None = "COMPLETED") -> None:
        """A CLOSED issue, with its `stateReason` as `gh issue view --json`
        returns it: `COMPLETED` (the work was done) or `NOT_PLANNED` (declined or
        superseded). The default is the COMMON case, so a test that wants the
        not-planned wording opts in (#8560 review)."""
        (self.gh_dir / "issue.json").write_text(json.dumps({
            "number": ISSUE,
            "title": "florfenicol dosing audit",
            "state": "CLOSED",
            "stateReason": state_reason,
            "url": f"https://example.invalid/issues/{ISSUE}",
            "assignees": [],
            "comments": [],
        }))

    def _merged_timeline_pr(self, number: int = 777) -> None:
        """One slurped page holding a MERGED cross-reference naming #ISSUE."""
        (self.gh_dir / "timeline.json").write_text(json.dumps([[
            {
                "event": "cross-referenced",
                "source": {"issue": {
                    "number": number,
                    "title": f"fix: shipped (#{ISSUE})",
                    "state": "closed",
                    "pull_request": {
                        "url": f"https://api.github.invalid/repos/o/r/pulls/{number}",
                        "html_url": f"https://github.invalid/o/r/pull/{number}",
                        "merged_at": "2026-10-05T09:19:18Z",
                    },
                }},
            },
        ]]))

    def test_a_closed_issue_answers_landed_not_nobody(self):
        """#8560: a CLOSED issue is not "nobody" — it is DONE. The answering
        verb must name the same terminal fact the gate refuses on, at rc 0 (the
        question HAS an answer)."""
        self._closed_issue()
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("LANDED — the work appears ALREADY SHIPPED", out)
        self.assertIn("closed as COMPLETED", out)
        self.assertIn("ANSWER: already landed", out)
        self.assertNotIn("ANSWER: nobody", out)
        self.assertNotIn("holder(s) are ALSO present", out)

    def test_the_landed_answer_names_the_issue_state_as_the_only_authority(self):
        """#8629 review (P2): this verb prints the LANDED contract to a reader
        deciding whether to dispatch. It used to say the answer could come from
        "the issue's own state, or a MERGED PR the PAGINATED timeline ties to
        it" — but #8625 demoted that arm to an ADVISORY, so on any LANDED answer
        the printed sentence described a source that cannot produce it. The
        verb's own module docstring made the same claim; both are pinned here so
        the two cannot drift back apart (the docstrings are the contract, and
        `_landed_wording` is shared with the gate).
        """
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "who_is_on_landed_contract", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # ⛔ #8629 review (F5): normalize BEFORE matching. These phrases pin the
        # docstring's MEANING, but `assertIn("its own\n        state is CLOSED",
        # …)` pinned an exact line wrap, so a meaning-preserving rewrap turned it
        # RED — and this repo has already had a commit whose only job was
        # rewrapping a paragraph (1bbaad59f). Flatten first; assert on phrases.
        flat_doc = " ".join((mod.__doc__ or "").split())
        self.assertNotIn("or a MERGED PR the paginated timeline ties to it",
                         flat_doc)
        self.assertIn("its own state is CLOSED", flat_doc)
        self._closed_issue()
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("the issue's OWN STATE, and nothing else", out)
        self.assertNotIn("or a MERGED PR the", out)
        self.assertNotIn("PAGINATED timeline ties to it", out)

    def test_a_not_planned_closure_is_not_answered_as_shipped(self):
        """#8560 review: `NOT_PLANNED` means the issue was DECLINED or
        SUPERSEDED — the work did not ship. The answer is still terminal (rc 0,
        not "nobody"), but it must not claim a shipment that never happened."""
        self._closed_issue(state_reason="NOT_PLANNED")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("LANDED — the issue was CLOSED AS NOT PLANNED", out)
        self.assertNotIn("ALREADY SHIPPED", out)
        self.assertIn("ANSWER: already closed (not planned)", out)
        self.assertNotIn("ANSWER: nobody", out)
        self.assertNotIn("do NOT dispatch onto shipped work", out)

    def test_a_closed_issue_with_an_unknown_reason_makes_no_shipment_claim(self):
        """#8560 review: `CLOSED` with an unavailable / unrecognised
        `stateReason` is TERMINAL, but the tool did not OBSERVE a shipment.
        The gate's own `_landed_conclusion` stays neutral for it, and this verb
        must not re-introduce the claim the gate refuses to make — the tail
        line and the ANSWER line are part of the same terminal fact (this
        mirrors the gate's
        `test_closed_with_an_unknown_reason_makes_no_shipment_claim`)."""
        self._closed_issue(state_reason=None)
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("LANDED — the work item is TERMINAL (closed)", out)
        self.assertNotIn("ALREADY SHIPPED", out)
        # The two extra claim lines the gate does NOT print on this input.
        self.assertNotIn("do NOT dispatch onto shipped work", out)
        self.assertIn("do NOT dispatch onto a terminal work item", out)
        self.assertIn("ANSWER: already closed —", out)
        self.assertNotIn("ANSWER: already landed", out)
        self.assertNotIn("(not planned)", out)
        self.assertNotIn("ANSWER: nobody", out)

    def test_landed_surface_failure_answers_incomplete(self):
        """The landed surface is BLOCKING: an unqueryable timeline must answer
        INCOMPLETE (rc 2) naming `issue landed` — never "nobody". The gate's
        fail-closed posture must hold through this verb too (#8560 review)."""
        rc, out = self.run_tool(ISSUE, env_extra={"GH_STUB_TIMELINE_FAIL": "1"})
        self.assertEqual(rc, 2, out)
        self.assertIn("ANSWER INCOMPLETE", out)
        self.assertIn("issue landed", out)
        self.assertNotIn("ANSWER: nobody", out)

    def test_a_merged_timeline_pr_on_an_open_issue_is_an_advisory(self):
        """#8625: an OPEN issue whose merged PR names it in the title is NOT
        landed — a reference is not a closure. The verb reports the advisory and
        still answers "nobody": a merged PR is not a holder either."""
        self._merged_timeline_pr()
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("[merged PRs naming an OPEN issue]", out)
        self.assertIn("PR #777", out)
        self.assertIn("ANSWER: nobody", out)
        self.assertNotIn("ANSWER: already landed", out)

    def test_landed_with_a_coexisting_holder_names_both(self):
        """A terminal signal and a live holder CAN coexist. The gate says so
        explicitly ("ALSO present"); this verb must not deny a holder it just
        displayed above — the two describe the same input."""
        self._closed_issue()
        self.local_branch(f"fix/{ISSUE}-strand")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: already landed", out)
        self.assertIn("holder(s) are ALSO present", out)

    def test_landed_with_an_unreadable_blocking_surface_still_answers_incomplete(self):
        """A landed fact does not license an INCOMPLETE answer to read as
        complete: when a non-advisory surface could not be read, the verb stays
        rc 2 and says the landed fact is ALSO present."""
        self._closed_issue()
        (self.gh_dir / "open_prs.json").write_text("{not json")
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 2, out)
        self.assertIn("ANSWER INCOMPLETE", out)
        self.assertIn("ALSO reported as LANDED", out)

    def test_wrapper_runs_the_same_verb(self):
        self.local_branch(f"fix/{ISSUE}-strand")
        proc = subprocess.run(
            ["bash", str(WRAPPER), str(ISSUE), "--repo", str(self.repo),
             "--gh", str(self.gh)],
            capture_output=True, text=True, env=self._env(), timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(f"fix/{ISSUE}-strand", proc.stdout)


if __name__ == "__main__":
    unittest.main()
