"""Hermetic regression tests for the TWO scripts retained in ``operations/orchestrator-harness/``.

``tests/test_orchestrator_harness.py`` (904 lines, 32 tests) was retired with the copy it drove —
but eleven of its tests drove ``merge-one.sh``, the file this retirement deliberately KEEPS in place
(the #8484 detached-reviewer sandbox, plus the #8602 ``reviewer_prompt()`` and #8612
``verdict_kind()`` / ``apply_verdict_marker()`` pair), and nine drove
``merged-not-closed.sh``, which has **no landed copy anywhere else** (agent-infra `main` has no path
matching ``merged-not-closed``; agent-infra#2160, which carries it plus its own hermetic test, is
still OPEN). Review finding: *"keeping the code and deleting its guard is the one combination that
leaves nothing to notice a regression."*

So the coverage that belongs to the two RETAINED files stays here, and only that coverage:

* ``merged-not-closed.sh`` — the universe must be enumerated to exhaustion (a capped
  ``gh issue list --limit N`` read dropped ~620 of ~1,020 open issues and printed the truncated count
  as the whole repo; the truncation lands on the OLDEST issues, the stranded population the
  instrument exists to find); a shortfall against the search total, a malformed page, a row without
  ``number`` and an unusable search surface must all be exit **2** — never 1, never a printed
  partial universe; ``--help`` prints the header only; a missing option value is 2.
* ``merge-one.sh`` — the reviewer prompt must not tell its agent to provision a venv or run the
  suite (#8602: 35% of reviewer runs ended at the 1800 s cap with no verdict, the last tool call a
  ``find $HOME``); a verdict-LESS run must not authorise a fixer (#8612: three stalls parked a PR
  "needs a human" with the findings never addressed).

Every assertion drives the SHIPPED script text (``bash -c`` on the extracted function, or the
retained shell script itself), never a re-implementation of its logic — a re-implementation would
pass on the broken copy too. No network, no DB, no Docker: ``gh`` is a recording stub.

Run: python3 -m pytest tests/test_orchestrator_harness_retained.py -q
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
MNC = HARNESS / "merged-not-closed.sh"
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
