"""Hermetic tests for tools/who_is_on.py — the "who is on #N?" verb (#4256).

No network, no Docker, no FalkorDB. The GitHub surfaces are driven by a stub
`gh` binary reading JSON fixtures from $GH_STUB_DIR; the LOCAL surfaces (the
ones this tool exists for) use a real temp git repo with real branches and a
real `git worktree add`. The PROCESS CENSUS — the third host input, which decides
whether a local branch may be demoted — is served by a stub `ps` (see
`_write_ps_stub`): read from the real table, the answer would depend on whether
the machine running the suite happens to host live lanes (#10814). Git is isolated from the host (`GIT_CONFIG_GLOBAL` /
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
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "who_is_on.py"
WRAPPER = ROOT / "tools" / "who-is-on.sh"
GATE = ROOT / "tools" / "collision_preflight.py"
PYTHON = sys.executable
ISSUE = 4242
#: The slug the fixture's git remote resolves to; the board fixture stamps it.
SLUG = "acme/example"

_GATE = None
#: `fleet_state`, cached beside `_GATE` so a guard can be pinned by patching the
#: MODULE OBJECT (patching by name only resolves once something else imported it).
_FLEET = None


def _fleet_state():
    """`fleet_state` — the module that OWNS the cwd-reader seam these tests ask.

    Loaded once for the same reason as `_gate_module`: putting `tools/` on
    `sys.path` is a side effect these tests should pay once, not per test.
    """
    global _FLEET
    if _FLEET is None:
        sys.path.insert(0, str(ROOT / "tools"))
        import fleet_state as _fs
        _FLEET = _fs
    return _FLEET


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


def _write_ps_stub(directory: Path, rows: list[str]) -> Path:
    """A `ps` stub serving the PROCESS TABLE from `rows` — one census, no host.

    ⛔ THE PROCESS CENSUS IS A HOST INPUT, NOT A FIXTURE INPUT (#10814).
    `collision_preflight` decides whether a local branch/worktree may be demoted
    from `fleet_state.pi_processes()` (bare `ps` from `PATH`) and
    `fleet_state.process_cwds()` (`/proc/<pid>/cwd`). On any box that HOSTS lanes
    that table holds live `pi` processes — and one whose cwd is a REAPED worktree
    reads back as `… (deleted)`, which `_usable_cwd` refuses, so the census is a
    GAP and EVERY demotion fails closed. The answer then depends on what the host
    happens to be running: measured 2026-11-03 on a cell, `pid 665680 ->
    /home/cmux/tortoise/.worktrees/p1-10538 (deleted)` made
    `test_an_empty_claim_is_listed_but_never_a_holder` red with
    `'EMPTY-CLAIM' not found`, and the same failure reds `test (b)` in CI on the
    fleet's own runners. Stubbing the table is the same PATH-shadowing technique
    `failing_rebuild_path` already uses for `gh`/`cmux`; the fail-closed half is
    pinned by
    `test_a_live_pi_process_with_an_unreadable_cwd_is_never_an_empty_claim`,
    which serves a REAL unreadable-cwd row through this same stub.

    `fleet_state` makes two `ps` calls, so the stub answers both shapes: the
    `command=` census and the `time=` table (`process_table`, read on a board
    REBUILD — the `01 00:00:00` row is the harmless placeholder a fresh, no-pi
    table needs).
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "rows").write_text("".join(f"{row}\n" for row in rows))
    stub = directory / "ps"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "# Stub process table — see the module docstring's HERMETICITY note.\n"
        'case "$*" in\n'
        '  *command=*) cat "$PS_STUB_ROWS" ;;\n'
        '  *time=*)    printf \'0 0 00:00:00\\n\' ;;\n'
        "  *)          exit 1 ;;\n"
        "esac\n")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def _git_env(tmp: Path, gh_dir: Path | None = None, ps_dir: Path | None = None) -> dict:
    """Host-independent env for child processes: no COLLISION_PREFLIGHT_* leak
    (a bad inherited value would change every invocation), no global git config
    (a global commit.gpgsign/init.defaultBranch must not affect the fixture),
    HOME inside the temp dir, and — when `ps_dir` is given — a `ps` stub ahead of
    the real one on `PATH` so the process CENSUS cannot come from the host."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("COLLISION_PREFLIGHT_")}
    if gh_dir is not None:
        env["GH_STUB_DIR"] = str(gh_dir)
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["HOME"] = str(tmp)
    if ps_dir is not None:
        env["PATH"] = f"{ps_dir}{os.pathsep}{env.get('PATH', '')}"
        env["PS_STUB_ROWS"] = str(ps_dir / "rows")
    return env


class WhoIsOnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="who-is-on-")
        self.tmp = Path(self._tmp.name)
        # FIRST — every `_git` call below goes through `_env()`, which puts this
        # stub ahead of the real `ps`. The process table is HOST state and must
        # never reach the answer (see `_write_ps_stub`).
        self.ps_dir = self.tmp / "psstub"
        _write_ps_stub(self.ps_dir, [f"1 0 {os.getuid()} /sbin/init"])
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
        # Where a REBUILD's shadowed `gh`/`cmux` stubs record that they were
        # called (see `failing_rebuild_path`).
        self.rebuild_stub_log = self.tmp / "rebuild-stub-calls.log"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _write_fleet_board(self, issues=None, lanes=None, repo=SLUG,
                           registry_measured=True, registry_error="",
                           liveness_source=None, liveness_unreadable=None) -> Path:
        """Write the claim gate's board at `$HOME/.pi/agent/state`.

        Only the fields `collision_preflight._read_board` / `_holders_from_board`
        consume: `repo` (the stamp `_board_is_for_repo` compares against the
        queried slug — a mismatch forces a rebuild), `reconciliation` (both reads
        measured), `index.issues` (attribution) and `lanes` (liveness).
        `generated_at` is NOW, so the board is fresh inside
        `fleet_state.STATE_FRESH_S` and no run pays a REBUILD.

        `liveness_source` / `liveness_unreadable` are the LIVENESS provenance the
        gate publishes in its own holder note (#11202, #11678): a cell answers from
        `process-table` while both cmux grammars refuse, and the board must be able
        to carry that shape here for the parity test to be non-vacuous.
        """
        path = self.tmp / ".pi" / "agent" / "state" / "fleet-state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        reconciliation: dict = {"registry_measured": bool(registry_measured),
                                # The cause text `load_registry` records, so a
                                # test can model an UNPROVISIONED roster (#9392).
                                "registry_error": str(registry_error),
                                "github_measured": True,
                                # The board must also stamp its OPEN-ISSUE
                                # coverage: `_board_can_answer` refuses an
                                # unstamped board (a pre-#8175 cache cannot prove
                                # it saw every open issue) and this fixture's
                                # boards are meant to be SERVED, not rebuilt.
                                "open_issues_truncated": False,
                                "open_issues_read": 1,
                                "open_issues_limit": 2000}
        if liveness_source is not None:
            reconciliation["liveness_source"] = str(liveness_source)
        if liveness_unreadable is not None:
            reconciliation["liveness_unreadable"] = list(liveness_unreadable)
        path.write_text(json.dumps({
            "generated_at": datetime.now(UTC).isoformat(),
            "repo": repo,
            # `orphans` is REQUIRED by `_board_can_answer` (an absent orphan report
            # would leave the board's liveness provenance unknown): `build_state`
            # always writes it, and an empty list is what a readable live-session
            # store produces.
            "orphans": [],
            "reconciliation": reconciliation,
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
        git config, HOME inside the temp dir, a STUBBED process census."""
        return _git_env(self.tmp, self.gh_dir, self.ps_dir)

    def set_process_table(self, *rows: str) -> None:
        """Replace the stubbed process table — `pid ppid uid command` rows."""
        _write_ps_stub(self.ps_dir, list(rows))

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                       text=True, env=_git_env(self.tmp))

    @staticmethod
    def _write_exec(path: Path, body: str) -> Path:
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return path

    def failing_rebuild_path(self) -> str:
        """A `PATH` on which the claim gate's REBUILD reads fail locally.

        ⛔ A REBUILD RUNS IN THE TOOL SUBPROCESS (`collision_preflight._read_board`
        → `fleet_state.build_state`), so it cannot be mocked from here, and it
        uses BARE `gh`/`cmux` from `PATH` — not this harness's `--gh` stub. An
        unanswerable board therefore reaches the network (two real GraphQL calls
        against a nonexistent repo) and, on a box where cmux is reachable, reads
        the OPERATOR's real workspace list.

        Shadowing both with FAILING stubs keeps the cause under test intact —
        the real `FileNotFoundError` from `load_registry`, which runs first —
        while making the rest of the rebuild a local no-op (`_run` degrades a
        non-zero exit to "", the production fail-open shape). The stubs also
        RECORD their calls (`self.rebuild_stub_log`), so the test can prove the
        rebuild happened through THIS path.
        """
        stub_dir = self.tmp / "rebuild-stubs"
        stub_dir.mkdir(exist_ok=True)
        log = self.rebuild_stub_log
        for name in ("gh", "cmux"):
            self._write_exec(
                stub_dir / name,
                "#!/usr/bin/env bash\n"
                f'printf \'%s\\n\' "$0 $*" >> {log}\n'
                f'echo "{name}-stub: rebuilds never leave the test" >&2\n'
                "exit 127\n")
        return f"{stub_dir}:{os.environ.get('PATH', '')}"

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

    def reachable_origin(self) -> None:
        """Point `origin` at a REAL bare repo in the fixture.

        The default remote URL is a network-free `https://github.com/acme/…` that
        resolves the SLUG but cannot be FETCHED, so the tool's `refs/claims/issue-N`
        read fails (exit 128) and the row is NOT demoted for that reason — which
        would make any demotion-family test pass vacuously on the wrong cause (found
        exactly that way while proving the census test non-vacuous). A bare repo in
        the temp dir makes the claim read answer "no claim", leaving the behaviour
        under test as the only thing between the fixture and a demotion.
        """
        origin = self.tmp / "remotes" / "acme" / "example.git"
        origin.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                       check=True, capture_output=True, text=True, env=self._env())
        self._git(self.repo, "remote", "set-url", "origin", str(origin))
        self._git(self.repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")
        self._git(self.repo, "push", "-q", "origin", "refs/heads/main:refs/heads/main")

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

    def test_an_empty_claim_is_listed_but_never_a_holder(self):
        """#6108 (reopened 2026-10-24): an EMPTY CLAIM is a REPORT, not a block.

        The gate demotes a local branch that is 0 commits ahead of `origin/main` with no
        live owner, no uncommitted work and no live claim ref. This verb must still LIST
        it — reaping the branch is a human, evidence-based action, and a row that vanished
        would hide the thing that needs cleaning up. So the answer is "nobody is a holder"
        WITH the empty branch named, in the weak-signal section.
        """
        self.reachable_origin()
        self.local_branch(f"fix/{ISSUE}-empty", ahead=0)
        rc, out = self.run_tool(ISSUE)
        self.assertEqual(rc, 0, out)
        # LISTED — the report half.
        self.assertIn(f"fix/{ISSUE}-empty", out)
        self.assertIn("EMPTY-CLAIM", out)
        # ...and NOT a holder: the weak section says so explicitly.
        self.assertIn("WEAK SIGNALS", out)
        self.assertIn("none of these make", out)
        # The gate agrees on the same input — the parity these two verbs promise.
        rc_gate, out_gate = self.run_tool(ISSUE, tool=GATE)
        self.assertEqual(rc_gate, 0, out_gate)
        self.assertIn("VERDICT: CLEAN", out_gate)
        self.assertIn("EMPTY-CLAIM", out_gate)

    def _require_proc_cwd_reads(self) -> None:
        """SKIP where the kernel does not hand us a pid's cwd (`/proc` absent).

        The deleted-cwd census fixture is a property of the KERNEL (the ` (deleted)`
        marker on `/proc/<pid>/cwd`), not of the change under test. On a box with no
        `/proc` — macOS, where `fleet_state` reads a cwd through `lsof` instead — the
        shape cannot be built at all, so a hard assertion there reds a mac lane's
        local run FOR ITS PLATFORM: the same ambient-input class this fixture exists
        to remove. A named precondition that FAILS is still a red suite.

        ⛔ THE QUESTION IS ASKED OF THE SEAM, NOT RE-SPELLED. Asking
        `fleet_state._proc_cwd_is_readable()` rather than `Path("/proc/self/cwd").
        exists()` keeps ONE definition of it: that seam is what decides whether the
        production read even takes the `/proc` branch, so a re-spelled copy here can
        drift away from the behaviour the test is supposed to track (review, #10777).
        It is a RUNTIME guard rather than a `skipUnless` decorator for the same
        reason: a decorator evaluates at import, so
        `test_the_deleted_cwd_guard_asks_the_seam` could not force it.
        """
        if not _fleet_state()._proc_cwd_is_readable():
            self.skipTest(
                "this fixture builds a live process whose cwd the KERNEL marks "
                "' (deleted)', which needs /proc; on this box "
                "fleet_state._proc_cwd_is_readable() is False, so the shape cannot "
                "be built (not a defect in the code under test)")

    def test_the_deleted_cwd_guard_asks_the_seam(self):
        """The skip is pinned, and it is asked of `fleet_state`'s own seam.

        Two silent regressions are possible and neither is visible on Linux: the
        guard is dropped again (a mac lane goes red for its platform), or it is
        re-spelled as `Path("/proc/self/cwd").exists()` and quietly diverges from
        the seam the production read uses. Forcing the module's OWN seam to `False`
        is the only way to exercise that branch on a Linux box, so this arm reddens
        on both. Non-vacuity, measured: with the guard removed from
        `_require_proc_cwd_reads`, this arm FAILS (1 failed); restored, 1 passed.
        """
        with (mock.patch.object(_fleet_state(), "_proc_cwd_is_readable",
                               return_value=False),
              self.assertRaises(unittest.SkipTest)):
            self._require_proc_cwd_reads()

    def test_a_live_pi_process_with_an_unreadable_cwd_is_never_an_empty_claim(self):
        """⛔ THE STUBBED CENSUS STILL FAILS CLOSED (#10814).

        `setUp` serves a process table with no `pi` row so the demotion family
        does not depend on what the host is running. That must not degrade into an
        unconditional "nobody is there": a LIVE `pi` whose cwd cannot be resolved
        (`fleet_state._usable_cwd` refuses the kernel's ` (deleted)` suffix — the
        shape a lane leaves when its worktree is reaped under it) is a GAP, and a
        gap means the read could not answer, so the branch is NOT demoted and NOT
        reported as an empty claim. The row is served through the SAME stub, from a
        real process whose directory is really gone.
        """
        self._require_proc_cwd_reads()
        self.reachable_origin()
        self.local_branch(f"fix/{ISSUE}-census", ahead=0)
        gone = self.tmp / "reaped-worktree"
        gone.mkdir()
        # A real process standing in a directory that is then REMOVED: the kernel
        # appends ` (deleted)` to its `/proc/<pid>/cwd`, which is exactly the
        # unreadable-cwd shape the census must not read as "no lane there".
        proc = subprocess.Popen(
            ["/bin/sh", "-c", 'cd "$0" && rmdir "$0" && exec sleep 120', str(gone)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self._wait_for_deleted_cwd(proc.pid)
            self.set_process_table(f"{proc.pid} 1 {os.getuid()} pi")
            rc, out = self.run_tool(ISSUE)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertEqual(rc, 0, out)
        self.assertIn("UNREADABLE", out)
        self.assertIn("NOT treated as an empty claim", out)
        self.assertNotIn("EMPTY-CLAIM", out)

    def test_an_ambient_process_table_cannot_change_an_answer(self):
        """A `ps` on the INHERITED `PATH` must not win over the fixture's stub.

        `setUp` owns the census by PREPENDING its stub dir to `PATH`. That is the
        load-bearing half of the fix — a `ps` that merely exists is not enough —
        and it is invisible on a clean host, where the real table names no `pi`
        and the answer is the same either way. So the leak is SIMULATED: a host
        `PATH` carrying a gap-serving `ps` of its own (the 2026-11-03 shape, one
        live `pi` whose cwd reads back ` (deleted)`) must still answer
        EMPTY-CLAIM. Drop the prepend in `_git_env` and this arm answers
        UNREADABLE instead.

        ⛔ THE AMBIENT STUB SERVES ITS OWN ROWS, deliberately. A copy of
        `_write_ps_stub` here would ALSO inherit the fixture's `PS_STUB_ROWS`
        indirection, which `_git_env` points at the FIXTURE's table — so a dropped
        prepend would still answer EMPTY-CLAIM and the arm would be VACUOUS.
        Measured: with `_write_ps_stub` and the prepend removed this arm PASSED,
        i.e. it pinned nothing. Hence a hard-coded `cat` of the ambient rows.
        """
        self._require_proc_cwd_reads()
        self.reachable_origin()
        self.local_branch(f"fix/{ISSUE}-empty", ahead=0)
        ambient = self.tmp / "ambient-bin"
        ambient.mkdir()
        gone = self.tmp / "ambient-gone"
        gone.mkdir()
        proc = subprocess.Popen(
            ["/bin/sh", "-c", 'cd "$0" && rmdir "$0" && exec sleep 120', str(gone)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            self._wait_for_deleted_cwd(proc.pid)
            rows = ambient / "ambient-rows"
            rows.write_text(f"{proc.pid} 1 {os.getuid()} pi\n")
            stub = ambient / "ps"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                '# An ambient (host-PATH) stub: its OWN rows, not the fixture\'s.\n'
                'case "$*" in\n'
                f'  *command=*) cat "{rows}" ;;\n'
                "  *time=*)    printf '0 0 00:00:00\\n' ;;\n"
                "  *)          exit 1 ;;\n"
                "esac\n")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                       | stat.S_IXOTH)
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith("COLLISION_PREFLIGHT_")}
            env["PATH"] = f"{ambient}{os.pathsep}{env.get('PATH', '')}"
            with mock.patch.dict(os.environ, env, clear=True):
                rc, out = self.run_tool(ISSUE)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
        self.assertEqual(rc, 0, out)
        self.assertIn("EMPTY-CLAIM", out)
        self.assertNotIn("could not resolve the cwd", out)

    @staticmethod
    def _wait_for_deleted_cwd(pid: int, timeout: float = 10.0) -> None:
        """Block until `/proc/<pid>/cwd` reads back with the ` (deleted)` suffix."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = os.readlink(f"/proc/{pid}/cwd")
            except OSError:
                raw = ""
            if raw.endswith(" (deleted)"):
                return
            time.sleep(0.02)
        raise AssertionError(
            f"`/proc/{pid}/cwd` never read back as deleted — the fixture cannot "
            "model a live `pi` whose cwd is unreadable, so this run proves nothing")

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

    def test_the_answer_says_which_store_the_holder_read_rested_on(self):
        """⛔ PARITY ON THE DISCLOSURE, NOT ONLY ON THE COUNT (#11678).

        Measured at `origin/main` `a7da06cc77` on a cell: the GATE's
        `issue assignee/holder` row said
        `liveness: process-table; unreadable store(s): cmux-session-store` inside a
        CLEAN, and this verb — the same read, from the same `run_preflight` —
        answered `ANSWER: nobody` with NEITHER clause, because it renders only
        surfaces that produced a HIT. #11202's whole property is that a CLEAN
        resting on one liveness store must not read as a CLEAN that read them all;
        that property is about the reader, and this verb is the reader agents are
        pointed at. The strings asserted here are the GATE's own note, so the two
        verbs cannot drift into describing one read two ways.
        """
        self._write_fleet_board(
            liveness_source="process-table",
            liveness_unreadable=["cmux-session-store"],
        )
        rc, out = self.run_tool(9999)
        self.assertEqual(rc, 0, out)
        self.assertIn("ANSWER: nobody", out)
        self.assertIn("CLAIM GATE — the gate's own note", out)
        self.assertIn("liveness: process-table", out)
        self.assertIn("unreadable store(s): cmux-session-store", out)

    def test_a_board_with_no_liveness_keys_prints_no_store_claim(self):
        """ABSENT IS NOT EMPTY, on this verb too (#11678).

        A pre-#11202 board carries neither key. Printing a store name — or an empty
        "unreadable store(s): (none)" — would be a claim about a read that never
        happened, so the clause is omitted entirely; the CLAIM GATE block still
        prints, because the board source it DID record is a real datum.
        """
        rc, out = self.run_tool(9999)
        self.assertEqual(rc, 0, out)
        self.assertIn("board source:", out)
        self.assertNotIn("liveness:", out)
        self.assertNotIn("unreadable store(s)", out)

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

    def test_a_failed_git_remote_refuses_the_inventory_instead_of_answering(self):
        """The fail-closed guard in `pushed_branch_names` (#8881) had no test.

        With `remotes=[]` — what a FAILED `git remote` used to be read as — the
        no-prefix arm strips one path component, so a never-pushed branch could
        be credited as pushed and hidden from `--inventory`: stranded work
        reported as FREE. The read is therefore refused, and the refusal must
        reach the caller as INCOMPLETE (exit 2), never as a decided "all clear"
        (exit 0 — what the pre-fix run answered) and never as exit 1, which is
        this repo's COLLISION code.

        The shim fails `git remote` but NOT `git remote -v`, so the earlier
        slug-resolution guard cannot answer for the branch under test.
        """
        self.local_branch(f"fix/{ISSUE}-strand")
        real = shutil.which("git")
        stub = self.tmp / "gitremote" / "git"
        stub.parent.mkdir(parents=True, exist_ok=True)
        stub = self._write_exec(stub, f'''#!/usr/bin/env bash
if [ "${{1:-}}" = "remote" ] && [ "${{2:-}}" != "-v" ]; then
  echo "git-shim: refusing the remote list" >&2; exit 128
fi
exec {real} "$@"
''')
        rc, out = self.run_tool("--inventory", git_bin=stub)
        self.assertEqual(rc, 2, out)          # INCOMPLETE, never a decided answer
        self.assertNotEqual(rc, 1, out)       # exit 1 is COLLISION — never a crash
        self.assertNotIn("Traceback", out)
        self.assertIn("could not be read", out)
        self.assertIn("UNKNOWN", out)

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

    def test_a_pr_number_resolves_to_its_work_item_and_answers(self):
        """#9976: the PR-number shape ANSWERS instead of abstaining.

        The gate resolves an OPEN pull request to the work item(s) it serves
        (#9957); this verb used to stop at `ANSWER INCOMPLETE: … is an OPEN PULL
        REQUEST`, so the two verbs disagreed about the same number — which
        AGENTS.md says they cannot, because this verb reuses the gate's own
        enumeration. Here the PR's closing reference names ISSUE, so the answer
        for ISSUE is computed and printed under its own heading, at rc 0.
        """
        (self.gh_dir / "open_prs.json").write_text(json.dumps([{
            "number": 9999,
            "title": "fix: something unrelated in its title",
            "body": "",
            "state": "open",
            "url": "https://example.invalid/pulls/9999",
            "headRefName": "feat/some-pr",
            "mergedAt": None,
            "closingIssuesReferences": [{"number": ISSUE}],
        }]))
        rc, out = self.run_tool(9999)
        self.assertEqual(rc, 0, out)
        self.assertIn("is an OPEN PULL REQUEST — resolved via", out)
        self.assertIn(f"to #{ISSUE}", out)
        self.assertIn(f"who is on #{ISSUE}?", out)
        # It answers — it does not abstain with the old refusal.
        self.assertNotIn("not a work item", out)
        self.assertNotIn("INCOMPLETE", out)
        # …and the resolution is REPORTED, so a reader knows which item answered.
        self.assertIn("the same resolution the pre-flight uses", out)

    def test_a_pr_number_whose_work_items_cannot_be_resolved_still_refuses(self):
        """The fail-closed half: no resolvable work item is rc 2, never `nobody`.

        `release_pull_request_work_items` is the gate's own resolver; when it
        finds nothing there is no work item to answer for, and a half-answer
        would be worse than the refusal. The gate's own sentence stays first so
        the shape is recognisable.
        """
        (self.gh_dir / "open_prs.json").write_text(json.dumps([{
            "number": 9999, "title": "fix: nothing names the issue", "body": "",
            "state": "open", "url": "https://example.invalid/pulls/9999",
            "headRefName": "feat/some-pr", "mergedAt": None,
            "closingIssuesReferences": [],
        }]))
        rc, out = self.run_tool(9999)
        self.assertEqual(rc, 2, out)
        self.assertIn("OPEN PULL REQUEST", out)
        self.assertIn("This is NOT", out)
        self.assertNotIn("ANSWER: nobody", out)

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

    def test_an_unprovisioned_roster_names_provisioning_not_a_rebuild(self):
        """#9392, on THIS verb: the two renders must agree.

        `who-is-on` prints the claim gate's surface note, and the gate's
        INCOMPLETE verdict line prints the remedy — both now take their sentence
        from `fleet_state.registry_remedy`, so a lane that asks "who is on #N?"
        and the lane that runs the pre-flight are told the SAME thing: the roster
        was never provisioned, and a rebuild cannot help. Before the fix this
        verb could only report "the lane registry could not be read" while the
        gate told the reader to rebuild the board.

        ⛔ THE REBUILD IS KEPT LOCAL (review P2): the gate rebuilds inside this
        tool's own subprocess with bare `gh`/`cmux`, so both are shadowed by
        failing stubs — the real `FileNotFoundError` under test still happens,
        but no GraphQL call and no operator cmux read can.
        """
        self._write_fleet_board(
            registry_measured=False,
            registry_error=(
                "FileNotFoundError: [Errno 2] No such file or directory: "
                f"'{self.tmp}/.pi/agent/state/lane-registry.tsv'"),
        )
        rc, out = self.run_tool(
            ISSUE, env_extra={"PATH": self.failing_rebuild_path()})
        self.assertEqual(rc, 2, out)
        self.assertIn("ANSWER INCOMPLETE", out)
        self.assertNotIn("ANSWER: nobody", out)
        self.assertIn("lane-registry.tsv", out)
        self.assertIn("NEVER PROVISIONED", out)
        self.assertIn("CANNOT create it", out)
        self.assertIn("PROVISIONING", out)
        self.assertNotIn("rebuild the fleet board", out.lower())
        # ⛔ AND THE REBUILD REALLY RAN — through the LOCAL stubs (review P2), so
        # this test keeps proving the claim gate meets the REAL roster read and
        # keeps doing it without a network round-trip.
        log = self.rebuild_stub_log.read_text()
        self.assertIn("/gh ", log)
        self.assertIn("/cmux ", log)

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

    def _interpreter_path(self, *, uv: bool) -> str:
        """A PATH holding a pre-3.12 `python3` shim, and optionally a `uv` stub.

        ⛔ HERMETIC, AND THAT IS THE POINT (#11202 review round 8). The wrapper's
        interpreter selection was shipped with NO test: reverting it to the old
        `exec python3 "$here/who_is_on.py" "$@"` left the whole suite green, so the
        measured owner-Mac failure (3.9 -> the verb exits 1) could come back silently.
        Every entry here is a shell script needing no interpreter, and the wrapper's
        only other external call — `dirname` — is a symlink to the real one, so the
        result cannot depend on what this host happens to have installed.

        ⛔ AND THE PROBE PAYLOAD IS EVALUATED, NOT SWALLOWED (#11202 review round 10).
        An `exit 1` for any `-c` pinned only the SHAPE of the fast path: the version
        BOUND stayed unpinned, so lowering it to `>= (3, 9)` — the exact owner-Mac
        regression, where a 3.9 `python3` wins and the module's own guard exits 1 —
        left the suite green (measured). The real payload therefore runs under a
        FAKED `sys.version_info`, the idiom `tests/test_collision_preflight.py` uses.
        """
        d = self.tmp / "interp-bin"
        d.mkdir(exist_ok=True)
        self._write_exec(d / "python3", (
            "#!/bin/bash\n"
            "# a 3.11 interpreter, one patch below the floor: EVALUATES the real probe\n"
            "if [ \"$1\" = \"-V\" ]; then echo 'Python 3.11.99'; exit 0; fi\n"
            "if [ \"$1\" = \"-c\" ]; then\n"
            # ⛔ THE FAKE IS *BELOW* THE FLOOR, AT A POINT NO REAL BOUND CAN SIT ABOVE
            # (#11202 review rounds 11-12). 3.9 pinned the bound only over
            # (3,9,6]..host, so `>= (3, 10)` / `>= (3, 11)` shipped green. 3.11.9 fixed
            # those but was itself a SINGLE POINT: `>= (3, 11, 10)` still shipped green
            # (3.11.10 is a real release), which would take the fast path on a genuine
            # 3.11 and land in the module's own guard -> exit 1, the owner-Mac failure.
            # 3.11.99 sits above every real 3.11 patch, so any bound that would accept
            # a real 3.11 fails this probe.
            f'  exec "{PYTHON}" -c \'import sys; '
            'sys.version_info = (3, 11, 99, "final", 0);'
            ' exec(sys.argv[1])\' "$2"\n'
            "fi\n"
            "exit 1\n"
        ))
        for name in ("bash", "dirname"):
            link = d / name
            if not link.exists():
                real = shutil.which(name)
                self.assertIsNotNone(real, name)
                link.symlink_to(real)
        if uv:
            self._write_exec(d / "uv", (
                "#!/bin/bash\n"
                "printf '%s\\n' \"$@\" > \"$UV_ARGS_FILE\"\n"
                "exit 0\n"
            ))
        return str(d)

    def test_the_wrapper_names_the_missing_runtime_instead_of_dying_in_the_module(self):
        """No 3.12 interpreter and no `uv` -> rc 2 naming what is missing.

        ⛔ rc 2 is this verb's "could not check" code (`rc 3` is a malformed
        INVOCATION, and the flags here are fine — the RUNTIME is absent). Pre-change
        the module's own guard exited 1, a code this verb documents it never returns.
        """
        proc = subprocess.run(
            ["bash", str(WRAPPER), str(ISSUE), "--repo", str(self.repo)],
            capture_output=True, text=True, timeout=120,
            env={**self._env(), "PATH": self._interpreter_path(uv=False)},
        )
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("Python >= 3.12", proc.stderr)
        self.assertIn("uv", proc.stderr)
        # It must NAME the interpreter it found, not just assert one is missing.
        self.assertIn("3.11.99", proc.stderr)

    def test_a_312_python3_is_preferred_and_uv_is_never_reached(self):
        """The FAST PATH: a >=3.12 `python3` wins, and `uv` must not run at all.

        ⛔ AND IT IS THE ARM THAT RUNS ON EVERY NORMAL BOX (#11202 review round 9).
        Both tests above use a shim whose version probe FAILS, so they drive only the
        two fallback arms — deleting the `python3` fast path outright left this suite
        green (measured: 61 passed). That mutation is not cosmetic: with the fast path
        gone the wrapper falls through to the refusal, whose message says there is
        "neither" a 3.12 python3 nor `uv` on a box that plainly has one — a
        self-contradicting rc 2 for the verb AGENTS.md documents.

        ⛔ AND IT IS DRIVEN AT TWO ABOVE-FLOOR POINTS (#11202 review round 12). One
        point AT the floor pinned only that `>= (3, 12)` accepts (3,12,0); two
        plausible tightenings — `sys.version_info[:2] == (3, 12)` and
        `(3, 12) <= sys.version_info < (3, 13)` — then shipped green, and on a 3.13
        host they lose the fast path and hand back the same self-contradicting refusal
        ("neither a 3.12+ python3 nor uv", from an interpreter that IS 3.13). Faking
        (3,12,0) and (3,13,0) pins "at the floor is accepted" AND "above it still is".
        """
        for version, text in (((3, 12, 0), "3.12.0"), ((3, 13, 0), "3.13.0")):
            with self.subTest(python=text):
                d = self.tmp / f"interp-fast-{text}"
                d.mkdir(exist_ok=True)
                ran = self.tmp / f"py-args-{text}.txt"
                uv_ran = self.tmp / f"uv-ran-{text}.txt"
                self._write_exec(d / "python3", (
                    "#!/bin/bash\n"
                    f'printf \'%s\\n\' "$@" >> "{ran}"\n'
                    f"if [ \"$1\" = \"-V\" ]; then echo 'Python {text}'; exit 0; fi\n"
                    # ⛔ THE PROBE IS PINNED, NOT RUN UNDER THE HOST INTERPRETER
                    # (#11202 review round 11): delegating to the real interpreter made
                    # this arm host-version dependent, so a raise above the floor
                    # shipped green on a newer host.
                    f'if [ "$1" = "-c" ]; then exec "{PYTHON}" -c \'import sys; '
                    # ⛔ SPELLED AS A FULL FIVE-TUPLE, LIKE A REAL `version_info`
                    # (#11202 review round 13). A bare `{version!r}` produced
                    # `(3, 12, 0)` — no `releaselevel`/`serial` — so a
                    # BEHAVIOUR-PRESERVING probe (`>= (3, 12, 0, "final", 0)`, which a
                    # 3.12.3 interpreter answers True) was reported RED: the fake made
                    # it False and the arm failed at `uv`'s exit 7. It was also not
                    # quote-safe: `repr` of any string component emits `'final'`, which
                    # closes the bash single-quoted `-c` word and leaves python with a
                    # NameError. Spelling the components removes both.
                    f'sys.version_info = ({version[0]}, {version[1]}, {version[2]},'
                    ' "final", 0); exec(sys.argv[1])\' "$2"; fi\n'
                    "exit 0\n"
                ))
                self._write_exec(d / "uv", (
                    "#!/bin/bash\n"
                    f'echo ran > "{uv_ran}"\n'
                    "exit 7\n"      # loud if the fast path fails to short-circuit
                ))
                for name in ("bash", "dirname"):
                    link = d / name
                    if not link.exists():
                        link.symlink_to(shutil.which(name))
                proc = subprocess.run(
                    ["bash", str(WRAPPER), str(ISSUE), "--repo", str(self.repo)],
                    capture_output=True, text=True, timeout=120,
                    env={**self._env(), "PATH": str(d)},
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertFalse(uv_ran.exists(),
                                 f"uv must not be reached with a {text} python3")
                args = ran.read_text().splitlines()
                # The FIRST call is the version probe (`-c …`), never the module…
                self.assertEqual(args[0], "-c")
                # …and somewhere after it, the module is executed from THIS tree.
                modules = [a for a in args if a.endswith("who_is_on.py")]
                self.assertTrue(modules, args)
                self.assertEqual(Path(modules[0]).resolve(), TOOL.resolve())

    def test_the_wrapper_falls_back_to_uv_with_the_repo_project(self):
        """`uv` is the fallback, and `--project` must select THIS repo's environment.

        Without `--project`, uv resolves whatever environment the caller's cwd
        happens to belong to — which is how a lane in another checkout gets a
        different answer for the same verb.
        """
        log = self.tmp / "uv-args.txt"
        proc = subprocess.run(
            ["bash", str(WRAPPER), str(ISSUE)],
            capture_output=True, text=True, timeout=120,
            env={**self._env(), "PATH": self._interpreter_path(uv=True),
                 "UV_ARGS_FILE": str(log)},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        args = log.read_text().split()
        self.assertIn("run", args)
        self.assertIn("--project", args)
        project = Path(args[args.index("--project") + 1]).resolve()
        self.assertEqual(project, ROOT.resolve())
        # argv after the project is `python <script>` — the interpreter uv owns, then
        # the module. Assert the MODULE is ours (that is what the fallback must run).
        self.assertEqual(args[args.index("--project") + 2], "python")
        target = Path(args[args.index("--project") + 3])
        self.assertEqual(target.name, "who_is_on.py")

    def test_the_ancestry_walk_uses_the_PS_binary_not_git(self):
        """Cycle-2 review, MEDIUM: `_ancestor_pids(ps_bin, …)` got `git_bin`.

        On Linux `/proc` masks the mistake; on a host WITHOUT procfs the macOS
        fallback runs `<binary> -o ppid= -p <pid>`, so `git -o ppid= …` fails and
        the ancestry set collapses to `{getpid()}` — the caller's own `pi`
        ancestor is then invisible and the verb reports the caller as its own
        holder (the #10684 defect, one verb over, on macOS). The seam is driven
        here so the finding cannot come back on any host: the binary handed to
        `_ancestor_pids` must be the PS binary, never the git one.
        """
        import importlib.util
        from types import SimpleNamespace
        from unittest import mock as _mock

        cp = _gate_module()
        spec = importlib.util.spec_from_file_location("who_is_on_ps_binary", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        used: list[str] = []
        captured: list[object] = []

        class _Captured(Exception):
            pass

        def fake_ancestors(binary, timeout):
            used.append(binary)
            return frozenset({424242})

        def fake_preflight(issue, target, gh, git, timeout, **kw):
            captured.append((kw.get("identity"), kw.get("ps_bin")))
            raise _Captured("identity captured")

        with _mock.patch.object(
                cp, "_resolve_target",
                return_value=(SimpleNamespace(path=None, slug=SLUG), 0)), \
             _mock.patch.object(cp, "_ambiguity_refusal", return_value=None), \
             _mock.patch.object(cp, "_ancestor_pids", fake_ancestors), \
             _mock.patch.object(cp, "run_preflight", fake_preflight):
            mod.who_holds(ISSUE, None, "gh", "git-binary-not-a-ps", 5.0)

        self.assertEqual(len(used), 1, used)
        self.assertNotIn("git-binary-not-a-ps", used)
        self.assertTrue(used[0].endswith("ps"),
                        f"the ancestry walk was handed {used[0]!r}, not a ps binary")
        self.assertEqual(captured[0][0].self_pids, frozenset({424242}))
        # ⛔ THE SAME RESOLUTION MUST REACH `run_preflight` TOO (cycle-3 review).
        # It defaults to the literal `"ps"`, so `COLLISION_PREFLIGHT_PS` meant two
        # different things inside one `who_holds` run while `_ps_bin`'s docstring
        # claimed parity with the gate (which resolves `ps` once and passes it to
        # BOTH uses, `collision_preflight.py:9840` and `:9860`).
        self.assertEqual(captured[0][1], used[0],
                         "run_preflight was not handed the same ps binary")


if __name__ == "__main__":
    unittest.main()
