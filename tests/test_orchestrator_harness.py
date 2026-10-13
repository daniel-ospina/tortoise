"""Hermetic regression tests for the orchestrator harness scripts (#8469 review).

No network, no Docker, no FalkorDB, no git, no tmux. Every assertion drives the
SHIPPED script in ``operations/orchestrator-harness/`` — never a re-implementation
of its logic, which would pass on the broken copy too.

Findings covered (all reproduced by the reviewer at the pre-fix head):

* ``lane.sh`` writes the lane brief with an UNQUOTED heredoc, so an unescaped
  backtick in the body is COMMAND SUBSTITUTION. The new ``Refs`` rule shipped
  with its four backticked spans unescaped, so the word ``Refs`` was deleted
  from the brief before the lane ever read it (the token the sentence forbids,
  and the ``Still open: <indicator>`` escape hatch it requires, both vanished).
* ``merged-not-closed.sh`` enumerated its "universe" with
  ``gh issue list --limit 400``. ``gh`` returns the cap and exits 0, so ~620 of
  ~1,020 open issues were never examined and the truncated count printed as if
  it were the whole repo — the one class of partial check this repo's own rules
  forbid. The truncation lands on the OLDEST issues, i.e. exactly the stranded
  population the instrument exists to find.
* ``merged-not-closed.sh --help`` printed the script's own source (a hardcoded
  ``sed -n '2,40p'`` overshot the header comment block).
* ``merged-not-closed.sh --label`` / ``--repo`` with no value dereferenced an
  unset positional under ``set -u`` and exited 1 — a code the header promises
  never to emit ("2 when a surface could not be queried — never 1").

Run under pytest:  python3 -m pytest tests/test_orchestrator_harness.py -q
Run standalone:    python3 tests/test_orchestrator_harness.py
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "operations" / "orchestrator-harness"
LANE = HARNESS / "lane.sh"
MNC = HARNESS / "merged-not-closed.sh"
REFRESH = HARNESS / "refresh-candidates.sh"

# The enumeration fixture. The pull-request row is filtered, so the issue
# universe is TOTAL - len(PRS); the hits live at the OLD (tail) end so a
# single-page / capped read cannot see them.
TOTAL = 450
PAGE = 100
PRS = [5]
HITS = [11, 27]
EXPECTED_UNIVERSE = TOTAL - len(PRS)

GH_STUB = r'''#!/usr/bin/env python3
"""Recording stub for `gh`, driven by a JSON config next to it.

Models the two facts that make the old enumeration silently partial:
  * `api --paginate` reaches every page (what the fix uses);
  * `issue list --limit N` returns N rows and exits 0 (what the bug used).
Rows are returned NEWEST-FIRST, so the tail (the oldest issues) is exactly
what a capped read drops.
"""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
cfg = json.load(open(os.path.join(here, "config.json")))
log = os.environ.get("GH_STUB_LOG")
if log:
    with open(log, "a") as fh:
        fh.write(json.dumps(sys.argv[1:]) + "\n")

TOTAL, PAGE = cfg["total"], cfg["page"]
PRS, HITS = set(cfg["prs"]), set(cfg["hits"])
TRUNC = cfg.get("truncate_pages")


def row(n):
    r = {"number": n, "title": "issue %d" % n, "labels": [{"name": "P0-blocker"}]}
    if n in PRS:
        r["pull_request"] = {"url": "stub"}
    return r


def page(p):
    """Newest-first: page 1 is numbers TOTAL..TOTAL-PAGE+1, the tail is page N."""
    hi = TOTAL - (p - 1) * PAGE
    lo = max(1, hi - PAGE + 1)
    return [row(n) for n in range(hi, lo - 1, -1)]


args = sys.argv[1:]
path = next((a for a in args if a.startswith("repos/") or a.startswith("search/")), "")

if args[:2] == ["api", "--paginate"] and "timeline" in path:
    num = int(path.split("/issues/")[1].split("/")[0])
    tl = []
    if num in HITS:
        tl = [{"event": "cross-referenced",
               "source": {"issue": {"number": 8000 + num, "title": "PR for %d" % num,
                                    "pull_request": {"merged_at": "2026-10-01T00:00:00Z"}}}}]
    sys.stdout.write(json.dumps(tl)); sys.exit(0)

if args[:2] == ["api", "--paginate"]:
    pages = (TOTAL + PAGE - 1) // PAGE
    if TRUNC:
        pages = min(pages, TRUNC)
    for p in range(1, pages + 1):
        sys.stdout.write(json.dumps(page(p)))
    sys.exit(0)

if "search/issues" in path:
    # The search surface excludes PRs, exactly like `is:issue`.
    print(TOTAL - len(PRS)); sys.exit(0)

if args[:1] == ["issue"] and "list" in args:
    # `gh issue list --limit N`: returns N rows and exit 0 even when the cap
    # is hit, which is why the old code could not tell "400 of 400" from
    # "400 of 1020". Models the pre-fix path so the regression is a clean
    # assertion failure rather than a missing-binary error.
    limit = TOTAL
    for i, a in enumerate(args):
        if a == "--limit":
            limit = int(args[i + 1])
    rows = [r for p in range(1, (TOTAL + PAGE - 1) // PAGE + 1) for r in page(p)
            if "pull_request" not in r]
    sys.stdout.write(json.dumps(rows[:limit])); sys.exit(0)

sys.stderr.write("gh stub: unhandled %r\n" % (args,))
sys.exit(1)
'''


class _Stub:
    """A temp dir holding the recording `gh` stub and its config."""

    def __init__(self, tmp: Path, **cfg) -> None:
        self.dir = tmp
        bin_dir = tmp / "bin"
        bin_dir.mkdir(parents=True)
        config = {"total": TOTAL, "page": PAGE, "prs": PRS, "hits": HITS}
        config.update(cfg)
        (bin_dir / "config.json").write_text(json.dumps(config))
        gh = bin_dir / "gh"
        gh.write_text(GH_STUB)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        self.bin_dir = bin_dir
        self.log = tmp / "gh.log"

    def run(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin_dir}{os.pathsep}{env.get('PATH', '')}"
        env["GH_STUB_LOG"] = str(self.log)
        return subprocess.run([str(MNC), *args], capture_output=True, text=True,
                              cwd=cwd, env=env, timeout=120)

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


class MergedNotClosedEnumeration(unittest.TestCase):
    """The universe must be enumerated to exhaustion, never to a cap."""

    def test_the_whole_universe_is_enumerated_and_the_capped_read_is_gone(self):
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td))
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 0, p.stderr)
            # The OLD tail's issues are found — a `--limit 400` read drops them.
            self.assertIn("#11", p.stdout)
            self.assertIn("#27", p.stdout)
            # ...and the denominator is the REAL universe (PRs filtered out),
            # not the truncation artefact.
            self.assertIn(f"2 of {EXPECTED_UNIVERSE} open issue(s)", p.stdout)
            # The cap cannot come back silently: the enumeration must paginate.
            self.assertTrue(any("--paginate" in c for c in stub.calls()),
                            "merged-not-closed.sh no longer paginates its universe")
            # Nor may it fall back to the capped `issue list` surface.
            self.assertFalse(any(c[:2] == ["issue", "list"] for c in stub.calls()),
                             "merged-not-closed.sh fell back to `gh issue list`")

    def test_a_truncated_sweep_is_refused_never_reported_as_complete(self):
        """A shortfall against the search total must exit 2, not print a partial universe."""
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td), truncate_pages=2)  # only 200 of 449 enumerated
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("PARTIAL", p.stderr)

    def test_a_pull_request_row_is_not_counted_as_an_issue(self):
        """The `issues` endpoint returns PRs; the universe is issues."""
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td))
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn(f"of {TOTAL} open issue(s)", p.stdout)
            self.assertIn(f"of {EXPECTED_UNIVERSE} open issue(s)", p.stdout)


class MergedNotClosedCli(unittest.TestCase):
    """The documented CLI contract: header-only help, and exit 2 — never 1."""

    def test_help_prints_the_header_and_no_source(self):
        p = subprocess.run([str(MNC), "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        self.assertIn("merged-not-closed.sh", p.stdout)
        for source in ("set -u", "REPO=", "LABELS=()", "while [ $# -gt 0 ]", "case \"$1\""):
            self.assertNotIn(source, p.stdout, f"--help leaked source: {source!r}")

    def test_a_missing_option_value_exits_2_not_1(self):
        for opt in ("--label", "--repo"):
            p = subprocess.run([str(MNC), opt], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2, f"{opt} -> {p.returncode}: {p.stderr}")
            self.assertIn("needs a value", p.stderr)

    def test_an_unknown_argument_still_exits_2(self):
        p = subprocess.run([str(MNC), "--nope"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("unknown argument", p.stderr)


class LaneBriefHeredoc(unittest.TestCase):
    """The brief a lane READS must equal the brief in the file.

    Executes the shipped heredoc verbatim. Backticks in the body of an unquoted
    ``<<EOF`` are command substitution, so an unescaped `` `Refs` `` is replaced
    by the (empty) output of running ``Refs`` as a command — silently, exit 0.
    """

    def _render(self) -> str:
        src = LANE.read_text()
        m = re.search(r'^cat > "\$O/logs/prompt-\$N\.txt" <<EOF\n.*?\nEOF\n', src,
                      re.S | re.M)
        self.assertIsNotNone(m, "lane.sh no longer writes the brief with `cat > ... <<EOF`")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "logs").mkdir()
            script = tmp / "brief.sh"
            script.write_text(m.group(0))
            env = dict(os.environ)
            env.update(N="8468", BR="fix/8468-closure-linkage",
                       OR="daniel-ospina/tortoise", O=str(tmp))
            p = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                               cwd=tmp, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            prompt = tmp / "logs" / "prompt-8468.txt"
            self.assertTrue(prompt.exists(), "the shipped heredoc wrote no brief")
            return prompt.read_text()

    def test_backticked_tokens_survive_heredoc_expansion(self):
        brief = self._render()
        self.assertIn("`Refs`", brief)
        self.assertIn("`Still open: <indicator>`", brief)
        # The deleted-token signature the reviewer reproduced: a sentence that
        # has lost its noun ("GitHub never closes an issue from a , and").
        self.assertNotIn("from a , and", brief)
        self.assertNotIn("a bare  is a lane", brief)
        # The rule the brief exists to carry is present and complete.
        self.assertIn('The body MUST contain "Closes #8468"', brief)


class RefreshPoolContract(unittest.TestCase):
    """The shipped pool block and its two consumers must agree (#8469).

    The producer derives TWO files from ONE sort: ``.prio`` (``<number>TAB<tier>``,
    the map ``orch-loop.sh``'s P0 merge-priority override keys on) and ``.pool``
    (bare numbers, what this file's loop reads). A tier in ``.pool`` is a
    malformed argument to the loop (it reads the WHOLE line), so nothing is
    pre-flighted and the backlog never refills; a missing ``.prio`` silently
    disables the override. Both are invisible from the sort alone, so the
    shipped block is EXECUTED and both artefacts asserted.
    """

    def test_the_shipped_pool_block_produces_both_artefacts_its_consumers_read(self):
        src = REFRESH.read_text()
        block = re.search(r'^\{\n  gh issue list.*?^cut -f1 "\$O/\.prio" > "\$O/\.pool"\n',
                          src, re.S | re.M)
        self.assertIsNotNone(
            block, "the shipped pool block no longer writes `.prio` then `cut`-derives `.pool`")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # The three tier queries return "<tier> <number>" (their --jq builds
            # it); the stub prints exactly that, per label.
            (tmp / "bin").mkdir()
            gh = tmp / "bin" / "gh"
            gh.write_text(
                "#!/bin/bash\n"
                "case \"$*\" in\n"
                "  *--label[[:space:]]P0-blocker*) printf '0 7951\\n';;\n"
                "  *--label[[:space:]]P1-severe*)  printf '1 8039\\n1 7936\\n';;\n"
                "  *--label[[:space:]]P2-moderate*) printf '2 1120\\n2 3147\\n2 3329\\n';;\n"
                "  *) exit 1;;\n"
                "esac\n")
            gh.chmod(0o755)
            o = tmp / "o"
            o.mkdir()
            script = tmp / "pool.sh"
            script.write_text(block.group(0))
            env = dict(os.environ)
            env["PATH"] = f"{tmp / 'bin'}{os.pathsep}{env.get('PATH', '')}"
            env["O"] = str(o)
            env["OR"] = "stub/stub"
            p = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                               cwd=tmp, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)

            # .prio = number<TAB>tier, severity-first, age second, dedupon NUMBER.
            prio = (o / ".prio").read_text()
            self.assertEqual(prio,
                             "7951\t0\n7936\t1\n8039\t1\n1120\t2\n3147\t2\n3329\t2\n")
            # .pool = the BARE numbers the loop reads, in the same order.
            pool = (o / ".pool").read_text().split()
            self.assertEqual(pool, ["7951", "7936", "8039", "1120", "3147", "3329"])
            for token in pool:
                self.assertRegex(token, r"^[0-9]+$",
                                 f".pool carries a non-number token {token!r}")
            # The override's own lookup, verbatim: head=7951 must resolve to tier 0.
            head = pool[0]
            tier = subprocess.run(
                ["awk", "-F\\t", "-v", f"n={head}", "$1==n{print $2; exit}",
                 str(o / ".prio")], capture_output=True, text=True).stdout.strip()
            self.assertEqual(tier, "0", "orch-loop.sh's P0 override cannot read .prio")

            # And the shipped loop, driven with that .pool, must mark candidates
            # checked. A malformed token enters the `*)` arm and marks NOTHING.
            loop = re.search(r"^n=0\n.*?^done < \"\$O/\.pool\"\n", src, re.S | re.M)
            self.assertIsNotNone(loop, "the refresh loop body moved")
            rl = tmp / "rl"
            (rl / "fakesc" / ".venv" / "bin").mkdir(parents=True)
            fake = rl / "fakesc" / ".venv" / "bin" / "python"
            # Discriminating stub: the issue number ($2 — the loop passes
            # `collision_preflight.py <N> --repo .`) must be a BARE number, so a
            # tier prefix cannot ride through as a substring match.
            fake.write_text("#!/bin/bash\n"
                            "case \"$2\" in *[!0-9]*|\"\") exit 3;; esac\n"
                            "echo 'VERDICT: CLEAN'\n")
            fake.chmod(0o755)
            (rl / ".pool").write_text((o / ".pool").read_text())
            for name in ("checked.txt", "candidates.txt", "dispatched.txt"):
                (rl / name).write_text("")
            loop_script = tmp / "loop.sh"
            loop_script.write_text(loop.group(0))
            env["O"] = str(rl)
            env["LIMIT"] = "3"
            env["SC"] = str(rl / "fakesc")
            p = subprocess.run(["bash", str(loop_script)], capture_output=True, text=True,
                               cwd=tmp, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual((rl / "checked.txt").read_text().split(),
                             ["7951", "7936", "8039"],
                             "the shipped loop did not consume the shipped .pool")
            self.assertEqual((rl / "candidates.txt").read_text().split(),
                             ["7951", "7936", "8039"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
