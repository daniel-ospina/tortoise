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
* ``refresh-candidates.sh`` marked an issue checked even when the pre-flight
  emitted NO verdict, blacklisting it forever (``checked.txt=63`` vs
  ``candidates.txt=9`` live). Only a real ``CLEAN`` marks it now; a no-verdict
  pre-flight AND a transient COLLISION both leave the issue for the next pass.
* The no-verdict arm must ALSO be bounded and must not consume the pass budget:
  counting every entry against ``LIMIT`` let a few persistently non-answering
  entries at the pool head re-spend the whole pass every time, starving the
  entries behind them so the backlog could never refill. The budget counts
  ANSWERED candidates only, and ``RETRY_MAX`` bounds the retries so a
  permanently failing head entry eventually leaves the candidate set.
* ``merged-not-closed.sh``'s PARTIAL guard was skipped when the ``search``
  surface could not be queried: an unusable total left ``floor`` as ``None`` and
  a truncated sweep printed as a complete universe with exit 0. An unusable
  total is now an unusable surface (exit 2).
* ``merged-not-closed.sh`` broke its own "never 1" contract on every RUNTIME
  error path: a 200 with a non-JSON/truncated page (the #8246 mode), an item
  with no ``number``, a ``gh`` that vanished mid-sweep, and a missing ``python3``
  all exited 1 (traceback / 127). Every failure now maps to the documented 2.
* ``orch-loop.sh``'s P0 merge-priority override (BP0, default-ON
  ``P0_BYPASS_MERGE_PRIORITY=1``) clears only the merge-priority gate when a
  ``P0-blocker`` is at the head of the backlog. A grep for its text stayed green
  when the block was deleted or its tab key changed, so the shipped FUNCTION is
  executed here.
* ``merge-one.sh``'s reviewer prompt told its agent to run the test suite with
  ``.venv/bin/python -m pytest`` in a checkout that has **no** ``.venv`` — and,
  after #8484 gave the reviewer its own detached worktree, to ``uv sync`` one
  first. So the agent went hunting for an interpreter (``find $HOME -name
  pyvenv.cfg``, killed at >120s) and 35% of runs ended at the 1800s cap with no
  verdict at all. The prompt is now a shipped ``reviewer_prompt()`` FUNCTION and
  these tests execute it, so a reflow that restores an execution instruction
  fails.
* ``merge-one.sh`` shared ONE ``*)`` arm for a real ``ISSUES`` verdict and for a
  review that produced **no** ``CLEAN``/``ISSUES`` line at all (a stall, a
  crash), and wrote ``state/needsfix-<PR>-<head>`` for both. A pure stall thus
  authorised a fixer for a review that never happened, spent one of
  ``MAX_FIX_TRIES`` on it, and three of them PARKED the PR "needs a human". The
  shipped ``verdict_kind()``/``apply_verdict_marker()`` pair is executed here, AND
  the shipped ``case "$(verdict_kind "$LAST")" in`` DISPATCH that consumes it — the
  arm the loop actually obeys. Driving only the helpers would stay green if the
  marker were written back into the verdict-less arm, because the helper's kind
  guard is never exercised by production (its one call site passes the literal
  ``"issues"``): only a real ``ISSUES`` verdict writes the marker (#8602,
  agent-infra#1741, #8612 review P2).

Run under pytest:  python3 -m pytest tests/test_orchestrator_harness.py -q
Run standalone:    python3 tests/test_orchestrator_harness.py
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "operations" / "orchestrator-harness"
LANE = HARNESS / "lane.sh"
MNC = HARNESS / "merged-not-closed.sh"
REFRESH = HARNESS / "refresh-candidates.sh"
LOOP = HARNESS / "orch-loop.sh"
MERGE = HARNESS / "merge-one.sh"


def _extract_function(path: Path, name: str) -> str:
    """Return the shipped ``name() { ... }`` block, verbatim, or fail loudly.

    Drives the SHIPPED function — never a re-implementation of its logic, which
    would pass on the broken copy too.
    """
    m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", path.read_text(), re.S | re.M)
    assert m, f"{path.name} no longer defines {name}()"
    return m.group(0)


def _extract_verdict_dispatch() -> str:
    """Return the shipped ``case "$(verdict_kind "$LAST")" in ... esac`` block.

    The helper pair is prepended, so the block runs the SHIPPED classifier and the
    SHIPPED marker writer: the DECISION the loop obeys is the arm taken here, and a
    test that drives only the helpers cannot observe it (#8612 review P2 — a marker
    re-added to the verdict-less arm, or removed from the ``issues`` arm, kept a
    29-test suite fully green).
    """
    m = re.search(r'^case "\$\(verdict_kind "\$LAST"\)" in\n.*?^esac\n',
                  MERGE.read_text(), re.S | re.M)
    assert m, 'merge-one.sh no longer dispatches on `case "$(verdict_kind "$LAST")" in`'
    return (_extract_function(MERGE, "verdict_kind")
            + _extract_function(MERGE, "apply_verdict_marker")
            + m.group(0))

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
MALFORMED = cfg.get("malformed")
MISSING_NUMBER = cfg.get("missing_number")


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
    if MALFORMED:
        # A 200 with a non-JSON/truncated body — the #8246 failure mode. `gh` exits 0,
        # so the shell cannot tell it from a real page; the parser must refuse (exit 2)
        # rather than let a traceback exit 1.
        sys.stdout.write('[{"number": 11, "title": "truncated page"')
        sys.exit(0)
    if MISSING_NUMBER:
        # A well-formed JSON page whose single row has no `number` key.
        sys.stdout.write(json.dumps([{"title": "no number here"}]))
        sys.exit(0)
    pages = (TOTAL + PAGE - 1) // PAGE
    if TRUNC:
        pages = min(pages, TRUNC)
    for p in range(1, pages + 1):
        sys.stdout.write(json.dumps(page(p)))
    sys.exit(0)

if "search/issues" in path:
    if cfg.get("search_fail"):
        # The search surface has a much stricter rate limit than the REST enumeration, so it
        # can fail while `api --paginate` succeeds. Models the fail-open #8469 review P2.
        sys.stderr.write("gh stub: search surface unavailable (HTTP 403)\n")
        sys.exit(1)
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

    def test_a_malformed_enumeration_page_exits_2_not_1(self):
        """A 200 with a non-JSON body is a surface that could not be queried, not exit 1.

        The header's contract is exit 2 — never 1 — so a caller cannot mistake "the API
        failed" for "nothing to drain". The enumeration parse used to sit outside any
        try/except, so a truncated page (the #8246 mode) escaped as a traceback and
        python exited 1 (#8469 review P2).
        """
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td), malformed=True)
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("could not be queried", p.stderr)

    def test_an_item_without_a_number_exits_2_not_1(self):
        """A malformed row is the same class: refuse with 2, never a KeyError exit 1."""
        with tempfile.TemporaryDirectory() as td:
            # total=2, one PR row -> the search floor is 1, so the enumeration is not
            # short and the bad row is actually reached.
            stub = _Stub(Path(td), total=2, prs=[5], hits=[11], missing_number=True)
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("could not be queried", p.stderr)

    def test_a_pull_request_row_is_not_counted_as_an_issue(self):
        """The `issues` endpoint returns PRs; the universe is issues."""
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td))
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn(f"of {TOTAL} open issue(s)", p.stdout)
            self.assertIn(f"of {EXPECTED_UNIVERSE} open issue(s)", p.stdout)

    def test_an_unusable_search_total_is_refused_not_treated_as_complete(self):
        """A search surface that cannot be queried must not disable the partial guard.

        `search/issues` has its own, much stricter rate limit than the REST enumeration, so it
        can 403 while `api --paginate` succeeds. Pre-fix, both totals came back empty, `floor`
        stayed `None`, the `floor is not None` test skipped the guard, and a truncated sweep was
        printed as a complete universe with exit 0 — the fail-open the guard exists to prevent
        (#8469 review P2). An unusable total is an unusable surface: refuse.
        """
        with tempfile.TemporaryDirectory() as td:
            stub = _Stub(Path(td), truncate_pages=2, search_fail=True)
            p = stub.run("--label", "P0-blocker")
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("search surface could not be queried", p.stderr)
            # It must not present the 200-row partial sweep as an answer.
            self.assertNotIn("of 200 open issue(s)", p.stdout)


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
            # The shipped CONSUMER of that `.prio` — not a copy of its awk. Extract
            # orch-loop.sh's override and call it with the producer's real output: a
            # re-implementation here would stay green if the shipped lookup broke.
            # `candidates.txt` is what the override reads its head from.
            (o / "candidates.txt").write_text((o / ".pool").read_text())
            fn = _extract_function(LOOP, "p0_merge_priority_override")
            ovlog = tmp / "orch.log"
            drive = tmp / "override.sh"
            drive.write_text(fn + "\n" + 'p0_merge_priority_override "$1" "$2"\n')
            ov_env = dict(env)
            ov_env["LOG"] = str(ovlog)
            r = subprocess.run(["bash", str(drive), "merge-priority (19 open PRs)", "19"],
                               capture_output=True, text=True, env=ov_env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(
                r.stdout, "",
                "orch-loop.sh's P0 override did not clear the gate for a tier-0 head")
            self.assertIn("OVERRIDDEN for a P0-blocker", ovlog.read_text())

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

    def test_a_pre_flight_with_no_verdict_is_not_marked_checked(self):
        """A pre-flight that ERRORED is not a verdict (#8469 §2).

        The fix's whole substance: `checked.txt` is written only on a CLEAN
        verdict. With the pre-fix unconditional write, issue 10 (no VERDICT
        line) is blacklisted forever and never re-flighted; here it must stay for the
        next pass while the later CLEAN candidate is still queued. The existing
        fixtures all print `VERDICT: CLEAN`, so neither suite took this arm.
        """
        src = REFRESH.read_text()
        loop = re.search(r'^n=0\n.*?^done < "\$O/\.pool"\n', src, re.S | re.M)
        self.assertIsNotNone(loop, "the refresh loop body moved")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rl = tmp / "rl"
            (rl / "fakesc" / ".venv" / "bin").mkdir(parents=True)
            fake = rl / "fakesc" / ".venv" / "bin" / "python"
            # Issue 10: the pre-flight ERRORS and emits NO `VERDICT` line.
            # Issue 20: a real CLEAN verdict in the same pass.
            fake.write_text("#!/bin/bash\n"
                            "case \"$2\" in\n"
                            "  10) echo 'boom: TypeError' >&2; exit 1;;\n"
                            "  *[!0-9]*|\"\") exit 3;;\n"
                            "esac\n"
                            "echo 'VERDICT: CLEAN'\n")
            fake.chmod(0o755)
            (rl / ".pool").write_text("10\n20\n")
            for name in ("checked.txt", "candidates.txt", "dispatched.txt"):
                (rl / name).write_text("")
            loop_script = tmp / "loop.sh"
            loop_script.write_text(loop.group(0))
            env = dict(os.environ)
            env.update(O=str(rl), LIMIT="3", SC=str(rl / "fakesc"))
            p = subprocess.run(["bash", str(loop_script)], capture_output=True, text=True,
                               cwd=tmp, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            checked = (rl / "checked.txt").read_text().split()
            candidates = (rl / "candidates.txt").read_text().split()
            self.assertNotIn("10", checked,
                             "a pre-flight that emitted NO verdict was marked checked")
            self.assertNotIn("10", candidates)
            self.assertIn("20", checked, "the later CLEAN candidate was not queued")
            self.assertIn("20", candidates)
            self.assertIn("?#10 no verdict", p.stdout + p.stderr)

    def _drive_loop(self, tmp: Path, pool: str, stub_body: str, limit: str = "3",
                    runs: int = 1, retry_max: str | None = None):
        """Extract the shipped refresh loop and drive it against a stub pre-flight.

        Returns ``(procs, states)`` where ``states`` snapshots
        ``(checked, candidates)`` after EACH run — the retry counter persists in
        ``$O/state``, so a multi-run test must observe the intermediate passes.
        """
        loop = re.search(r'^n=0\n.*?^done < "\$O/\.pool"\n', REFRESH.read_text(),
                         re.S | re.M)
        self.assertIsNotNone(loop, "the refresh loop body moved")
        rl = tmp / "rl"
        (rl / "fakesc" / ".venv" / "bin").mkdir(parents=True)
        fake = rl / "fakesc" / ".venv" / "bin" / "python"
        fake.write_text(stub_body)
        fake.chmod(0o755)
        (rl / ".pool").write_text(pool)
        for name in ("checked.txt", "candidates.txt", "dispatched.txt"):
            (rl / name).write_text("")
        loop_script = tmp / "loop.sh"
        loop_script.write_text(loop.group(0))
        env = dict(os.environ)
        env.update(O=str(rl), LIMIT=limit, SC=str(rl / "fakesc"))
        if retry_max is not None:
            env["RETRY_MAX"] = retry_max
        procs, states = [], []
        for _ in range(runs):
            procs.append(subprocess.run(["bash", str(loop_script)], capture_output=True,
                                        text=True, cwd=tmp, env=env))
            states.append(((rl / "checked.txt").read_text().split(),
                           (rl / "candidates.txt").read_text().split()))
        return procs, states

    def test_a_non_verdict_head_does_not_starve_the_entries_behind_it(self):
        """The pass budget counts ANSWERED candidates, not entries walked.

        Pre-fix, ``n`` incremented for every entry examined, so a few persistently
        non-answering entries at the pool head spent the whole ``LIMIT`` and the CLEAN
        entries behind them were never reached — ``candidates.txt`` never refilled and
        so the backlog could never refill (#8469 review P2). The failed entries must
        stay unchecked (a transient failure is not a verdict) while the CLEAN entries
        behind them are queued in the SAME pass.
        """
        with tempfile.TemporaryDirectory() as td:
            stub = ("#!/bin/bash\n"
                    "case \"$2\" in\n"
                    "  10|20) echo 'boom: TypeError' >&2; exit 1;;\n"
                    "  *[!0-9]*|\"\") exit 3;;\n"
                    "esac\n"
                    "echo 'VERDICT: CLEAN'\n")
            procs, states = self._drive_loop(
                Path(td), "10\n20\n30\n40\n", stub, limit="2")
            self.assertEqual([p.returncode for p in procs], [0])
            checked, candidates = states[-1]
            self.assertEqual(checked, ["30", "40"],
                             "the CLEAN entries behind a non-verdict head were starved")
            self.assertEqual(candidates, ["30", "40"])
            self.assertNotIn("10", checked)
            self.assertNotIn("20", checked)

    def test_a_persistently_non_verdict_entry_is_retried_then_bounded(self):
        """A bounded retry eventually leaves the candidate set (the cursor must advance).

        Pre-fix there was no bound at all: a pre-flight that never answered kept the same
        entry at the head of every pass, so the entries behind it were never examined and
        the backlog could never refill (#8469 review P2). After ``RETRY_MAX`` consecutive
        attempts the entry is marked checked — bounded retries, not permanent blacklisting
        on the first transient failure.
        """
        with tempfile.TemporaryDirectory() as td:
            stub = ("#!/bin/bash\n"
                    "case \"$2\" in *[!0-9]*|\"\") exit 3;; esac\n"
                    "echo 'boom: no verdict' >&2; exit 1\n")
            procs, states = self._drive_loop(
                Path(td), "10\n", stub, limit="2", runs=3, retry_max="3")
            self.assertEqual([p.returncode for p in procs], [0, 0, 0])
            self.assertEqual(states[0][0], [],
                             "an unanswerable entry was blacklisted on attempt 1")
            self.assertEqual(states[1][0], [],
                             "blacklisted before RETRY_MAX attempts")
            self.assertEqual(states[2][0], ["10"],
                             "the retry bound never advanced the cursor")
            self.assertEqual(states[2][1], [])
            self.assertIn("marked checked after 3 attempts",
                          procs[-1].stdout + procs[-1].stderr)


    def test_a_collision_is_retried_not_blacklisted(self):
        """A COLLISION is transient — the colliding work lands — so it must be retried.

        `checked.txt` used to be written before the verdict was read, so a legitimate
        COLLISION consumed the issue permanently (agent-infra#1716). With the fix, the
        first pass records nothing and the second pass queues the issue once the peer's
        work has landed.
        """
        stub = ("#!/bin/bash\n"
                "case \"$2\" in *[!0-9]*|\"\") exit 3;; esac\n"
                "S=\"$(dirname \"$0\")/.pass\"\n"
                "if [ -f \"$S\" ]; then echo 'VERDICT: CLEAN'; "
                "else : > \"$S\"; echo 'VERDICT: COLLISION'; fi\n")
        with tempfile.TemporaryDirectory() as td:
            procs, states = self._drive_loop(Path(td), "10\n", stub, limit="5", runs=2)
            self.assertEqual([p.returncode for p in procs], [0, 0])
            self.assertNotIn("10", states[0][0], "a COLLISION blacklisted the issue")
            self.assertNotIn("10", states[0][1])
            self.assertIn("10", states[1][0], "the re-attempted COLLISION was not queued")
            self.assertIn("10", states[1][1])


class MergePriorityOverride(unittest.TestCase):
    """`orch-loop.sh`'s P0 merge-priority override is executed, not grepped.

    It clears ONLY the merge-priority reason when a `P0-blocker` is at the head of
    the backlog, and it is default-ON (`P0_BYPASS_MERGE_PRIORITY=1`). A regression
    that deletes the block or changes its tab key silently restores the old gate,
    and a grep for the block's text stays green through both mutations — so every
    assertion drives the shipped function.
    """

    WHY = "merge-priority (19 open PRs)"

    def _run(self, tmp: Path, head: str, prio: str | None,
             bypass: str | None = None):
        fn = _extract_function(LOOP, "p0_merge_priority_override")
        o = tmp / "o"
        o.mkdir(exist_ok=True)
        (o / "candidates.txt").write_text(head + "\n")
        if prio is not None:
            (o / ".prio").write_text(prio)
        log = tmp / "orch.log"
        log.write_text("")
        script = tmp / "drive.sh"
        script.write_text(fn + "\n" + 'p0_merge_priority_override "$1" "$2"\n')
        env = dict(os.environ)
        env.update(O=str(o), LOG=str(log))
        if bypass is not None:
            env["P0_BYPASS_MERGE_PRIORITY"] = bypass
        p = subprocess.run(["bash", str(script), self.WHY, "19"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout, log.read_text()

    def test_a_p0_backlog_head_clears_the_merge_priority_gate(self):
        with tempfile.TemporaryDirectory() as td:
            out, log = self._run(Path(td), "7951", "7951\t0\n")
            self.assertEqual(out, "")
            self.assertIn("OVERRIDDEN for a P0-blocker", log)
            self.assertIn("7951", log)

    def test_a_non_p0_backlog_head_leaves_the_gate_closed(self):
        with tempfile.TemporaryDirectory() as td:
            out, log = self._run(Path(td), "8039", "8039\t1\n")
            self.assertEqual(out, self.WHY)
            self.assertNotIn("OVERRIDDEN", log)

    def test_the_kill_switch_restores_the_unconditional_gate(self):
        with tempfile.TemporaryDirectory() as td:
            out, log = self._run(Path(td), "7951", "7951\t0\n", bypass="0")
            self.assertEqual(out, self.WHY)
            self.assertNotIn("OVERRIDDEN", log)

    def test_a_missing_prio_map_suppresses_the_override(self):
        # Fail-safe: an absent/partial .prio must never invent a P0.
        with tempfile.TemporaryDirectory() as td:
            out, log = self._run(Path(td), "7951", None)
            self.assertEqual(out, self.WHY)
            self.assertNotIn("OVERRIDDEN", log)

    def test_the_lookup_is_tab_keyed_like_the_producer(self):
        # A space-separated .prio (the `-F' '` mutation) must NOT match: the shipped
        # producer writes `<number>TAB<tier>`.
        with tempfile.TemporaryDirectory() as td:
            out, log = self._run(Path(td), "7951", "7951 0\n")
            self.assertEqual(out, self.WHY)
            self.assertNotIn("OVERRIDDEN", log)


class ReviewerPromptContract(unittest.TestCase):
    """#8602: the reviewer prompt must not send its agent hunting for an interpreter.

    The reviewer's cwd is a DISPOSABLE detached worktree (``merge-one.sh``,
    #8484) — it has no ``.venv``. The prompt used to tell the reviewer to run
    ``.venv/bin/python -m pytest`` there, and (after #8484) to ``uv sync``
    first. Either way the agent goes looking: measured on the live box, a
    reviewer's last tool call was ``find ... -name pyvenv.cfg`` and it then sat
    silent until the 1800s cap killed it, with no verdict. The prompt is now a
    shipped FUNCTION, and this drives it, so a reflow of the heredoc that
    restores an execution instruction fails here.
    """

    def _prompt(self) -> str:
        fn = _extract_function(MERGE, "reviewer_prompt")
        env = dict(os.environ)
        env.update(OR="daniel-ospina/tortoise", PR="42", BR="feat/x-tm",
                   REPO="/tmp/shared-main")
        p = subprocess.run(["bash", "-c", fn + "\nreviewer_prompt\n"],
                           capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_the_shipped_prompt_forbids_executing_or_provisioning_tests(self):
        out = self._prompt()
        for token in ("uv sync", ".venv", "pytest", "TORTOISE_TEST_CARVE_OUT"):
            self.assertNotIn(token, out, f"the reviewer is still told to run tests ({token!r})")
        self.assertIn("DO NOT RUN THE TEST SUITE", out)

    def test_the_shipped_prompt_names_the_walk_bound_and_the_writers_job(self):
        out = self._prompt()
        self.assertIn("FIXER's job", out)
        self.assertIn("$HOME", out)

    def test_the_verdict_contract_is_still_required(self):
        # The fix must delete the venv hunt, NOT weaken the artifact contract:
        # the review is the merge gate's audit evidence, and its final line is
        # still exactly CLEAN <url> or ISSUES <n>.
        out = self._prompt()
        self.assertIn("gh pr comment", out)
        self.assertIn("CLEAN <", out)
        self.assertIn("ISSUES <n>", out)
        self.assertIn("do not fix code", out)


class VerdictMarkerContract(unittest.TestCase):
    """#8602 / agent-infra#1741: a verdict-less run must not authorise a fixer.

    ``state/needsfix-<PR>-<head>`` is the only thing that makes ``orch-loop.sh``
    spend a ``MAX_FIX_TRIES`` attempt on a fixer at that head. A reviewer that
    times out produces no ``CLEAN``/``ISSUES`` line, so the old single ``*)`` arm
    wrote the marker for a stall too — three stalls parked the PR "needs a
    human" with the reviewer's findings never addressed. Both halves of the
    shipped decision are executed here, never re-implemented.
    """

    def _kind(self, last: str) -> str:
        fn = _extract_function(MERGE, "verdict_kind")
        p = subprocess.run(["bash", "-c", fn + '\nverdict_kind "$1"', "bash", last],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.strip()

    def test_a_clean_verdict_is_clean(self):
        self.assertEqual(self._kind("CLEAN https://github.com/o/r/pull/1#issuecomment-9"), "clean")

    def test_an_issues_verdict_is_issues(self):
        self.assertEqual(self._kind("ISSUES 3"), "issues")

    def test_no_final_line_is_none_not_issues(self):
        for last in ("", "   ", "some other output"):
            self.assertEqual(self._kind(last), "none", last)

    def _marker(self, kind: str, head: str):
        with tempfile.TemporaryDirectory() as td:
            o = Path(td) / "o"
            (o / "state").mkdir(parents=True)
            fn = _extract_function(MERGE, "apply_verdict_marker")
            env = dict(os.environ)
            env.update(O=str(o), PR="42")
            p = subprocess.run(["bash", "-c", fn + '\napply_verdict_marker "$1" "$2"',
                                "bash", kind, head], capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stderr)
            marker = o / "state" / f"needsfix-42-{head}" if head else None
            return p.stdout.strip(), (marker.exists() if marker is not None else False)

    def test_an_issues_verdict_writes_the_marker_the_loop_reads(self):
        out, exists = self._marker("issues", "deadbeef")
        self.assertTrue(exists)
        self.assertIn("needsfix-42-deadbeef", out)

    def test_a_verdict_less_run_writes_no_marker(self):
        # The helper's own guard. The regression it protects is only observable
        # through the DISPATCH — see VerdictDispatchContract below.
        for kind in ("none", "clean"):
            out, exists = self._marker(kind, "deadbeef")
            self.assertFalse(exists, f"kind={kind} wrote an authorising marker")
            self.assertEqual(out, "")

    def test_an_issues_verdict_with_no_head_writes_no_marker(self):
        # HEAD is empty only when gh could not resolve the PR head; a marker keyed
        # on an empty head is unreadable by the loop and must not be written.
        out, exists = self._marker("issues", "")
        self.assertFalse(exists)
        self.assertEqual(out, "")


class VerdictDispatchContract(unittest.TestCase):
    """#8612 review P2: the SHIPPED ``case`` arm is the decision the loop obeys.

    ``apply_verdict_marker`` has exactly one production call site, in the ``issues``
    arm, always passed the literal ``"issues"`` — so its kind guard can never refuse
    anything in production. The rule that produced the false park lives in the ARM:
    re-adding the marker call to the verdict-less arm (the pre-fix behaviour,
    re-expressed through the new helper) must fail here. The whole shipped block is
    executed against a stubbed ``gh``/``record-review.sh`` and a temp ``$O`` state
    dir, so nothing here re-implements the routing.
    """

    def _run_dispatch(self, last: str):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            o = root / "o"
            (o / "state").mkdir(parents=True)
            (o / "logs").mkdir()
            scripts = root / "home" / ".pi" / "agent" / "scripts"
            scripts.mkdir(parents=True)
            rec = scripts / "record-review.sh"
            rec.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$REC_LOG"\nexit 0\n')
            rec.chmod(0o755)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            gh = bin_dir / "gh"
            gh.write_text('#!/bin/sh\n'
                          'case "$*" in\n'
                          '  *"pr checks"*) echo SUCCESS ;;\n'
                          '  *"pr merge"*) ;;\n'
                          '  *) echo "gh stub: unhandled $*" >&2; exit 1 ;;\n'
                          'esac\n'
                          'exit 0\n')
            gh.chmod(0o755)
            rec_log = root / "record.log"
            env = dict(os.environ)
            env.update(O=str(o), PR="42", HEAD="deadbeef", OR="daniel-ospina/tortoise",
                       BR="feat/x-tm", REPO=str(root / "checkout"),
                       L=str(o / "logs" / "merge-42"), HOME=str(root / "home"),
                       REC_LOG=str(rec_log), LAST=last,
                       PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}")
            p = subprocess.run(["bash", "-c", "set -u\n" + _extract_verdict_dispatch()],
                               capture_output=True, text=True, env=env, timeout=60)
            return (p, (o / "state" / "needsfix-42-deadbeef").exists(),
                    (o / "state" / "merged-42.done").exists(),
                    rec_log.read_text() if rec_log.exists() else "")

    def test_a_verdict_less_run_takes_the_non_authorising_arm(self):
        # THE regression, driven through the shipped dispatch: a marker in this arm
        # books a fixer attempt for a review that never happened, and three of them
        # park the PR "needs a human" with the findings never addressed.
        for last in ("", "   ", "some other output"):
            p, marker, merged, rec = self._run_dispatch(last)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(marker, f"LAST={last!r} authorised a fixer for a non-verdict")
            self.assertFalse(merged)
            self.assertEqual(rec, "")

    def test_an_issues_verdict_takes_the_arm_that_authorises_the_fixer(self):
        p, marker, merged, rec = self._run_dispatch("ISSUES 3")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(marker, "a real ISSUES verdict must authorise a fixer")
        self.assertFalse(merged)
        self.assertEqual(rec, "")

    def test_a_clean_verdict_takes_the_arm_that_records_and_merges(self):
        p, marker, merged, rec = self._run_dispatch(
            "CLEAN https://github.com/daniel-ospina/tortoise/pull/8612#issuecomment-424242")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(marker, "a CLEAN verdict must not authorise a fixer")
        self.assertTrue(merged, "a CLEAN verdict must record the review and merge")
        self.assertIn("42 deadbeef clean", rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)
