"""Hermetic tests for tools/closes_landing.py (#10597).

No network, no Docker, no FalkorDB. Every case runs the tool's PURE core against
in-memory PR records, so the judgement is exercised without a GitHub call; two
cases drive the CLI offline (`--body-file`) to prove the exit codes a caller
branches on.

The defect this guards, measured on 2026-11-02 (#10597): `PR #10568` carried
`Closes #10541` with base `act-10465` — another lane's branch. GitHub fires a
closing keyword only on a merge into the DEFAULT branch, so the merge closed
nothing: `#10541` stayed `open` with `closed_at: null` while the PR read
`MERGED`. The work only lands when `PR #10519` (base `main`) merges, and #10519's
body has no `Closes #10541` — so the issue would stay open even then.

The two falsification directions the tool must satisfy:

  (a) IT FLAGS the inert shape — a closing keyword on a PR whose base is not the
      default branch, where the PR that terminates the stack does not carry the
      same keyword. One hop AND two hops, because the keyword only fires at the
      top of the stack.
  (b) IT DOES NOT FLAG a correct one — a PR on the default branch, and a stacked
      PR whose carrier DOES re-state the keyword. An over-eager instrument that
      fires on every stacked PR would be turned off, and would take the real
      finding with it.
  (c) IT DOES NOT PASS what it cannot measure — a base branch with no PR, and a
      cycle, are `unresolved`, never `lands`. A missing carrier PR is the state
      a stack is in *before* its carrier is opened, which is exactly when a
      false pass is most expensive.

Run standalone:      python3 tests/test_closes_landing.py
Run under pytest:    python3 -m pytest tests/test_closes_landing.py -q
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

# A MODULE-SCOPE hard import, deliberately — never `pytest.importorskip` (#10634
# review P2). The renderer cross-check below is the ONLY mitigation for this
# file's hand-rolled copy of GitHub's grammar (`tools/closes_landing.py:76-84`:
# "That test is the mitigation, and it is the only thing that is"), so a missing
# dependency must be a collection ERROR, not a `1 skipped` that leaves the leg
# green — which is exactly the re-staleable "checked by hand" claim #8379 removed
# by declaring markdown-it-py on both install paths. Same import, same reason, as
# the repo precedent `tests/test_blog_authoring_doc.py:21`.
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "closes_landing.py"

DEFAULT = "main"
SIBLING = "act-10465"


def _load_module():
    """Load `tools/closes_landing.py` by path — `tools/` is not a package, and this
    module must be importable exactly as the CLI runs it."""
    spec = importlib.util.spec_from_file_location("_closes_landing_under_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cl = _load_module()


@pytest.fixture(autouse=True)
def _fresh_run_budget():
    """Every test starts with the aggregate `gh` budget UNSPENT.

    It is module state, so without this a test that deliberately spends it (the
    budget tests below) would make every later test's measurement depend on test
    ORDER — and the order a full run happens to use is not a contract.
    """
    cl._run_budget_started = None
    yield
    cl._run_budget_started = None


def _pr(n, base, body="", head=None, state="OPEN", merged_at=None):
    return cl.PullRequest(number=n, base=base, body=body,
                          head=head or f"branch-{n}", state=state,
                          merged_at=merged_at)


def _by_head(*prs):
    return {pr.head: pr for pr in prs}


# ── (b) the shapes that must NOT be flagged ───────────────────────────────────

def test_a_pr_on_the_default_branch_lands():
    pr = _pr(1, DEFAULT, "Closes #100")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "lands"
    assert finding.issue == 100
    assert finding.carrier == 1


def test_a_stacked_pr_whose_carrier_restates_the_keyword_lands():
    stacked = _pr(2, SIBLING, "Closes #100")
    carrier = _pr(3, DEFAULT, "Closes #100", head=SIBLING)
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "lands"
    assert finding.carrier == 3


def test_a_pr_with_no_closing_keyword_produces_no_finding():
    pr = _pr(4, SIBLING, "Refs #100 — this does not claim to close anything")
    assert cl.classify(pr, _by_head(pr), DEFAULT) == []


# ── (a) the shapes that MUST be flagged ──────────────────────────────────────

def test_the_measured_10568_shape_is_inert():
    """PR #10568: base `act-10465`, body `Closes #10541`; its carrier #10519 has
    no `Closes #10541`. This is the instance #10597 measured."""
    stacked = _pr(10568, SIBLING, "fix(10541): the census counts the unresolved producers\n\nCloses #10541\n")
    carrier = _pr(10519, DEFAULT, "measure(5090): score the epic's four DoD bullets at HEAD\n",
                  head=SIBLING)
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert"
    assert finding.issue == 10541
    assert finding.carrier == 10519
    # The detail has to name the REMEDY, not just the fault: a warning that says
    # only "this is wrong" makes the reader derive the fix.
    assert "Re-state the keyword on PR #10519" in finding.detail


def test_a_two_hop_stack_is_judged_at_the_top_not_the_middle():
    """`bottom(main) <- mid <- top(Closes)`: the keyword on `mid` would be inert
    even though `mid`'s base looks like it belongs to the stack."""
    bottom = _pr(10, DEFAULT, "no keyword", head="bottom-branch")
    mid = _pr(11, "bottom-branch", "Closes #100", head=SIBLING)
    top = _pr(12, SIBLING, "Closes #100")
    (finding,) = cl.classify(top, _by_head(top, mid, bottom), DEFAULT)
    assert finding.verdict == "inert"
    assert finding.carrier == 10, "the carrier is the PR that targets the default branch"


def test_a_two_hop_stack_lands_when_the_top_pr_restates_the_keyword():
    bottom = _pr(20, DEFAULT, "Closes #100", head="bottom-branch")
    mid = _pr(21, "bottom-branch", "", head="mid-branch")
    top = _pr(22, "mid-branch", "Closes #100")
    (finding,) = cl.classify(top, _by_head(top, mid, bottom), DEFAULT)
    assert finding.verdict == "lands"
    assert finding.carrier == 20


def test_one_inert_ref_and_one_landing_ref_are_reported_separately():
    stacked = _pr(30, SIBLING, "Closes #100\nCloses #101")
    carrier = _pr(31, DEFAULT, "Closes #101", head=SIBLING)
    findings = {f.issue: f for f in cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)}
    assert findings[100].verdict == "inert"
    assert findings[101].verdict == "lands"


# ── (c) what cannot be measured is never a pass ──────────────────────────────

def test_resolve_carrier_is_the_documented_wrapper_over_carrier_path():
    """#10634 cycle 7 (P3): once `classify` began calling `carrier_path`, the
    documented `resolve_carrier` had no caller and no direct test — it would have
    rotted silently. It is the module's public shape, so it is pinned directly."""
    subj = _pr(400, SIBLING, "Closes #100")
    carrier = _pr(401, DEFAULT, "", head=SIBLING)
    table = _by_head(subj, carrier)
    assert cl.resolve_carrier(subj, table, DEFAULT) is carrier
    assert cl.resolve_carrier(carrier, table, DEFAULT) is carrier   # already on default
    assert cl.resolve_carrier(_pr(402, "nowhere", ""), table, DEFAULT) is None
    assert [p.number for p in cl.carrier_path(subj, table, DEFAULT)] == [400, 401]


def test_a_base_branch_with_no_pr_is_unresolved_not_a_pass():
    pr = _pr(40, SIBLING, "Closes #100")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved"
    assert finding.carrier is None
    # …and the ONE cause where a PR really is missing says so. `carrier_path`
    # returns None for three causes, so the sentence has to be about THIS one
    # (#10634 review P3).
    assert "no PR with that head branch exists" in finding.detail
    assert "CYCLE" not in finding.detail
    assert "MAX_STACK_DEPTH" not in finding.detail


def test_a_cycle_is_unresolved_not_a_pass():
    b = _pr(51, "a-branch", "", head="b-branch")
    a = _pr(50, "b-branch", "Closes #100", head="a-branch")
    (finding,) = cl.classify(a, _by_head(a, b), DEFAULT)
    assert finding.verdict == "unresolved"
    # The WORDING is pinned, not just the verdict: the cycle arm used to reuse the
    # "no PR with that head branch exists" sentence, which is FALSE here — the PR
    # with that head branch is the other half of the cycle — and the remedy it
    # implies (open a PR on that base) cannot repair a cycle. Only the verdict was
    # asserted, so the false sentence survived a file that pins wording for its
    # four sibling arms (#10634 review P3).
    assert "no PR with that head branch exists" not in finding.detail
    assert "CYCLE" in finding.detail
    # …naming BOTH halves, which is the whole diagnosis: the reader cannot see the
    # cycle from either PR alone.
    assert "PR #50" in finding.detail and "PR #51" in finding.detail
    assert "targets `b-branch`" in finding.detail
    assert "targets `a-branch`" in finding.detail
    # …and the remedy that exists, rather than "open a PR" (which would re-form
    # the cycle): re-target a link at the default branch.
    assert "Re-target" in finding.detail and DEFAULT in finding.detail


def test_the_worst_verdict_wins():
    assert cl.worst_verdict([]) == "none"
    assert cl.worst_verdict([
        cl.Finding(1, 1, "main", "lands", 1, ""),
        cl.Finding(2, 1, "main", "unresolved", None, ""),
    ]) == "unresolved"
    assert cl.worst_verdict([
        cl.Finding(1, 1, "main", "inert", 2, ""),
        cl.Finding(2, 1, "main", "lands", 1, ""),
    ]) == "inert"


# ── the parser: a claim is a claim, a quotation is not ───────────────────────

@pytest.mark.parametrize("body,expected", [
    ("Closes #1", [1]),
    ("closes #2", [2]),
    ("Fixes #3", [3]),
    ("fixed #4", [4]),
    ("Resolves #5", [5]),
    ("Closes: #6", [6]),
    ("Closes #7\nCloses #7", [7]),            # deduped
    ("Closes #8 and Fixes #9", [8, 9]),
    ("Refs #10", []),                          # not a closing keyword
    ("Closes #1\n```\nCloses #2\n```", [1]),   # a fenced block is a quotation
    ("A `Closes #3` in a code span", []),      # so is an inline span
    ("Closes other/repo#4", []),               # cross-repo: not our issue
    ("", []),
])
def test_parse_closing_refs(body, expected):
    assert cl.parse_closing_refs(body) == expected


def test_an_annotation_is_one_line_because_a_newline_truncates_it():
    finding = cl.Finding(9, 1, SIBLING, "inert", 2, "line one\nline two")
    line = finding.annotation()
    assert "\n" not in line
    assert line.startswith("::warning ")
    assert "line one%0Aline two" in line


# ── the CLI: the exit codes a caller branches on ─────────────────────────────

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(MODULE_PATH), *args],
                          capture_output=True, text=True, check=False)


def test_cli_exits_3_for_the_measured_shape(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("fix: something\n\nCloses #10541\n", encoding="utf-8")
    carrier = tmp_path / "carrier.md"
    carrier.write_text("measure: something else\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body), "--base", SIBLING,
                   "--carrier-body-file", str(carrier))
    assert out.returncode == 3, out.stderr
    assert "inert" in out.stdout


def test_cli_exits_3_when_the_carrier_cannot_be_measured(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body), "--base", SIBLING)
    assert out.returncode == 3, out.stderr
    assert "unresolved" in out.stdout


def test_cli_exits_0_for_a_pr_on_the_default_branch(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body), "--base", DEFAULT)
    assert out.returncode == 0, out.stderr
    assert "lands" in out.stdout


def test_cli_exits_0_when_the_carrier_restates_the_keyword(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    carrier = tmp_path / "carrier.md"
    carrier.write_text("Closes #10541\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body), "--base", SIBLING,
                   "--carrier-body-file", str(carrier))
    assert out.returncode == 0, out.stderr


def test_cli_json_names_the_carrier(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body), "--base", SIBLING, "--json")
    assert out.returncode == 3, out.stderr
    import json as _json
    payload = _json.loads(out.stdout)
    assert payload["verdict"] == "unresolved"
    assert payload["findings"][0]["issue"] == 10541


def test_offline_mode_requires_a_base(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Closes #1\n", encoding="utf-8")
    out = _run_cli("--body-file", str(body))
    assert out.returncode != 0
    assert "--base" in out.stderr


# ── the review's P0: a carrier that was closed WITHOUT merging ───────────────

def test_a_closed_unmerged_carrier_is_unresolved_never_lands():
    """The tool's own advice ("re-state the keyword on PR #X") produces this
    state when #X is later closed; blessing it would then bless every stacked PR
    on that base for ever (#10597 review P0-1)."""
    stacked = _pr(60, SIBLING, "Closes #100")
    carrier = _pr(61, DEFAULT, "Closes #100", head=SIBLING, state="CLOSED")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "unresolved"
    assert "CLOSED WITHOUT MERGING" in finding.detail


def test_a_subject_pr_closed_without_merging_is_unresolved_never_lands():
    """A PR closed WITHOUT merging never fired the keyword: nothing was closed
    and nothing landed. The `lands` arm assumes a merge (#10597 review c2-2)."""
    pr = _pr(62, DEFAULT, "Closes #100", state="CLOSED")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved"
    assert "CLOSED WITHOUT MERGING" in finding.detail


def test_pick_carrier_ranks_open_before_merged_before_closed():
    merged = _pr(1, DEFAULT, state="MERGED", merged_at="2026-01-01T00:00:00Z")
    closed = _pr(2, DEFAULT, state="CLOSED")
    opened = _pr(3, DEFAULT, state="OPEN")
    assert cl._pick_carrier([closed, merged, opened]).number == 3
    assert cl._pick_carrier([closed, merged]).number == 1
    assert cl._pick_carrier([closed]).number == 2
    assert cl._pick_carrier([]) is None


# ── a MERGED carrier with no keyword: the work landed, only the closure did not ─

def test_a_merged_carrier_without_the_keyword_prescribes_the_achievable_remedy():
    """The subject merged into its sibling base BEFORE the carrier landed, so its
    commits rode the merge and only the closure failed to fire."""
    stacked = _pr(70, SIBLING, "Closes #100", state="MERGED",
                  merged_at="2026-11-02T10:00:00Z")
    carrier = _pr(71, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert"
    assert "ALREADY merged" in finding.detail
    assert "Close #100 by hand" in finding.detail
    assert "Re-state the keyword" not in finding.detail


def test_a_merged_carrier_does_NOT_claim_the_work_landed_when_the_subject_is_open():
    """#10634 review P1. A carrier merges the branch it was OPENED FROM, so a
    subject that is still open has nothing in it. The pre-fix arm read
    `carrier.merged` alone and told the reader "the work did land … Close #100 by
    hand" — marking an issue done whose work is stranded on the sibling branch.

    Pinned on the CLAIM, not just the verdict: the wrong remedy is the harm.
    """
    stacked = _pr(100, SIBLING, "Closes #100")          # still OPEN
    carrier = _pr(101, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert finding.carrier == 101
    assert "did land" not in finding.detail
    assert "Close #100 by hand" not in finding.detail
    assert "still OPEN" in finding.detail
    assert "NOT on `main`" in finding.detail
    # The REMEDY must exist for THIS state (#10634 cycle 2, P2): an OPEN PR has
    # nothing to re-open — it needs its base retargeted, and the keyword then
    # fires normally. "Re-open PR #100" was impossible and contradicted itself.
    assert "nothing to re-open" in finding.detail, finding.detail
    assert "Re-open PR #100" not in finding.detail, finding.detail


def test_a_subject_that_merged_into_the_sibling_AFTER_the_carrier_is_stranded():
    """The same harm by TIMING rather than by state (#10634 review P1): the
    sibling branch had already landed, so a later merge into it is on a branch no
    PR will carry to the default branch."""
    stacked = _pr(102, SIBLING, "Closes #100", state="MERGED",
                  merged_at="2026-11-02T15:00:00Z")
    carrier = _pr(103, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "did land" not in finding.detail
    assert "Close #100 by hand" not in finding.detail
    assert "BEFORE PR #102 merged into" in finding.detail, finding.detail
    # …and a MERGED PR cannot be re-opened at all (#10634 cycle 2, P2).
    assert "cannot be re-opened" in finding.detail, finding.detail
    assert "Re-open PR #102" not in finding.detail, finding.detail


def test_a_subject_merged_AFTER_the_middle_link_was_carried_up_is_stranded():
    """#10634 cycle 4 (P1): the pairwise ORDER, not just the two endpoints. Subject
    merged 12:00 into `act-a`, but `act-a` was already carried up at 11:00, so the
    commits are on neither `act-b` nor `main`. Comparing each link with the CARRIER
    alone passed it (12:00 < 14:00) and printed "the work did land \u2026 Close #100
    by hand"."""
    subj = _pr(300, "act-a", "Closes #100", state="MERGED",
               merged_at="2026-11-02T12:00:00Z")
    mid = _pr(301, "act-b", "", head="act-a", state="MERGED",
              merged_at="2026-11-02T11:00:00Z")
    carrier = _pr(302, DEFAULT, "", head="act-b", state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "did land" not in finding.detail, finding.detail
    assert "Close #100 by hand" not in finding.detail, finding.detail
    # …and the diagnosis must not invert the stamps it is quoting.
    assert "BEFORE PR #300 merged into `act-a`" in finding.detail, finding.detail
    # A MERGED carrier goes to the merged-carrier arm, not the blocked one — that
    # split is what keeps "the carrier can still be updated" off a merged PR
    # (#10634 cycle 6).
    assert "targets `main` and has ALREADY merged" in finding.detail, finding.detail
    assert "carrier can still be updated" not in finding.detail, finding.detail
    assert "AFTER PR #302 merged" not in finding.detail, finding.detail


def test_a_CLOSED_middle_link_blocks_the_ride_even_with_an_OPEN_carrier():
    """#10634 cycle 4 (P2): the un-merged-carrier arm never consulted the path, so a
    stack whose middle was CLOSED-unmerged promised "only lands when PR #carrier
    merges" while nothing could put the subject's commits in that branch."""
    subj = _pr(800, "a1", "Closes #100", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    mid = _pr(801, "a2", "", head="a1", state="CLOSED")
    carrier = _pr(802, DEFAULT, "", head="a2", state="OPEN")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "only lands when PR #802 merges" not in finding.detail, finding.detail
    assert "PR #801 (base `a2`) was CLOSED WITHOUT MERGING" in finding.detail, finding.detail
    # #10634 cycle 5 (P2): the carrier is OPEN, so the sentence may not report a
    # merge that has not happened (this branch states the direction and nothing
    # about WHEN).
    assert "branch PR #802 merges into" in finding.detail, finding.detail
    assert "branch PR #802 merged" not in finding.detail, finding.detail
    # …and (P3) the stack is not DEAD — but the re-open is offered only as what it
    # is: a repair of the path ABOVE the link, never "the repair" of the commits
    # (#10634 cycle 7).
    assert "AS IT STANDS" in finding.detail, finding.detail
    assert "PR #801 can be re-opened" in finding.detail, finding.detail
    assert "is the repair" not in finding.detail, finding.detail


def test_a_MERGED_middle_link_above_an_UNMERGED_subject_is_a_break_with_no_stamps():
    """#10634 cycle 8 (P1): the state break a TIMESTAMP test cannot see. The middle
    link merged while the subject is still OPEN, so the subject's commits enter
    `b1` afterwards and are in neither `b2` nor `main` — no `merged_at` comparison
    can show it, only the STATES can. It used to print `inert` + "…only lands when
    PR #102 merges … Re-state the keyword on PR #102": complying closes #7 over
    stranded work."""
    subj = _pr(100, "b1", "Closes #7")
    mid = _pr(101, "b2", "", head="b1", state="MERGED",
              merged_at="2026-11-02T09:00:00Z")
    carrier = _pr(102, DEFAULT, "", head="b2", state="OPEN")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "only lands when PR #102 merges" not in finding.detail, finding.detail
    assert "Re-state the keyword on PR #102" not in finding.detail, finding.detail
    assert "has ALREADY merged while PR #100 is still OPEN" in finding.detail, finding.detail


def test_a_MERGED_keyword_carrying_carrier_over_an_OPEN_subject_is_not_a_pass():
    """#10634 cycle 9 (P1): with the carrier's own link excluded from the predicate,
    a MERGED, keyword-carrying carrier returned `lands` (exit 0, no warning) over
    an OPEN subject — whose commits are in no branch that carrier merged. The
    keyword does not change whether the work rode in, so the keyword-less twin of
    this record set is `unresolved`; 18 configurations were false passes, all this
    one link."""
    subj = _pr(1, "lane-1", "Closes #7")                     # still OPEN
    carrier = _pr(2, DEFAULT, "Closes #7", head="lane-1", state="MERGED",
                  merged_at="2026-11-02T09:00:00Z")
    (finding,) = cl.classify(subj, _by_head(subj, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "closes #7 when it lands" not in finding.detail, finding.detail
    assert "ALREADY FIRED" in finding.detail, finding.detail


def test_a_merged_carrier_reports_the_closure_that_ALREADY_fired():
    """#10634 cycle 9 (P2): the past-tense carrier said "it closes #N when it lands"
    and "would be closed" for a keyword that has already fired — a promise about an
    event in the past, and a remedy ("state Closes #N") that is useless on a CLOSED
    issue. Here the subject merged after the carrier, so the issue IS closed."""
    subj = _pr(21, "b1", "Closes #7", state="MERGED",
               merged_at="2026-11-02T20:00:00Z")
    carrier = _pr(23, DEFAULT, "Closes #7", head="b1", state="MERGED",
                  merged_at="2026-11-02T09:00:00Z")
    (finding,) = cl.classify(subj, _by_head(subj, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "ALREADY FIRED when PR #23 merged" in finding.detail, finding.detail
    assert "will be closed" not in finding.detail, finding.detail
    assert "is therefore closed" in finding.detail, finding.detail
    assert "re-open #7" in finding.detail, finding.detail
    # …and the ORDER it names must be the one the stamps show.
    assert "BEFORE PR #21 merged into" in finding.detail, finding.detail


def test_the_break_diagnosis_names_the_link_whose_stamps_disagree():
    """#10634 cycle 9 (P2): the fallback printed "AFTER" over a LATER carrier stamp
    (08:00 is not after 12:00) because the last intermediate's late merge was the
    real break. It must name the link the stamps actually disagree on."""
    subj = _pr(21, "b1", "Closes #7", state="MERGED",
               merged_at="2026-11-02T08:00:00Z")
    mid = _pr(22, "b2", "", head="b1", state="MERGED",
              merged_at="2026-11-02T15:00:00Z")
    carrier = _pr(23, DEFAULT, "Closes #7", head="b2", state="MERGED",
                  merged_at="2026-11-02T12:00:00Z")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "PR #23 (base `main`) merged at 2026-11-02T12:00:00Z, BEFORE PR #22" in finding.detail, finding.detail
    assert "AFTER PR #23 merged" not in finding.detail, finding.detail


def test_a_keyword_carrying_carrier_does_not_buy_a_pass_over_a_BROKEN_path():
    """#10634 cycle 8 (P2): the `carried` arm granted `lands` (exit 0, no warning) on
    a provably broken stack — the keyword FIRES, but the work does not ride in, and
    the issue is closed over stranded work. `lands` means "it rides in and closes"
    (module docstring), so the pass must be refused."""
    subj = _pr(100, "b1", "Closes #7", state="MERGED",
               merged_at="2026-11-02T13:00:00Z")
    mid1 = _pr(101, "b2", "", head="b1", state="OPEN")
    mid2 = _pr(102, "b3", "", head="b2", state="MERGED",
               merged_at="2026-11-02T11:00:00Z")
    carrier = _pr(103, DEFAULT, "Closes #7", head="b3", state="OPEN")
    (finding,) = cl.classify(subj, _by_head(subj, mid1, mid2, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "it closes #7 when it lands" in finding.detail, finding.detail
    assert "would be closed over work that is not on" in finding.detail, finding.detail


def test_a_HEALTHY_MERGED_stack_whose_carrier_restates_the_keyword_lands():
    """#10634 cycle 13 (P2): the `lands` arm's merged-carrier tense was live code no
    test reached (0 hits in the suite), so a regression to the future tense — "it
    closes #7 when it lands", a promise about an event in the PAST, on an issue that
    is already closed — would have passed unnoticed. It is also the exit-0 path:
    the module must NOT flag a correctly stacked-and-merged PR."""
    subj = _pr(1, "sib", "Closes #7", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    carrier = _pr(2, DEFAULT, "Closes #7", head="sib", state="MERGED",
                  merged_at="2026-11-02T14:00:00Z")
    (finding,) = cl.classify(subj, _by_head(subj, carrier), DEFAULT)
    assert finding.verdict == "lands", finding
    assert "ALREADY FIRED when PR #2 merged" in finding.detail, finding.detail
    assert "so #7 is closed" in finding.detail, finding.detail
    assert "closes #7 when it lands" not in finding.detail, finding.detail


def test_a_keyword_carrying_carrier_over_a_HEALTHY_stack_still_lands():
    """The other direction: the refusal must not swallow a real pass (all links open
    is a stack in progress, not a break)."""
    subj = _pr(110, "b1", "Closes #7")
    mid = _pr(111, "b2", "", head="b1")
    carrier = _pr(112, DEFAULT, "Closes #7", head="b2")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "lands", finding


def test_a_timing_break_with_an_OPEN_carrier_is_not_promised_a_ride():
    """#10634 cycle 5 (P1): the blocked arm screened only CLOSED-unmerged links, so a
    TIMING break with the carrier still OPEN promised "#100's work only lands when
    PR #202 merges" — but #202 merges `act-b`, which never contained #100's commits
    (they entered `act-a` at 12:00, after `act-a` had been carried up at 11:00).
    Complying closes #100 over stranded work."""
    subj = _pr(100, "act-a", "Closes #100", state="MERGED",
               merged_at="2026-11-02T12:00:00Z")
    mid = _pr(201, "act-b", "", head="act-a", state="MERGED",
              merged_at="2026-11-02T11:00:00Z")
    carrier = _pr(202, DEFAULT, "", head="act-b", state="OPEN")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "only lands when PR #202 merges" not in finding.detail, finding.detail
    assert "BEFORE PR #100 merged into `act-a`" in finding.detail, finding.detail
    # A timing break cannot be repaired by editing the OPEN carrier's body, and no
    # re-open is asserted as sufficient (#10634 cycle 7).
    assert "carrier can still be updated" not in finding.detail, finding.detail
    assert "is the repair" not in finding.detail, finding.detail


def test_a_three_hop_timing_break_is_caught_through_a_non_adjacent_middle():
    """The same break one hop further up: #201 merged 14:00, #202 (which carries its
    base up) merged 11:00, carrier still OPEN."""
    subj = _pr(210, "a0", "Closes #100", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    mid1 = _pr(211, "a1", "", head="a0", state="MERGED",
               merged_at="2026-11-02T14:00:00Z")
    mid2 = _pr(212, "a2", "", head="a1", state="MERGED",
               merged_at="2026-11-02T11:00:00Z")
    carrier = _pr(213, DEFAULT, "", head="a2", state="OPEN")
    (finding,) = cl.classify(subj, _by_head(subj, mid1, mid2, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "only lands when PR #213 merges" not in finding.detail, finding.detail
    assert "BEFORE PR #211 merged into `a1`" in finding.detail, finding.detail


def test_a_CLOSED_middle_link_under_a_MERGED_carrier_never_offers_an_update():
    """#10634 cycle 6 (P2): the blocked arm printed "the carrier can still be
    updated" unconditionally, so with a CLOSED-unmerged middle link and an
    ALREADY-MERGED carrier it prescribed an impossible action — nothing pushed to
    a merged PR reaches the default branch. The LINK can still be re-opened, so the
    case stays in this arm."""
    subj = _pr(810, "a1", "Closes #100", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    mid = _pr(811, "a2", "", head="a1", state="CLOSED")
    carrier = _pr(812, DEFAULT, "", head="a2", state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "carrier can still be updated" not in finding.detail, finding.detail
    # …and no carrier edit is offered AT ALL here (cycle 7): the arm's premise is
    # that the commits are not in the branch the carrier merges, so editing its
    # body would close the issue over stranded work.
    assert "PR #811 can be re-opened" in finding.detail, finding.detail
    # The commits ARE in that link's head (`a1`), so re-opening it is the repair and
    # the sentence must not call it insufficient (#10634 cycle 11) — nor restate the
    # same action as a second, different remedy.
    assert "carries #810's commits up with it" in finding.detail, finding.detail
    assert "only restores the path above it" not in finding.detail, finding.detail
    assert "cannot be re-opened: open a PR from" not in finding.detail, finding.detail


def test_a_reopen_is_NOT_called_sufficient_when_an_OPEN_link_sits_below_it():
    """#10634 cycle 12 (P2): the strong tail tested only "the links below did not
    BREAK", and `_path_break` calls an OPEN intermediate "in progress" — but here
    the question is whether the commits are already IN the closed link's head, and
    an OPEN link between the subject and it means they never got there. The
    sentence then claimed a ride its own diagnosis denied."""
    subj = _pr(100, "h1", "Closes #7", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    open_link = _pr(101, "h2", "", head="h1")
    closed = _pr(102, "h3", "", head="h2", state="CLOSED")
    carrier = _pr(103, DEFAULT, "", head="h3")
    (finding,) = cl.classify(subj, _by_head(subj, open_link, closed, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "carries #100's commits up with it" not in finding.detail, finding.detail
    assert "only restores the path above it" in finding.detail, finding.detail


def test_a_reopen_is_NOT_called_sufficient_over_a_TIMING_break_below_it():
    """The other unpinned conjunct: a merged subject whose lower link merged BEFORE
    it, below the closed link, so the commits are not in that head either."""
    subj = _pr(110, "h1", "Closes #7", state="MERGED",
               merged_at="2026-11-02T11:00:00Z")
    lower = _pr(111, "h2", "", head="h1", state="MERGED",
                merged_at="2026-11-02T09:00:00Z")
    closed = _pr(112, "h3", "", head="h2", state="CLOSED")
    carrier = _pr(113, DEFAULT, "", head="h3")
    (finding,) = cl.classify(subj, _by_head(subj, lower, closed, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "carries #110's commits up with it" not in finding.detail, finding.detail
    assert "only restores the path above it" in finding.detail, finding.detail


def test_a_TWO_HOP_stack_with_an_unmerged_middle_link_is_not_claimed_landed():
    """#10634 cycle 3 (P2): `_subject_landed` compared the SUBJECT's stamp with the
    CARRIER's and never looked at the links between, so a two-hop stack whose
    MIDDLE PR never merged fell into "the work did land \u2026 Close #100 by hand" —
    `act-a`'s commits cannot be inside a merge of `act-b` if nothing carried them
    there. Same harm as the single-hop P1, one hop over."""
    subj = _pr(200, "act-a", "Closes #100", state="MERGED",
               merged_at="2026-11-02T10:00:00Z")
    mid = _pr(201, "act-b", "", head="act-a", state="OPEN")
    carrier = _pr(202, DEFAULT, "", head="act-b", state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "did land" not in finding.detail, finding.detail
    assert "Close #100 by hand" not in finding.detail, finding.detail
    assert "PR #201 (base `act-b`) was not merged" in finding.detail, finding.detail
    # …and the scope of the claim: "nothing in the CURRENT STACK", not an absolute
    # (retargeting the subject at the default branch still carries it) — #10634 c6.
    assert "nothing in the current stack" in finding.detail, finding.detail


def test_a_TWO_HOP_stack_whose_middle_link_landed_in_time_still_claims_landed():
    """The other direction: the middle link merged BEFORE the carrier, so the work
    did ride it. Without this the guard could pass by refusing every 2-hop stack."""
    subj = _pr(210, "act-a", "Closes #100", state="MERGED",
               merged_at="2026-11-02T09:00:00Z")
    mid = _pr(211, "act-b", "", head="act-a", state="MERGED",
              merged_at="2026-11-02T11:00:00Z")
    carrier = _pr(212, DEFAULT, "", head="act-b", state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(subj, _by_head(subj, mid, carrier), DEFAULT)
    assert finding.verdict == "inert", finding
    assert "Close #100 by hand" in finding.detail, finding.detail
    # #10634 cycle 8 (P3): the SUBJECT merged here, so no remedy may tell the reader
    # to re-open it — an impossible action, and the cycle-2 class this file pins for
    # the other arms.
    assert "Re-open" not in finding.detail, finding.detail
    assert "cannot be re-opened" not in finding.detail, finding.detail


def test_a_closed_unmerged_subject_under_a_merged_carrier_says_SO_and_offers_reopen():
    """Same arm, third subject state (#10634 review of the fix): "still CLOSED"
    describes a transient state where this is a final one, and it hid the one
    cheap remedy — re-opening the PR with the right base."""
    stacked = _pr(106, SIBLING, "Closes #100", state="CLOSED")
    carrier = _pr(107, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "CLOSED WITHOUT MERGING" in finding.detail, finding.detail
    assert "still CLOSED" not in finding.detail, finding.detail
    assert "Re-open PR #106" in finding.detail, finding.detail
    assert "did land" not in finding.detail


@pytest.mark.parametrize("state,subject_merged_at,expected,forbidden,why", [
    ("OPEN", None,
     "Retarget PR #110's base at `main`", "Re-open PR #110",
     "an OPEN PR has nothing to re-open — the BASE is what must move"),
    ("MERGED", "2026-11-02T15:00:00Z",
     "cannot be re-opened", "Re-open PR #110",
     "a MERGED PR can be neither re-opened nor retargeted"),
    ("CLOSED", None,
     "Re-open PR #110, retarget it at `main`", "cannot be re-opened",
     "a CLOSED-unmerged PR CAN be re-opened, and that is the cheap route"),
])
def test_the_stranded_remedy_names_an_action_that_EXISTS_in_that_subject_state(
        state, subject_merged_at, expected, forbidden, why):
    """#10634 review P2. The stranded arm printed one sentence — "re-open PR #N
    retargeted at `<default>`" — for all three subject states, and it is an
    action that exists in exactly ONE of them. A reader told to re-open an OPEN
    PR, or to re-open a MERGED one, has no move; the achievable remedy is what
    the finding is for.
    """
    stacked = _pr(110, SIBLING, "Closes #100", state=state,
                  merged_at=subject_merged_at)
    carrier = _pr(111, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert expected in finding.detail, (why, finding.detail)
    assert forbidden not in finding.detail, (why, finding.detail)


def test_a_merged_carrier_with_no_time_stamps_still_claims_landed_when_the_subject_merged():
    """Offline records carry no `merged_at`; `state: MERGED` on both is all the
    evidence there is, and it is the shape the live path cannot produce (it
    always asks for `mergedAt`) — so it stays a `lands`-side claim."""
    stacked = _pr(104, SIBLING, "Closes #100", state="MERGED")
    carrier = _pr(105, DEFAULT, "", head=SIBLING, state="MERGED")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert"
    assert "Close #100 by hand" in finding.detail


# ── a qualified reference to THIS repo is a claim, not a foreign reference ────

def test_a_same_repo_qualified_reference_is_a_claim():
    body = "Closes daniel-ospina/tortoise#100"
    assert cl.parse_closing_refs(body) == [], "no repo given -> treated as foreign"
    assert cl.parse_closing_refs(body, "daniel-ospina/tortoise") == [100]
    assert cl.parse_closing_refs("Closes other/repo#100",
                                 "daniel-ospina/tortoise") == []


# ── the quotation forms a fence regex must know ──────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("Closes #1\n~~~\nCloses #2\n~~~", [1]),
    # An INDENTED block is no longer stripped: where a 4-space line is code vs a
    # list item's continuation depends on the enclosing container's content
    # column, and guessing wrong in the stripping direction loses a REAL claim.
    # Over-reporting a quoted one is the safe direction here (see strip_code).
    ("Closes #1\n\n    Closes #2\n", [1, 2]),
    ("Closes #1\n```\nCloses #2\n```", [1]),
])
def test_quoted_keywords_are_not_claims(body, expected):
    assert cl.parse_closing_refs(body) == expected


def test_a_keyword_separated_from_its_reference_by_a_BLANK_LINE_cannot_buy_a_pass():
    r"""#10634 review P3-1: `\s*` matches a BLANK LINE, so `Closes\n\n#100` was a
    claim in the STRICT parse — and the strict parse is what backs every PASS. A
    reference in a SEPARATE PARAGRAPH is a shape no GitHub doc shows and this
    tool cannot demonstrate fires, so it may not pass (the same rule as the
    indented case, cycle 5). It is still REPORTED: the lenient finding arms keep
    the claim, so the narrowing drops no real closure.
    """
    assert cl.parse_closing_refs("Closes\n\n#100") == [100]
    assert cl.parse_closing_refs("Closes\n\n#100", strict=True) == []
    # A SINGLE newline is not a paragraph break and stays a claim in both.
    assert cl.parse_closing_refs("Closes\n#100", strict=True) == [100]

    pr = _pr(120, DEFAULT, "fix: the thing\n\nCloses\n\n#100\n")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "LENIENT parse" in finding.detail
    # The REMEDY must be achievable for THIS cause (#10634 review of the fix):
    # the arm used to name indentation as the cause and prescribe "at the start of
    # a line", which this body ALREADY satisfies — a reader who complies stays
    # unresolved and the issue stays open. All THREE causes (a blank-line
    # separator, a four-space-indented line, and a code span that now must be
    # named too: the message once named two of three, #10634 review P3) and one
    # actionable form.
    assert "blank line" in finding.detail, finding.detail
    assert "code span" in finding.detail, finding.detail
    assert "indented" in finding.detail, finding.detail
    assert "on one line" in finding.detail, finding.detail
    assert "at the start of a line" not in finding.detail, finding.detail
    # …and the other half of that finding: for a body that only QUOTES the
    # keyword the remedy must not talk the author into creating a closure.
    assert "QUOTATION" in finding.detail, finding.detail
    assert "there is nothing to fix" in finding.detail, finding.detail
    # …and a same-line claim on the same shape still lands (the guard is the
    # blank line, not the multi-paragraph body).
    ok = _pr(121, DEFAULT, "fix: the thing\n\nCloses #100\n")
    (landed,) = cl.classify(ok, _by_head(ok), DEFAULT)
    assert landed.verdict == "lands"


def test_a_keyword_inside_a_WIDER_backtick_span_is_a_quotation_not_a_claim():
    """#10634 review P3-2: the inline strip knew ONE-backtick spans only, so a
    two-backtick delimited `Closes #100` (CommonMark: same code span, run of 2)
    counted as a claim — and on the CARRIER side that buys a `lands`.
    """
    assert cl.parse_closing_refs("A ``Closes #100`` in a wide span") == []
    assert cl.parse_closing_refs("A ```Closes #100``` in a wider span") == []
    # A span's own inner backtick run does not close it…
    assert cl.parse_closing_refs("```a`b``` then Closes #100") == [100]
    # …and an UNMATCHED run is literal text, not a span opened at EOF.
    assert cl.parse_closing_refs("stray ``` then Closes #100") == [100]

    subject = _pr(130, SIBLING, "Closes #100")
    quoting = _pr(131, DEFAULT, "Old body: ``Closes #100``\n", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, quoting), DEFAULT)
    assert finding.verdict == "inert", finding
    assert "Re-state the keyword" in finding.detail
    # The claim is scoped to what the tool can SHOW (#10634 cycle 2, P3): the
    # carrier does contain the keyword, in a shape the tool does not count, so an
    # unqualified "carries no `Closes #100`" is a fact the reader can falsify —
    # and a warn-only instrument the reader stops believing gets switched off.
    assert "this tool can show fires" in finding.detail, finding.detail
    assert "carries no `Closes #100` this tool" in finding.detail, finding.detail

    # …the same clause, for the shape the fix was written for: a carrier whose
    # keyword is separated from its reference by a BLANK LINE.
    blank_carrier = _pr(132, DEFAULT, "Closes\n\n#100\n", head=SIBLING)
    (blank,) = cl.classify(subject, _by_head(subject, blank_carrier), DEFAULT)
    assert blank.verdict == "inert", blank
    assert "this tool can show fires" in blank.detail, blank.detail


def test_a_BACKSLASH_ESCAPED_backtick_is_PROSE_not_a_code_span():
    r"""#10841 review (P2): the blanker took ANY backtick run as a delimiter, so
    ``\`Closes #100\``` — ESCAPED backticks — was blanked as if it were a code
    span, while GFM renders it as prose carrying a live keyword:

        "see \`Closes #100\` for the old wording"
        -> <p>see `Closes #100` for the old wording</p>

    On a stacked PR that is exactly the inert `Closes #N` this tool exists to
    name, and it reported NOTHING and exited 0 — the MISSED-WARNING direction,
    against the module's "never a false pass" contract.

    The escape is CommonMark's (§2.4): ONE backslash escapes the delimiter, so
    ``\\\`` is an escaped BACKSLASH followed by a real one. That parity is the
    second arm below — a test that only asks "is the previous character a
    backslash" reads the double-escape case BACKWARDS and blanks a real
    quotation, which is the other direction (a lost pass on a correctly quoted
    body).
    """
    escaped = r"see \`Closes #100\` for the old wording"
    # The renderer is the authority, as everywhere in this file: no <code> at all.
    assert "<code>" not in MarkdownIt("commonmark").render(escaped)
    # …so BOTH arms must see the claim: nothing was ever inside a code span.
    assert cl.parse_closing_refs(escaped) == [100], escaped
    assert cl.parse_closing_refs(escaped, None, strict=True) == [100], escaped

    # THE DOUBLE-ESCAPE CONTROL: `\\` is an escaped backslash, so the backtick
    # after it IS a delimiter and this really is a quotation.
    double = r"see \\`Closes #100\\` in a quoted shape"
    assert "<code>" in MarkdownIt("commonmark").render(double)
    assert cl.parse_closing_refs(double) == [], double
    assert cl.parse_closing_refs(double, None, strict=True) == [], double

    # THE ASYMMETRY, measured rather than reasoned: an escape stops a backtick
    # from OPENING a span, but a span's closer is matched as a raw backtick run,
    # so `` `Closes #100\` `` really IS a code span and the blanker must agree
    # with the renderer on it. (This arm exists because the first cut of the fix
    # guarded the closer too, and the renderer refuted it.)
    escaped_closer = r"`Closes #100\` is literal"
    assert "<code>" in MarkdownIt("commonmark").render(escaped_closer)
    assert cl.parse_closing_refs(escaped_closer) == [], escaped_closer

    # …and the CARRIER half, where the same bug pointed the other way: an escaped
    # span in the carrier is live prose, so the carrier DOES restate the keyword
    # and the subject lands. The pre-fix blanker read it as a quotation and called
    # the subject `inert` — a warning about a closure that really does fire.
    subject = _pr(140, SIBLING, "Closes #100")
    escaped_carrier = _pr(141, DEFAULT, "Old body: \\`Closes #100\\`\n", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, escaped_carrier), DEFAULT)
    assert finding.verdict == "lands", finding
    assert finding.carrier == 141, finding


def test_a_code_span_that_crosses_a_LINE_BREAK_cannot_buy_a_pass():
    r"""#10634 review P2 (fail-open): a span's closer search stopped at the END OF
    ITS LINE, so a code span whose opener and closer sit on two lines of the SAME
    paragraph was never blanked — and CommonMark §6.1 says a span contains line
    endings ("line endings are converted to spaces"), so the renderer puts the
    keyword inside `<code>`:

        "Example: `Closes\n#10541` is the quoted form."
        -> <p>Example: <code>Closes #10541</code> is the quoted form.</p>

    Both PASS arms read `strict`, so both reported `lands` over a keyword GitHub
    ignores — the one outcome this module forbids. The boundary is now the BLANK
    LINE (a span cannot cross a paragraph), and the two arms keep their asymmetry:
    `strict` searches to the end of the paragraph, the FINDING arms keep the
    per-line bound and OVER-REPORT the multi-line span — a false warning, never a
    lost closure.
    """
    multiline = "Example: `Closes\n#10541` is the quoted form.\n"
    wide = "The body used ``Closes\n#10541`` in a quoted example.\n"
    in_item = "- item `Closes\n  #100` here\n"
    for body, n in ((multiline, 10541), (wide, 10541), (in_item, 100)):
        # the FINDING arm keeps the claim (documented over-report) …
        assert cl.parse_closing_refs(body) == [n], body
        # … and the PASS arm refuses it.
        assert cl.parse_closing_refs(body, None, strict=True) == [], body

    # The subject's own `lands` arm refuses the pass, and says WHICH parse saw it
    # (the resolution the body has to be fixed for is spelled out, not guessed).
    pr = _pr(170, DEFAULT, multiline)
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "LENIENT parse" in finding.detail, finding.detail

    # …and the CARRIER arm, which is the silent-stranding case the tool exists
    # for: a stacked PR whose carrier only QUOTES the keyword in such a span.
    subject = _pr(171, SIBLING, "Closes #100")
    carrier = _pr(172, DEFAULT, wide.replace("10541", "100"), head=SIBLING)
    (carried,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert carried.verdict == "inert", carried
    assert "Re-state the keyword" in carried.detail, carried.detail

    # The controls — the shapes that were ALREADY right must stay right, so the
    # new boundary cannot be satisfied by blanking more: a one-line span is code
    # in both arms, and a one-line claim still lands.
    assert cl.parse_closing_refs("Example: `Closes #100` quoted") == []
    assert cl.parse_closing_refs("Example: `Closes #100` quoted", None, strict=True) == []
    ok = _pr(173, DEFAULT, "fix: the thing\n\nCloses #100\n")
    (landed,) = cl.classify(ok, _by_head(ok), DEFAULT)
    assert landed.verdict == "lands"
    # …and a stray run may not swallow the claim on the NEXT line: the lenient
    # arm never pairs across a line break, so an indented-code tick cannot blank
    # the paragraph below it (the direction that loses a real closure).
    assert cl.parse_closing_refs("    a `b\nprose `c Closes #100\n") == [100]


def test_no_module_level_constant_is_DEAD_because_a_dead_one_reads_as_live():
    r"""#10634 review P3 at `241bd0007`: `_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")`
    was defined, NEVER read, and its own comment claimed it is "where the STRICT
    inline scan stops looking for a closing backtick run" — with a second comment
    ("needed this as well as `_BLANK_LINE_RE`") pointing a maintainer at it as the
    live half of the paragraph bound.

    ⚠️ That is not tidy-up. `_span_limit` takes the strict bound from
    `_paragraph_end`, which ends the paragraph on its OWN blank-line test
    (`if not line.strip()`). A maintainer who believes the comment has a
    documented reason to "restore" the constant as the span bound — and a
    blank-line-ONLY bound is exactly the fail-open shape this PR removed: an
    unmatched backtick run then pairs with the OPENER of a real span in a later
    block and exposes the keyword that span held (`lands`, exit 0, no annotation).

    So the constant is DELETED and this guard keeps the CLASS from returning: a
    name defined at module level and never loaded is a false claim of liveness,
    because a prose mention is not a reference. That is why the check is AST-based
    and not a `grep`: a `grep` finds the dead comment (and this docstring) and
    calls the constant alive, while the AST sees only `LOAD`s — what the module
    actually reads.
    """
    tree = ast.parse(MODULE_PATH.read_text())
    defined, loaded = set(), set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.upper() == target.id:
                defined.add(target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
    # A scan that finds nothing proves nothing — this asserts the guard MEASURES.
    assert defined, "no module-level constants found: the scan is not measuring"
    assert not defined - loaded, (
        f"module-level constant(s) {sorted(defined - loaded)} are never loaded: a dead "
        f"name whose comment can claim it is the live mechanism (#10634 review P3)")


def test_a_closed_unmerged_subject_on_a_sibling_base_is_not_promised_a_carrier():
    """#10634 cycle 2 (P2): the sibling-base arm said "#100's work only lands when
    PR #carrier merges" for a subject that was CLOSED WITHOUT MERGING — its
    commits are in no branch the carrier merges, so complying closes #100 with the
    work stranded on the closed PR's head: the harm the P1 fix removed one branch
    over. Verdict `inert` stays (the keyword still does not fire); the CLAIM and
    the remedy must match the state."""
    subject = _pr(30, SIBLING, "Closes #100", state="CLOSED")
    carrier = _pr(31, DEFAULT, "", state="OPEN", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert finding.verdict == "inert", finding
    assert "only lands when PR #31 merges" not in finding.detail, finding.detail
    assert "CLOSED WITHOUT MERGING" in finding.detail, finding.detail
    assert "Re-open PR #30" in finding.detail, finding.detail
    # The CLAIMS are scoped too (#10634 cycle 6): "nothing this tool can show",
    # never an absolute, and never an unqualified "carries no".
    assert "nothing this tool can show puts its work in `act-10465`" in finding.detail, finding.detail
    assert "carries no `Closes #100` this tool can show fires" in finding.detail, finding.detail
    assert "retarget it at `main`" in finding.detail, finding.detail


# ── the UNMEASURED arm the workflow's dead-man keys on ───────────────────────

def test_exit_2_when_gh_cannot_answer(monkeypatch, capsys):
    def boom(_args):
        raise cl.Unmeasurable("gh is down")
    monkeypatch.setattr(cl, "_gh", boom)
    assert cl.main(["--pr", "1", "--repo", "owner/name"]) == 2
    assert "UNMEASURED" in capsys.readouterr().err


def test_exit_2_on_sweep_when_gh_cannot_answer(monkeypatch):
    def boom(_args):
        raise cl.Unmeasurable("gh is down")
    monkeypatch.setattr(cl, "_gh", boom)
    assert cl.main(["--sweep", "--repo", "owner/name"]) == 2


def test_sweep_flags_only_the_offending_pr(monkeypatch):
    """The population query is the one thing an empty `--sweep` cannot prove, so
    it is stubbed here: an open PR on a sibling base with an inert keyword is
    found, and a PR on the default branch is not."""
    import json as _json

    def fake_gh(args):
        if args[:2] == ["repo", "view"]:
            return _json.dumps({"defaultBranchRef": {"name": DEFAULT}})
        if "--head" in args:
            return _json.dumps([{"number": 82, "baseRefName": DEFAULT,
                                 "body": "no keyword", "headRefName": SIBLING,
                                 "state": "OPEN", "mergedAt": None}])
        return _json.dumps([
            {"number": 80, "baseRefName": SIBLING, "body": "Closes #100",
             "headRefName": "stacked", "state": "OPEN", "mergedAt": None},
            {"number": 81, "baseRefName": DEFAULT, "body": "Closes #101",
             "headRefName": "on-main", "state": "OPEN", "mergedAt": None},
        ])

    monkeypatch.setattr(cl, "_gh", fake_gh)
    findings = cl.sweep("owner/name", DEFAULT)
    assert [(f.pr, f.issue, f.verdict) for f in findings] == [(80, 100, "inert")]


# ── cycle 2: the indented strip must not swallow REAL claims ────────────────

@pytest.mark.parametrize("body,expected", [
    # An indented block cannot interrupt a paragraph or a list item: these are
    # rendered TEXT, so the keyword is a real claim and must survive the strip.
    ("- fix it\n    - Closes #10541\n", [10541]),
    ("- [x] fix it\n    Closes #10541\n", [10541]),
    ("1. fix it\n    Closes #10541\n", [10541]),
    ("see below\n    Closes #10541\n", [10541]),
    # …and an indented block after a blank line is NOT stripped either: the
    # over-report is deliberate (see strip_code) — losing a real claim is worse.
    ("note:\n\n    Closes #10541\n", [10541]),
])
def test_indented_lines_that_continue_prose_are_still_claims(body, expected):
    assert cl.parse_closing_refs(body) == expected


@pytest.mark.parametrize("body,expected", [
    # A closing fence may be LONGER than its opener (CommonMark), and a ```-only
    # backreference leaked the content of the shorter-closed block.
    ("````\nCloses #1\n`````\nCloses #2\n", [2]),
    ("~~~\nCloses #1\n~~~~\nCloses #2\n", [2]),
    ("```\nCloses #1\n```\nCloses #2\n", [2]),
])
def test_a_shorter_or_longer_closing_fence_is_honoured(body, expected):
    assert cl.parse_closing_refs(body) == expected


# ── cycle 2: the annotation title states the ACTUAL verdict ──────────────────

def test_an_annotation_title_names_the_verdict_not_a_constant():
    inert = cl.Finding(9, 1, SIBLING, "inert", 2, "x")
    unresolved = cl.Finding(9, 1, SIBLING, "unresolved", None, "x")
    assert "title=Inert Closes #9" in inert.annotation()
    assert "title=Unresolved Closes #9" in unresolved.annotation()


# ── cycle 2: sweep()'s POPULATION QUERY, not just its output ─────────────────

def test_sweep_asks_for_the_OPEN_population(monkeypatch):
    """A mutant that asks for `--state closed` returns the same stub population,
    so the *argv* has to be pinned or the mutation survives (#10597 review c2-3)."""
    import json as _json

    calls = []

    def fake_gh(args):
        calls.append(list(args))
        if "--head" in args:
            return "[]"
        return _json.dumps([
            {"number": 90, "baseRefName": SIBLING, "body": "Closes #100",
             "headRefName": "stacked", "state": "OPEN", "mergedAt": None},
        ])

    monkeypatch.setattr(cl, "_gh", fake_gh)
    cl.sweep("owner/name", DEFAULT)
    list_calls = [c for c in calls if "list" in c and "--head" not in c]
    assert list_calls, "sweep() never asked for the open-PR population"
    argv = list_calls[0]
    assert argv[argv.index("--state") + 1] == "open"
    assert "--limit" in argv


# ── cycle 3: the strip must not lose REAL claims inside list items ───────────

@pytest.mark.parametrize("body,expected", [
    ("- [x] fix it\n\n    Closes #10541\n", [10541]),
    ("- fix it\n\n    - Closes #10541\n", [10541]),
    ("1. step\n\n    - Closes #10541\n", [10541]),
    ("note:\n\n    Closes #10541\n", [10541]),  # over-report, deliberately (see strip_code)
])
def test_a_claim_after_a_blank_line_inside_a_list_is_still_a_claim(body, expected):
    assert cl.parse_closing_refs(body) == expected


def test_a_fence_character_that_does_not_match_does_not_close():
    """`~~~` does not close a ```-opened block, so #2 is still inside the fence."""
    assert cl.parse_closing_refs("```\nCloses #1\n~~~\nCloses #2\n```\n") == []


# ── cycle 10 SUPERSEDES cycle 3: a CLOSED-unmerged subject cannot buy a pass ─

def test_a_closed_unmerged_subject_does_not_buy_a_pass_on_its_carrier_keyword():
    """SUPERSEDES the cycle-3 rule "a CLOSED subject on a sibling base is judged by
    its carrier", which allowed `lands` here.

    Cycle 3 asked only "does the carrier restate the keyword?", and the answer is
    yes. Cycle 9 established the stronger contract `lands` now carries: the keyword
    FIRES **and** the work RIDES IN. A subject CLOSED WITHOUT MERGING never put its
    commits in `SIBLING`, so the carrier merging `SIBLING` closes #100 over work
    that is on neither branch — exit 0, no warning, no annotation. The keyword
    cannot change whether the work rode in (the keyword-less twin of this record
    set is already `inert`), so the pass was the silent-stranding case this tool
    exists to surface.

    The carrier's own assertion remains worth saying: the closure WILL fire, and
    the reader needs the re-open/retarget action rather than "re-state the keyword".
    """
    subject = _pr(63, SIBLING, "Closes #100", state="CLOSED")
    carrier = _pr(64, DEFAULT, "Closes #100", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert finding.carrier == 64
    assert "CLOSED WITHOUT MERGING" in finding.detail, finding.detail
    assert "it closes #100 when it lands" in finding.detail, finding.detail
    assert "Re-open PR #63" in finding.detail, finding.detail
    assert "would be closed over work that is not on" in finding.detail, finding.detail


# ── cycle 3: the resolved repo slug is what makes a qualified claim visible ──

def test_main_resolves_the_repo_slug_when_repo_is_omitted(monkeypatch, capsys):
    import json as _json

    def fake_gh(args):
        if args[:2] == ["repo", "view"]:
            return _json.dumps({"defaultBranchRef": {"name": DEFAULT},
                                "nameWithOwner": "o/r"})
        if args[:2] == ["pr", "view"]:
            return _json.dumps({"number": 1, "baseRefName": DEFAULT,
                                "body": "Closes o/r#100", "headRefName": "h",
                                "state": "OPEN", "mergedAt": None})
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake_gh)
    assert cl.main(["--pr", "1", "--json"]) == 0
    assert "100" in capsys.readouterr().out


def test_main_drops_a_foreign_qualified_claim(monkeypatch, capsys):
    import json as _json

    def fake_gh(args):
        if args[:2] == ["repo", "view"]:
            return _json.dumps({"defaultBranchRef": {"name": DEFAULT},
                                "nameWithOwner": "o/r"})
        if args[:2] == ["pr", "view"]:
            return _json.dumps({"number": 1, "baseRefName": DEFAULT,
                                "body": "Closes other/r#100", "headRefName": "h",
                                "state": "OPEN", "mergedAt": None})
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake_gh)
    assert cl.main(["--pr", "1", "--json"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["findings"] == []


# ── cycle 4: the CARRIER side is parsed strictly, so a quotation cannot buy a pass ─

def test_a_carrier_that_only_QUOTES_the_keyword_does_not_carry_it():
    """Leniency is asymmetric: on the subject it costs a false finding (safe), on
    the carrier it would buy a `lands` (a pass — forbidden). So the carrier is
    parsed strictly (#10597 review c4-5)."""
    subject = _pr(100, SIBLING, "Closes #100")
    quoted_indented = _pr(101, DEFAULT, "Old shape, for context:\n\n    Closes #100\n",
                          head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, quoted_indented), DEFAULT)
    assert finding.verdict == "inert"


def test_a_carrier_that_only_QUOTES_the_keyword_in_a_blockquote_fence_does_not_carry_it():
    subject = _pr(102, SIBLING, "Closes #100")
    quoted = _pr(103, DEFAULT, "> ```\n> Closes #100\n> ```\n", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, quoted), DEFAULT)
    assert finding.verdict == "inert"


def test_a_carrier_that_really_states_the_keyword_still_lands():
    subject = _pr(104, SIBLING, "Closes #100")
    real = _pr(105, DEFAULT, "fix: the thing\n\nCloses #100\n", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, real), DEFAULT)
    assert finding.verdict == "lands"


# ── cycle 5: a PASS may not rest on a lenient-only match ─────────────────────

def test_a_default_base_pr_whose_keyword_is_only_in_a_code_block_is_unresolved():
    """Leniency is safe in the finding arms (a false warning) and unsafe in the
    `lands` arm (a false pass). A keyword that appears only inside an indented
    code block therefore downgrades to `unresolved` (#10597 review c5-2)."""
    pr = _pr(110, DEFAULT, "fix: the thing\n\nOld body said, for context:\n\n    Closes #10541\n")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved"
    assert "LENIENT parse" in finding.detail


def test_the_c5_detail_does_not_claim_github_ignores_a_list_continuation():
    """The strict parse refuses TWO shapes — an indented line, and a keyword whose
    reference sits across a blank line — and ONE of them is indentation. An
    indented line is a list item's continuation (where GitHub DOES fire) as often
    as it is code (see `test_indented_lines_that_continue_prose_are_still_claims`),
    so the finding may not assert what GitHub does with it — that is a fact this
    tool cannot see. Pre-fix wording did ("which GitHub does not act on")."""
    pr = _pr(114, DEFAULT, "- fix it\n    - Closes #10541\n")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved"
    assert "GitHub does not act on" not in finding.detail
    assert "at column 0" in finding.detail


def test_a_default_base_pr_with_a_plain_claim_still_lands():
    pr = _pr(111, DEFAULT, "fix: the thing\n\nCloses #10541\n")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "lands"


# ── cycle 5: blockquote PROSE is a claim (GitHub ignores code, not quotes) ───

def test_a_keyword_in_quoted_prose_is_still_a_claim():
    assert cl.parse_closing_refs("> earlier this said: Closes #10541\n") == [10541]


def test_a_carrier_restating_the_keyword_in_a_quoted_paragraph_lands():
    subject = _pr(112, SIBLING, "Closes #100")
    carrier = _pr(113, DEFAULT, "> re-stated for clarity: Closes #100\n", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert finding.verdict == "lands"


# ── the `gh` bridge: every failure is Unmeasurable, never a quiet empty answer ─

class _Completed:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_gh_maps_a_nonzero_exit_to_unmeasurable(monkeypatch):
    """`gh` exiting non-zero is the ONE bridge to reality. If `_gh` returned its
    (empty) stdout instead, `json.loads` on "" would be the only thing standing
    between a rate-limited API and a green step; the rc arm is pinned here."""
    monkeypatch.setattr(cl.subprocess, "run",
                        lambda *a, **k: _Completed(1, "", "rate limited"))
    with pytest.raises(cl.Unmeasurable) as exc:
        cl._gh(["pr", "view", "1"])
    assert "exited 1" in str(exc.value)
    assert "rate limited" in str(exc.value)


def test_gh_maps_a_missing_binary_to_unmeasurable(monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("gh")
    monkeypatch.setattr(cl.subprocess, "run", boom)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl._gh(["pr", "view", "1"])
    assert "not installed" in str(exc.value)


def test_gh_is_BOUNDED_so_a_stalled_call_cannot_hang_the_guard(monkeypatch):
    """#10634 review P2: `subprocess.run` carried no `timeout=`, so a stalled
    `gh` hung the run until the JOB-level timeout killed the always-on
    drift-guard job — `continue-on-error` covers a step's exit status, never a
    step that does not finish. The bound must be PASSED to the call (not just
    handled) and an expiry must be a VERDICT, never a hang and never a pass.
    """
    seen: dict = {}

    def stalled(cmd, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout") or 0)

    monkeypatch.setattr(cl.subprocess, "run", stalled)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl._gh(["pr", "view", "1"])
    bound = seen.get("timeout")
    assert isinstance(bound, int) and 0 < bound <= 300, (
        f"the gh call must carry a bounded positive timeout, got {bound!r}"
    )
    assert "did not answer within" in str(exc.value)


# ── the per-call bound is not enough: the RUN needs a deadline too ──────────

def test_gh_refuses_to_spend_the_run_budget_rather_than_the_whole_job(monkeypatch):
    """#10634 review P2. The per-call bound leaves the RUN unbounded: one `--pr`
    run issues `2 + L` sequential calls (identity, the PR, one per base branch up
    the stack) and `collect_stack` follows the stack as far as it goes, so a
    stalled API can consume the whole job cap (`.github/workflows/
    closes-coherence.yml`, `timeout-minutes: 10`) — and a JOB-level timeout is a
    red that `continue-on-error: true` cannot cover.

    Once the run's aggregate budget is spent, NO further call is made: the answer
    is UNMEASURED (exit 2), which is the same verdict the per-call bound gives.
    """
    monkeypatch.setattr(cl, "_run_budget_started",
                        time.monotonic() - (cl._GH_RUN_BUDGET_SECONDS + 1))
    calls: list = []
    monkeypatch.setattr(cl.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(cl.Unmeasurable) as exc:
        cl._gh(["pr", "view", "1"])
    assert calls == [], "an exhausted budget must not spend another call"
    assert "budget" in str(exc.value)


def test_gh_clamps_each_call_to_the_runs_REMAINING_budget(monkeypatch):
    """The last call must inherit what is LEFT of the run, not the full per-call
    cap — otherwise one call can overrun the budget by a whole minute."""
    monkeypatch.setattr(cl, "_run_budget_started",
                        time.monotonic() - (cl._GH_RUN_BUDGET_SECONDS - 5))
    seen: dict = {}

    def ok(cmd, **kwargs):
        seen.update(kwargs)
        return _Completed(0, "{}")

    monkeypatch.setattr(cl.subprocess, "run", ok)
    assert cl._gh(["x"]) == "{}"
    assert 0 < seen["timeout"] <= 6, seen
    assert seen["timeout"] < cl._GH_TIMEOUT_SECONDS, seen


# ── …and the justification for that budget is a fact about its HOST ──────────

#: The number of sequential `gh` calls the comment on `_GH_RUN_BUDGET_SECONDS`
#: names as enough to reach the job cap: "thirteen calls at the per-call cap".
#: It is the walk the pinned #10568/#10519 shape actually makes (`2 + L`: the
#: identity call, the PR, and one per base up a five-deep stack, measured), not a
#: ceiling — `collect_stack` walks as far as the stack goes.
_DOCUMENTED_CALLS = 13


def _timeout_minutes(path: Path) -> int:
    found = re.findall(r"^\s*timeout-minutes:\s*(\d+)\s*$", path.read_text(), re.M)
    assert len(found) == 1, (
        f"{path.name} carries {len(found)} `timeout-minutes` values ({found}); this "
        f"pin reads the ONE cap the tool's run budget must fit inside — re-measure it")
    return int(found[0])


def _workflows_running_the_tool() -> list[Path]:
    return sorted(
        path for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if "tools/closes_landing.py" in path.read_text()
    )


def test_the_run_budget_is_justified_by_the_cap_of_the_workflow_that_HOSTS_it():
    r"""#10634 review P3 at `15763ba97`. The comment justifying
    `_GH_RUN_BUDGET_SECONDS` said thirteen calls at the per-call cap "already
    reach the job's `timeout-minutes: 15`" — `drift-guard.yml`'s cap, left behind
    when the step moved to `closes-coherence.yml`, whose job caps at 10. That
    arithmetic is the whole argument for the aggregate budget, so a reader
    checking it against the tree found a workflow that does not host the tool.

    Correcting the prose alone would drift again on the next move, so the claim is
    MEASURED here against whichever workflow actually runs the tool: the budget
    must fit inside that job's cap (otherwise the job timeout, not the budget,
    ends the run — and a job-level kill is a red `continue-on-error` cannot cover),
    and the documented call count at the per-call cap must genuinely reach it.

    On the pre-fix tree this REDS on the prose leg: the comment region said
    `timeout-minutes: 15` while the only workflow that runs the tool caps at 10.
    """
    hosts = _workflows_running_the_tool()
    assert [path.name for path in hosts] == ["closes-coherence.yml"], (
        f"the tool's hosting workflow(s) are {[p.name for p in hosts]}: the run "
        f"budget's justification must be re-measured against the new cap")
    cap_minutes = _timeout_minutes(hosts[0])
    cap_seconds = cap_minutes * 60

    # The prose leg — the exact claim that drifted. It is read from the comment
    # block above `_GH_RUN_BUDGET_SECONDS`, because a `grep` of the whole file
    # would also find the OTHER workflow's cap (`drift-guard.yml:53`) quoted in a
    # sibling docstring and call the claim matched.
    source = MODULE_PATH.read_text()
    budget_at = source.index("_GH_RUN_BUDGET_SECONDS = ")
    comment_block = source[source.index("#: …and the bound is PER CALL", 0, budget_at):budget_at]
    named = re.findall(r"timeout-minutes:\s*(\d+)", comment_block)
    assert named == [str(cap_minutes)], (
        f"the comment justifying the run budget names `timeout-minutes: {named}` but "
        f"{hosts[0].name} caps its job at {cap_minutes}: that sentence IS the "
        f"argument for the budget, so it must name the workflow that hosts it")

    assert cap_seconds > cl._GH_RUN_BUDGET_SECONDS, (
        f"the {cl._GH_RUN_BUDGET_SECONDS}s run budget must end the run BEFORE the "
        f"{cap_seconds}s job cap does, or the job timeout wins and a stalled API "
        f"reds a job that `continue-on-error: true` cannot cover")
    assert cap_seconds < _DOCUMENTED_CALLS * cl._GH_TIMEOUT_SECONDS, (
        f"{_DOCUMENTED_CALLS} calls at the {cl._GH_TIMEOUT_SECONDS}s per-call cap "
        f"are {_DOCUMENTED_CALLS * cl._GH_TIMEOUT_SECONDS}s and must reach the "
        f"{cap_seconds}s job cap — that arithmetic IS the justification for an "
        f"aggregate budget; if it no longer holds, re-measure the comment")


def test_main_starts_a_FRESH_run_budget(monkeypatch, tmp_path):
    """A CLI process asks one question; two invocations in one process (the
    offline tests here do exactly that) must not share a deadline."""
    monkeypatch.setattr(cl, "_run_budget_started", time.monotonic() - 10_000)
    body = tmp_path / "b.md"
    body.write_text("Closes #1\n", encoding="utf-8")
    assert cl.main(["--body-file", str(body), "--base", "main"]) == 0
    assert cl._run_budget_started is None


@pytest.mark.parametrize("payload,expected", [
    ("not json", "repo identity could not be read"),
    ('{"defaultBranchRef": {"name": ""}, "nameWithOwner": "o/r"}',
     "empty default branch"),
    ('{"defaultBranchRef": {"name": "main"}, "nameWithOwner": ""}',
     "no nameWithOwner"),
])
def test_fetch_identity_refuses_what_it_cannot_use(monkeypatch, payload, expected):
    """An empty `nameWithOwner` must NOT degrade to `None`: that drops every
    same-repo qualified claim as foreign — the false pass this read exists to
    remove (#10597 review c3-5, previously asserted only in prose)."""
    monkeypatch.setattr(cl, "_gh", lambda _a: payload)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl.fetch_identity("o/r")
    assert expected in str(exc.value)


def test_fetch_pr_refuses_a_malformed_payload(monkeypatch):
    monkeypatch.setattr(cl, "_gh", lambda _a: '{"baseRefName": "main"}')
    with pytest.raises(cl.Unmeasurable):
        cl.fetch_pr(1, "o/r")


def test_fetch_prs_by_head_refuses_a_malformed_payload(monkeypatch):
    monkeypatch.setattr(cl, "_gh", lambda _a: '{"number": 1}')
    with pytest.raises(cl.Unmeasurable):
        cl.fetch_prs_by_head("b", "o/r")


def test_sweep_refuses_a_malformed_population(monkeypatch):
    monkeypatch.setattr(cl, "_gh", lambda _a: "nope")
    with pytest.raises(cl.Unmeasurable):
        cl.sweep("o/r", DEFAULT)


# ── the depth cap: the anti-infinite-loop arm must land, not pass ────────────

def _stack(depth, keyword_on_top="Closes #100"):
    """A subject on `s0 <- s1 <- … <- top(main)`: `depth` hops to the top PR."""
    mid = [_pr(300 + i, f"s{i + 1}", "", head=f"s{i}") for i in range(depth)]
    top = _pr(399, DEFAULT, keyword_on_top, head=f"s{depth}")
    subject = _pr(299, "s0", "Closes #100", head="subj")
    return subject, _by_head(subject, *mid, top)


@pytest.mark.parametrize("depth,verdict", [
    (cl.MAX_STACK_DEPTH - 2, "lands"),
    (cl.MAX_STACK_DEPTH - 1, "unresolved"),
])
def test_the_stack_depth_cap_is_off_by_at_most_nothing(depth, verdict):
    """The carrier walk must return None — not loop, and not `lands` — when the
    top of the stack sits beyond the cap. The boundary is pinned on BOTH sides so
    an off-by-one in the loop cannot silently turn the deepest real stacks into
    `unresolved`, or a runaway walk into a pass."""
    subject, by_head = _stack(depth)
    (finding,) = cl.classify(subject, by_head, DEFAULT)
    assert finding.verdict == verdict


def test_the_depth_cap_message_says_measurement_stopped_not_that_a_pr_is_missing():
    """The cap is the THIRD cause of `carrier is None`, and every PR in it exists.

    Reusing the "no PR with that head branch exists" sentence here told the reader
    evidence was missing when the walk had simply stopped, and pointed at a PR
    that is not missing (#10634 review P3). The sibling test above pins the
    verdict; this pins the sentence.
    """
    subject, by_head = _stack(cl.MAX_STACK_DEPTH - 1)
    (finding,) = cl.classify(subject, by_head, DEFAULT)
    assert finding.verdict == "unresolved"
    assert "no PR with that head branch exists" not in finding.detail
    assert str(cl.MAX_STACK_DEPTH) in finding.detail
    assert "not a missing PR" in finding.detail


# ── collect_stack: the LIVE walk (two hops, and a base with no PR) ───────────

def _fake_gh_by_head(table):
    import json as _json

    def fake(args):
        return _json.dumps(table.get(args[args.index("--head") + 1], []))
    return fake


def test_collect_stack_walks_more_than_one_hop(monkeypatch):
    """The walk is pinned on in-memory records, but `collect_stack` is
    what BUILDS them from `gh`: a two-hop live stack must terminate at the
    default-branch PR, or `classify` is handed an incomplete map and reports
    `unresolved` on a stack that is fine."""
    monkeypatch.setattr(cl, "_gh", _fake_gh_by_head({
        "s0": [{"number": 5, "baseRefName": "s1", "body": "", "headRefName": "s0",
                "state": "OPEN", "mergedAt": None}],
        "s1": [{"number": 6, "baseRefName": DEFAULT, "body": "Closes #100",
                "headRefName": "s1", "state": "OPEN", "mergedAt": None}],
    }))
    subject = _pr(4, "s0", "Closes #100")
    by_head = cl.collect_stack([subject], "owner/name", DEFAULT)
    assert by_head["s1"].number == 6
    (finding,) = cl.classify(subject, by_head, DEFAULT)
    assert finding.verdict == "lands"
    assert finding.carrier == 6


def test_collect_stack_stops_when_a_base_branch_has_no_pr(monkeypatch):
    monkeypatch.setattr(cl, "_gh", _fake_gh_by_head({}))
    subject = _pr(7, "no-such-branch", "Closes #100")
    by_head = cl.collect_stack([subject], "owner/name", DEFAULT)
    assert "no-such-branch" not in by_head
    (finding,) = cl.classify(subject, by_head, DEFAULT)
    assert finding.verdict == "unresolved"


def test_sweep_with_no_stacked_roots_reports_nothing(monkeypatch):
    """The population query can come back with only default-branch PRs; that is
    an empty report, and it must not reach `collect_stack` (which would then ask
    `gh` about branches that were never in question)."""
    import json as _json

    def fake(args):
        assert "--head" not in args, "sweep() walked a stack it had no root for"
        return _json.dumps([
            {"number": 90, "baseRefName": DEFAULT, "body": "Closes #100",
             "headRefName": "on-main", "state": "OPEN", "mergedAt": None},
        ])

    monkeypatch.setattr(cl, "_gh", fake)
    assert cl.sweep("owner/name", DEFAULT) == []


# ── the two PROSE arms of `main` (the shapes a reader and the step see) ──────

def test_annotate_suppresses_a_landing_finding(tmp_path, capsys):
    """The workflow step prints `--annotate`; a `lands` ref must produce no
    annotation at all, or every correct PR grows a warning and the step gets
    muted — taking the real inert warning with it."""
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    assert cl.main(["--body-file", str(body), "--base", DEFAULT, "--annotate"]) == 0
    assert capsys.readouterr().out == ""


def test_annotate_emits_the_unresolved_warning(tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("Closes #10541\n", encoding="utf-8")
    assert cl.main(["--body-file", str(body), "--base", SIBLING, "--annotate"]) == 3
    out = capsys.readouterr().out
    assert out.startswith("::warning title=Unresolved Closes #10541::"), out


def test_the_empty_report_does_not_claim_a_non_default_base(tmp_path, capsys):
    """The empty message is also reached by `--pr` on a PR whose base IS the
    default branch; asserting "non-default base" there tells the reader a false
    fact about the PR in front of them."""
    body = tmp_path / "body.md"
    body.write_text("Refs #10541 — no closure claimed\n", encoding="utf-8")
    assert cl.main(["--body-file", str(body), "--base", DEFAULT]) == 0
    out = capsys.readouterr().out
    assert "no closing refs to judge" in out
    assert "non-default base" not in out


# ── cycle 7: a fence inside a blockquote does not swallow out-of-quote prose ──

def test_a_keyword_after_a_blockquote_fence_ends_is_a_real_claim():
    """A fenced block has no lazy continuation: once a line drops its `>` the
    blockquote has ended, so the rest is top-level prose and its keyword is a
    real claim. Blanking it was a silent PASS over the inert shape (review c7-2)."""
    body = "fix: something\n\n> ```\nCloses #10541\n> ```\n"
    assert cl.parse_closing_refs(body) == [10541]
    assert cl.parse_closing_refs(body, None, strict=True) == [10541]


def test_an_inert_stacked_keyword_after_a_blockquote_fence_still_fires():
    subject = _pr(120, SIBLING, "fix: something\n\n> ```\nCloses #10541\n> ```\n")
    carrier = _pr(121, DEFAULT, "no keyword", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert finding.verdict == "inert"


def test_a_keyword_inside_the_blockquote_fence_is_still_stripped():
    body = "> ```\n> Closes #10541\n> ```\n"
    assert cl.parse_closing_refs(body) == []


# ── cycle 8: the container ENDS the fence (blank line, or lost indent) ───────

@pytest.mark.parametrize("body,expected", [
    # A blank line ends a blockquote, and a fenced block ends with its container,
    # so the `> Closes` after it is a NEW blockquote's prose — a real claim.
    ("> ```\n> a\n\n> Closes #10541\n> ```\n", [10541]),
    # A 4-space indented ``` is an INDENTED CODE BLOCK, not a fence, so the
    # column-0 prose after it is prose (review c8-2).
    ("    ```\nCloses #10541\n", [10541]),
    ("    ```\nCloses #12\n    ```\n", [12]),
    # …while content that stays inside the container is still stripped.
    ("> ```\n> a\n>\n> Closes #20\n> ```\n", []),
    ("- item:\n\n  ```\n  Closes #40\n  ```\n", []),
])
def test_the_container_ends_the_fence(body, expected):
    assert cl.parse_closing_refs(body) == expected
    assert cl.parse_closing_refs(body, None, strict=True) == expected


# ── cycle 9: fence shape truth, cross-checked against CommonMark ────────────

@pytest.mark.parametrize("body,expected,why", [
    # CODE per CommonMark, so the keyword is a quotation and must NOT be a finding
    # — and must never be a `lands`. A 3-space opener IS a fence.
    ("   ```\nCloses #10541\n   ```\n", [], "3-space opener is a fence"),
    (">  ```\n> Closes #10541\n>  ```\n", [], "quoted fence, 1-space indent"),
    # A depth-0 fence has no container: `> ``` ` inside it is CONTENT, not a
    # closer. Treating it as one blanked the prose after the block (c9-2(i)).
    ("```\n> ```\n```\nCloses #10541\n", [10541], "quoted fence inside a fence"),
    # A closing fence may not carry an info string (c9-2(ii)).
    ("```\na\n``` x\n```\nCloses #10541\n", [10541], "closer with info string"),
    # A backtick opener whose info string contains a backtick is not a fence (c9-2(iii)).
    ("``` `x`\nCloses #10541\n```\n", [10541], "backtick in info string"),
    # A tab is 4 columns: an INDENTED CODE BLOCK, so the prose after it is prose.
    ("\t```\n  Closes #10541\n", [10541], "tab-indented opener"),
    ("    ```\nCloses #10541\n", [10541], "4-space indented opener"),
    # A fence may open ON the LIST-MARKER line itself (review finding P1):
    # CommonMark puts the block at the item's CONTENT column, and `- ``` ` is a
    # fenced block, not prose. Reading that line as prose made the whole block
    # prose, so a QUOTED `Closes #N` inside it was a claim — and `lands`, the one
    # outcome this tool forbids. Each `expected` below was checked against a real
    # CommonMark renderer (`markdown_it`), never reasoned about.
    ("- ```\n  Closes #100\n  ```\n", [], "fence opened on a dash-marker line"),
    ("- ```bash\n  echo Closes #100\n  ```\n", [], "marker-line fence with an info string"),
    ("* ```\n  Closes #100\n  ```\n", [], "fence opened on a star-marker line"),
    ("1. ```\n   Closes #100\n   ```\n", [], "fence opened on an ordered-marker line"),
    # Content column 4 (`10. `), past the absolute limit a fence opener may have:
    # the block is still a fence, and its four-space closer still closes it.
    ("10. ```\n    Closes #100\n    ```\n", [], "marker-line fence, content column 4"),
    ("-  ```\n   Closes #100\n   ```\n", [], "marker-line fence, 2-space gap"),
    # A gap wider than four columns starts an INDENTED code block instead, so the
    # column-0 line after it is genuine prose — the over-report side, pinned
    # because guessing the other way would DROP a real claim.
    ("-     ```\nCloses #100\n", [100], "gap > 4 is an indented code block"),
    # A fence opened on the marker line ends with its ITEM, exactly as one opened
    # on the next line does.
    ("- ```\n  a\n- Closes #100\n", [100], "a new item ends a marker-line fence"),
    ("- ```\nCloses #100\n", [100], "a column-0 line ends the item and the fence"),
    # A closer may sit up to three columns past the content column (c10-3's rule,
    # applied to the marker-line form).
    ("- item\n  ```\n  Closes #40\n    ```\nCloses #999\n", [999],
     "a closer 2 columns past the content column still closes"),
])
def test_fence_shapes_match_commonmark(body, expected, why):
    assert cl.parse_closing_refs(body) == expected, why
    assert cl.parse_closing_refs(body, None, strict=True) == expected, why


def test_a_carrier_that_quotes_the_keyword_in_a_marker_line_fence_does_not_carry_it():
    """The `lands` direction of the P1 above, which is the one that must never
    fire: the CARRIER only QUOTES `Closes #N` inside a `- ``` ` block, so nothing
    will close the issue. The pre-fix tool returned `lands` — a pass manufactured
    by a code block."""
    stacked = _pr(70, SIBLING, "Closes #100")
    carrier = _pr(71, DEFAULT, "- ```\n  Closes #100\n  ```\n", head=SIBLING)
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert", finding
    assert finding.carrier == 71
    assert "Re-state the keyword on PR #71" in finding.detail


def test_a_code_block_on_a_marker_line_cannot_buy_a_lands_on_the_default_base():
    pr = _pr(72, DEFAULT, "- ```bash\n  echo Closes #100\n  ```\n")
    assert cl.classify(pr, _by_head(pr), DEFAULT) == []


def test_the_item_content_column_collapses_a_wide_gap_to_one():
    """Round 30 review, P2 — ONE definition of CommonMark's item CONTENT column.

    `_fence_candidate` collapsed a gap wider than four spaces to one column (§5.2: such a line
    starts an INDENTED code block, not a wider item); the item PUSH used
    `len(marker.group(0))`, i.e. marker + the WHOLE gap. Harmless while only the fence path read
    it — and a class-A false PASS once arm 2's `indented_code` threshold was read from the push,
    because the blanking threshold then sat up to four columns too far out:

        `-     item` + `        - [ ] x`   renderer `<pre><code>`  ->  must be hidden, was CLEARED

    These expected columns were checked against `MarkdownIt("commonmark")` (the renderer puts
    `item` itself in `<pre><code>` at a 5-space gap, which is the tell that the content column is
    2 and not 6).
    """
    assert cl._content_column("- item") == 2
    assert cl._content_column("-     item") == 2          # gap > 4 collapses to ONE column
    assert cl._content_column("1. Criteria") == 3
    assert cl._content_column("10. Criteria") == 4
    assert cl._content_column("    - item") == 6           # 4 columns of indent + `- `
    assert cl._content_column("plain prose") is None
    # …and the two call sites agree, which is the point: the fence path and the item push read
    # the same column for the same line (a marker line that OPENS a fence carries its content
    # column as `close_base`; a gap > 4 opens no fence at all, so it is asserted above).
    for rest in ("- item", "1. Criteria", "10. Criteria", "    - item"):
        marker = cl._LIST_MARKER_RE.match(rest).group(0)
        fence = cl._fence_candidate("", marker + "```", [])
        assert cl._content_column(rest) == fence.close_base, rest
    assert cl._fence_candidate("", "-     ```", []) is None, "gap > 4 opens no fence"


def test_strip_code_strict_is_keyword_only():
    """Round 31 review, P3 — the record said `strict` was keyword-only; the code was not.

    The round-30 record (and the round-28 one it repeated) states `strip_code(body, *, strict=False)`.
    The signature kept `strict` positional and three in-module callers passed it that way, so the
    record and the artifact disagreed in the one direction that breaks the next lane who makes them
    match. The `*` is real now; this pin is what keeps it real.
    """
    assert cl.strip_code("Refs #1\n", strict=True) == cl.strip_code("Refs #1\n")
    with pytest.raises(TypeError):
        cl.strip_code("Refs #1\n", True)
    with pytest.raises(TypeError):
        cl.strip_code("Refs #1\n", False, {})


def test_a_line_that_IS_prose_but_ENDS_the_paragraph_does_not_open_one():
    """Round 34 review, P2 — the FAIL-OPEN half of the consumer-side rule.

    `_ordered_marker_at_least_two` narrowed `own_code` to keep an ordered marker `>= 2` that CONTINUES
    an open paragraph, and read "is a paragraph open" as "was the previous line emitted as prose".
    Four families are emitted as prose and END the paragraph anyway, so the NEXT line was read as a
    continuation and a wide-gap marker after them was KEPT — a quoted `Closes #N` credited as a claim
    in BOTH arms, which is the direction this module forbids. The renderer is the oracle:

        `# H`         + `2.     Closes #100`  -> `<h1>H</h1>` then a new `<ol>`: `<pre><code>`  CODE
        `***`         + …                    -> `<hr />` then the same                            CODE
        `Notes`/`---` + …                    -> a SETEXT heading, then the same                   CODE
        `    Notes`   + …                    -> an INDENTED code block, then the same             CODE

    The container test is the block grammar's and NOT `strict`'s, which is why `- item` + `    cont`
    still opens a paragraph (4 <= 2 + 3) and the marker after it really is prose — that pair is the
    control, pinned beside the four, and it is what keeps this from becoming "blank every wide gap".
    """
    md = MarkdownIt("commonmark")
    text = "Closes #100"

    def block_kind(body: str) -> str | None:
        for token in md.parse(body):
            if text in getattr(token, "content", ""):
                return token.type
        return None

    def kept(body: str, strict: bool) -> bool:
        return any(text in line for line in cl.strip_code(body, strict=strict).splitlines())

    # The paragraph the consumer-side rule must still honour (the fix this pins did not lose it).
    for body in ("Notes\n2.     Closes #100\n",
                 "To be derived.\n2.     Closes #100\n"):
        assert block_kind(body) == "inline", body
        assert cl.parse_closing_refs(body) == [100], body
        assert cl.parse_closing_refs(body, None, strict=True) == [100], body
    # …including one INSIDE a list item, where the block grammar's 4+3 allowance (not `strict`'s)
    # is what says the paragraph is still open. Lenient only: `strict` blanks every indented line by
    # its own contract, so the `    cont` above it is blanked there and this shape is one of its
    # declared over-blanks (the direction that costs a warning, not a pass).
    item_body = "- item\n    cont\n   2.     Closes #100\n"
    assert block_kind(item_body) == "inline", item_body
    assert cl.parse_closing_refs(item_body) == [100], item_body
    assert cl.strip_code("- item\n   2.     Closes #100\n", strict=True) != "", "unindented control"
    assert cl.parse_closing_refs("- item\n   2.     Closes #100\n", None, strict=True) == [100]
    # …and the four families that END a paragraph, each of which was credited before this fix.
    # `<div>` is an HTML block: its content is raw HTML, so a `Closes #N` there is a quotation too.
    for body, kind, why in (("# H\n2.     Closes #100\n", "code_block", "an ATX heading"),
                            ("***\n2.     Closes #100\n", "code_block", "a thematic break"),
                            ("Notes\n---\n2.     Closes #100\n", "code_block", "a setext underline"),
                            ("    Notes\n2.     Closes #100\n", "code_block",
                             "an indented code block (§4.4)"),
                            ("<div>\n2.     Closes #100\n", "html_block", "an HTML block")):
        assert block_kind(body) == kind, (why, body)
        assert cl.parse_closing_refs(body) == [], (why, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [], (why, body)
    # The sweep: every paragraph-ENDER x marker x gap x indent, both arms, against the renderer.
    sweep = 0
    for ctx in ("# H\n", "***\n", "Notes\n---\n", "    Notes\n", "<div>\n"):
        for marker in ("-", "*", "+", "1.", "2.", "10.", "2)"):
            for gap in range(1, 10):
                for indent in (0, 1, 2, 3):
                    sweep += 1
                    body = ctx + " " * indent + marker + " " * gap + text + "\n"
                    kind = block_kind(body)
                    assert kind is not None, (ctx, marker, gap, indent)
                    for strict in (False, True):
                        # Never keep a line the renderer puts in a CODE block: crediting a quotation
                        # is the fail-open direction, in the finding arm and in the PASS arm alike.
                        assert not (kind == "code_block" and kept(body, strict)), \
                            ("fail-open", ctx, marker, gap, indent, strict)
    assert sweep == 1260, sweep
    # ⚠️ DECLARED RESIDUAL, measured here so it is not rediscovered as news: a marker line with a
    # gap of 4 or LESS inside an HTML block is KEPT (`own_code` only ever claimed the gap > 4
    # family, at every head this series touched), so `<div>` + `- Closes #100` is credited while the
    # renderer puts it in `html_block`. Pre-existing, outside this fix's rule, and left alone rather
    # than widened here: blanking the CONTENT of every HTML block is its own change with its own
    # blast radius. The two directions are separate tests (`…the paragraph does not open one`
    # above closes the gap > 4 half).
    assert block_kind("<div>\n- Closes #100\n") == "html_block"
    assert cl.parse_closing_refs("<div>\n- Closes #100\n") == [100], "residual, not a regression"


def test_the_paragraph_ender_is_NOT_over_applied_in_either_direction():
    """Round 36 review — the two halves `_leaves_no_paragraph` got wrong, pinned together.

    A predicate that decides "did this line leave a paragraph open?" while holding none of the state
    that decision needs fails in BOTH directions, and the two halves are one test because fixing one
    is how the other gets re-opened:

    **A LEAF BLOCK ON A MARKER LINE ends the paragraph too** (false PASS, both arms). `rest` starts
    with `-`, so testing only `rest` read `- # H` as prose-that-leaves-a-paragraph-open and credited
    a quoted close below it; the line's own content starts at its CONTENT COLUMN:

        `- # H` + `  2.     Closes #100`   renderer `<li><h1>H</h1><ol start="2"><li><pre><code>`

    **Three ordinary paragraphs were being treated as paragraph ENDERS** (lost closes, a REGRESSION
    against `origin/main`): a bare `=` is a paragraph rather than a setext underline (there is no
    paragraph above it to underline), `<3 items` and `<https://e.com>` are paragraphs rather than
    HTML blocks (a start condition needs a tag NAME), and §4.4 says an indented code block cannot
    INTERRUPT a paragraph — so a 4-column line under an open paragraph is its continuation, not code.
    Measured on `origin/main`: 228 lenient and 72 strict closes were lost before this was fixed.

    The controls that must NOT move: a genuine `<div>` still ends the paragraph (raw HTML is not
    markdown, so a close inside it is a quotation — GitHub does not act on it), and a wide-gap bullet
    that opens a real item is still code.
    """
    md = MarkdownIt("commonmark")

    def token_type(body: str) -> str | None:
        for token in md.parse(body):
            if "Closes #100" in getattr(token, "content", ""):
                return token.type
        return None

    # (A) A leaf block sitting ON the marker line — the false PASS.
    for leaf in ("- # H", "- ***", "1. # H"):
        body = leaf + "\n  " + "2.     Closes #100\n"
        assert token_type(body) == "code_block", (leaf, body)
        assert cl.parse_closing_refs(body) == [], (leaf, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [], (leaf, body)
    # (B) The shapes that are ordinary PARAGRAPHS, so the close below them is real. Lenient only for
    # the indented pair: `strict` blanks every indented line by its own documented contract.
    for body, why in (("=\n2.     Closes #100\n", "a bare `=` is a paragraph, not an underline"),
                      ("==\n2.     Closes #100\n", "…and `==` with nothing to underline either"),
                      ("<3 items\n2.     Closes #100\n", "`<3` is not an HTML block start"),
                      ("<https://e.com>\n2.     Closes #100\n", "an autolink is a paragraph"),
                      ("**bold**\n2.     Closes #100\n", "bold text is a paragraph")):
        assert token_type(body) == "inline", (why, body)
        assert cl.parse_closing_refs(body) == [100], (why, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [100], (why, body)
    for body, why in (("Notes\n    cont\n2.     Closes #100\n",
                       "§4.4: an indented code block cannot INTERRUPT a paragraph"),
                      ("Notes\n    -     Closes #100\n",
                       "…so a 4-column BULLET line under one is a continuation, not code")):
        assert token_type(body) == "inline", (why, body)
        assert cl.parse_closing_refs(body) == [100], (why, body)
    # (C) The controls, which must NOT move. A genuine HTML block still ends the paragraph (raw HTML
    # is not markdown); a wide-gap bullet that opens a real item is still code; and the `- item` +
    # `  2.` lazy continuation is still prose.
    # (the TEXT lands in the `html_block` token, not a `code_block` — raw HTML is passed through,
    # which is exactly why GitHub does not act on a keyword inside it)
    assert token_type("<div>\n2.     Closes #100\n") == "html_block"
    assert cl.parse_closing_refs("<div>\n2.     Closes #100\n") == []
    assert cl.parse_closing_refs("Notes\n-     Closes #100\n") == []
    assert cl.parse_closing_refs("Notes\n\n-     Closes #100\n") == []
    assert token_type("- item\n  2.     Closes #100\n") == "inline"
    assert cl.parse_closing_refs("- item\n  2.     Closes #100\n") == [100]


def test_a_marker_that_opens_NO_item_has_no_content_column_to_inspect():
    """Round 37 review, P2 — the ORDERED half of the leaf-block rule, and its second mechanism.

    Round 36 taught `_leaves_no_paragraph` to inspect a marker line's CONTENT COLUMN, which fixed
    `- # H` and left its ordered sibling wrong: an ordered marker >= 2 under an open paragraph opens
    NO item (§5.2's consumer rule), so its `# H` is INLINE TEXT rather than a heading and the
    paragraph is still open. Reading it as a heading ended the paragraph and blanked the real
    `Closes #100` below it — a lost close in the direction this module ranks second-worst.

    ⛔ AND THE PREDICATE ALONE DOES NOT FIX IT. The line also pushed a list item, so the NEXT line was
    measured against a content column the renderer never created and came out blanked anyway. The
    reviewer measured the pair: predicate-only `[]`, predicate + no-push `[100]`. Both halves are
    here because each is invisible while the other is wrong.
    """
    md = MarkdownIt("commonmark")

    def token_type(body: str) -> str | None:
        for token in md.parse(body):
            if "Closes #100" in getattr(token, "content", ""):
                return token.type
        return None

    for leaf, why in (("2. # H", "an ordered marker >= 2 opens no item, so `# H` is text"),
                      ("2. ***", "…and `***` is a horizontal rule only inside an item"),
                      ("10. # H", "any start number above 1")):
        body = "Notes\n" + leaf + "\n3.     Closes #100\n"
        assert token_type(body) == "inline", (why, body)
        assert cl.parse_closing_refs(body) == [100], (why, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [100], (why, body)
    # The controls: a BULLET opens its item even under a paragraph, so its leaf block DOES end it…
    body = "- # H\n  2.     Closes #100\n"
    assert token_type(body) == "code_block", body
    assert cl.parse_closing_refs(body) == [], body
    # …and with no paragraph above, the ordered marker opens its item too.
    body = "2. # H\n  3.     Closes #100\n"
    assert cl.parse_closing_refs(body) in ([], [100]), body


def test_the_HTML_block_rule_is_the_SPEC_s_two_rules_not_one():
    """Round 37 review, P2 — §4.6 has types 1-6 (they CAN interrupt) and type 7 (it CANNOT).

    One regex accepting "a tag name" treated `<span>`, `<br />`, `<custom-tag>` and `<x` as
    paragraph enders, so a real `Closes #N` below them was blanked — 24 lenient + 24 strict closes
    lost per shape against `origin/main`, 0 fixed. Type 6 requires a name FROM THE SPEC'S LIST, and
    type 7 — a complete tag alone on a line — never ends a paragraph at all:

        `<div>`            in the type-6 list  -> a real HTML block, ends the paragraph
        `<span>x</span>`   type 7              -> the marker line below it is still prose
        `<br />`           type 7              -> same (the regex's docstring used to cite it wrongly)
        `<x`               not a complete tag  -> same
    """
    md = MarkdownIt("commonmark")

    def token_type(body: str) -> str | None:
        for token in md.parse(body):
            if "Closes #100" in getattr(token, "content", ""):
                return token.type
        return None

    for tag in ("<span>x</span>", "<br />", "<x", "<custom-tag>", "<em>x</em>"):
        # AFTER a paragraph the marker line is unambiguously prose — the shape round 36 broke.
        body = "Notes\n" + tag + "\n2.     Closes #100\n"
        assert token_type(body) == "inline", (tag, body)
        assert cl.parse_closing_refs(body) == [100], (tag, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [100], (tag, body)
        # AT THE DOCUMENT START the renderer makes the tag a type-7 html_block that swallows the
        # marker line, and this module CREDITS the keyword there — which is its standing stance for
        # HTML-block content (only FENCED content is blanked; see the `<div>` assertion below). The
        # ender therefore leaves the paragraph open rather than manufacturing a boundary the module
        # does not otherwise honour.
        body = tag + "\n2.     Closes #100\n"
        assert cl.parse_closing_refs(body) == [100], (tag, body)
    for tag in ("<div>", "<blockquote>", "<p>", "<!-- c -->", "<?php"):
        body = tag + "\n2.     Closes #100\n"
        assert cl.parse_closing_refs(body) == [], (tag, body)
    # What an HTML block does NOT do is hide its own text: only FENCED content is blanked, and that
    # is parity with every head before this one — stated here so the ender's justification cannot be
    # read as a claim about GitHub's behaviour for raw HTML.
    assert cl.parse_closing_refs("<div>\nCloses #100\n</div>\n") == [100]


def test_the_44_indent_arm_is_a_DISTANCE_from_the_container_not_no_container():
    """Round 37 review, P2 — §4.4's rule is about the PARAGRAPH, not about the outermost container.

    Round 36 bounded the lazy-indent excuse to `container is None`, which lost the same close one
    level in: at content-column + 4 inside an item — or on a lazy line that dropped its `> ` — an
    open paragraph makes the line its continuation, so the close is prose GitHub acts on:

        `- Notes` + `      -     Closes #100`   renderer `<p>`       (6 columns = 2 + 4)
        `> Notes` + `    -     Closes #100`     renderer `<p>`       (a lazy line may drop its `>`)
        `- Notes` + `    -     Closes #100`     renderer `<pre>`     CONTROL: 4 columns = 2 + 2

    So the term is `rest_indent > container.rest_col + 4` — a DISTANCE — and the flip really is at 4
    past the content column, holding at every indent 0-12. The marker half keeps its depth term: a
    marker at another blockquote depth opens its own list.
    """
    md = MarkdownIt("commonmark")

    def token_type(body: str) -> str | None:
        for token in md.parse(body):
            if "Closes #100" in getattr(token, "content", ""):
                return token.type
        return None

    for body, why in (("- Notes\n      -     Closes #100\n", "6 columns = item content col 2 + 4"),
                      ("> Notes\n    -     Closes #100\n", "a lazy continuation may drop its `>`")):
        assert token_type(body) == "inline", (why, body)
        assert cl.parse_closing_refs(body) == [100], (why, body)
    # The control, which must stay CODE: 4 columns is only 2 past the item's content column.
    control = "- Notes\n    -     Closes #100\n"
    assert token_type(control) == "code_block", control
    assert cl.parse_closing_refs(control) == [], control


# ── cycle 10: containers by DEPTH, the ending line is fresh, list items end ──

@pytest.mark.parametrize("body,expected,why", [
    # `>`, `> `, `  > ` are ONE container to CommonMark; comparing the raw prefix
    # strings meant a legal closer was never recognised and the rest blanked.
    (">```\n>Closes #123\n> ```\n> Closes #10541\n", [10541], "c10-1 prefix by depth"),
    ("> ```\n  > ```\n> Closes #10541\n", [10541], "c10-1 prefix spacing"),
    # The line that ends a container is a FRESH line: if it opens a fence, what
    # follows is CODE, and reporting it as a claim would be a false `lands`.
    ("> ```\n```\nCloses #10541\n", [], "c10-2 the ending line may open a fence"),
    # A fence opened inside a list item ends when the item's content ends.
    ("- item\n  ```\nCloses #10541\n", [10541], "c10-3 list item dedent"),
    ("- item:\n\n  ```\n  Closes #40\n  ```\n", [], "c10-3 content stays in the item"),
])
def test_container_depth_and_list_endings(body, expected, why):
    assert cl.parse_closing_refs(body) == expected, why
    assert cl.parse_closing_refs(body, None, strict=True) == expected, why


# ── #10634 review: the CONTENT COLUMN, and a renderer as the oracle ─────────
#
# Three false `lands` (a claim inside a rendered code block read as prose) and
# one lost claim, all from measuring indentation against the wrong column.

@pytest.mark.parametrize("body,expected,why", [
    # §4.5: "A fenced code block begins with a code fence, preceded by up to three
    # spaces of indentation" — inside a list item that budget starts at the ITEM's
    # content column, not at column 0. Measured from column 0, a real fence was
    # refused and its content read as prose (a `lands` in BOTH parses).
    ("- prose\n    ```\n  Closes #1001\n", [], "opener 4 cols from col 0, 2 past the item"),
    ("1. item\n     ```\n   Closes #1001\n", [], "ordered item, opener past the absolute limit"),
    # §4.5 ends the block at "the end of the containing block": a content line may
    # sit BELOW the opener's own indent and still be inside the item. Using the
    # run's column as the item's edge ended the fence early (a `lands`).
    ("- item\n   ```\n  Closes #1001\n", [], "content line below the run, inside the item"),
    # …and the over-report side is NOT traded away: a column-0 line really does
    # end the item, so the claim after it is prose.
    ("- item\n   ```\nCloses #1001\n", [1001], "a column-0 line ends the item"),
    # A marker line INSIDE a fence is CONTENT, not a new item. Counting it pushed
    # a phantom item whose "dedent" ended the NEXT fence early, so that block's
    # content read as prose (a `lands`).
    ("  ```\n- a\n  ```\n  ```\n- Closes #1002\n", [], "marker inside a fence is content"),
    # A closer's allowance is measured from the CONTAINING BLOCK's content column,
    # never from the opening run's own indent (§4.5: "their indentation need not
    # match that of the opening fence"). A top-level fence opened at 2 columns
    # therefore does NOT close on a 4-space run — the renderer keeps it in
    # `<pre><code>` — and everything after it is CONTENT, not a claim.
    ("  ```\n  a\n    ```\nCloses #1001\n", [], "a 4-space closer at top level is CONTENT"),
    # …while the SAME run does close a fence inside a 2-column item, whose budget
    # is content column + 3. Both directions, or the fix is a coin flip.
    ("- item\n  ```\n  a\n    ```\nCloses #1001\n", [1001],
     "a 4-space closer inside a 2-column item still closes"),
    # A marker-line fence INSIDE a quote (`> - ``` `): the item's edge is measured
    # past the quote's own marker, so its content lines are NOT dedents. Reading
    # the raw indent against that column ended the block on its own first line
    # and the quoted keyword bought a `lands` — live at ca92b373f.
    ("> - ```\n>   Closes #1001\n", [], "marker-line fence inside a quote"),
    # …including after a BLANK quoted line, which does not end a list item and
    # whose `>` carries no content: treating it as a dedent blanked the rest.
    ("> - ```py\n>\n>   Closes #1001\n", [], "blank quoted line in a quoted item fence"),
    # A `>` inside a depth-0 item fence is LITERAL content (`_split_prefix` would
    # read it as a marker and lose the item's edge).
    ("- ```\n   > Closes #1001\n", [], "a literal `>` inside a depth-0 item fence"),
    # …and the over-report side of the same rule: at depth 0 a quote line starts at
    # column 0, which really does end the item, so that claim IS prose.
    ("- ```\n>   Closes #1001\n", [1001], "a blockquote line ends a depth-0 item fence"),
    # Spaces BEFORE a quote marker are the marker's allowance, not the item's
    # content: they end a quoted item, and the line becomes a paragraph of the
    # quote — prose, so it is still a claim (over-report direction, correct).
    ("> - ```\n   > Closes #1001\n", [1001], "indented quote marker ends a quoted item"),
])
def test_a_fence_is_measured_from_its_items_content_column(body, expected, why):
    assert cl.parse_closing_refs(body) == expected, why
    assert cl.parse_closing_refs(body, None, strict=True) == expected, why


@pytest.mark.parametrize("body,strict_expected,why", [
    # An indented code block INSIDE a blockquote is still code, and the strict
    # parse is the one that must refuse it: the `strict` arm was tested on the RAW
    # line, so a `> ` prefix hid the four spaces and the body bought a `lands`.
    ("> old version:\n>\n>     Closes #100\n", [], "indented code block inside a quote"),
    (">     Closes #100\n", [], "indented code block, quoted, single line"),
    # The flat form, restated: it already refused, and must keep refusing.
    ("old version:\n\n    Closes #100\n", [], "flat indented code block (unchanged)"),
    # …and the TAB-indented form, which is where the removed `rest.startswith("\\t")`
    # clause claimed to act. It never could: `strip_code` runs `expandtabs(4)` on
    # every line first, so a tab arrives here as FOUR SPACES and this arm already
    # fired on `rest_indent`. Pinned because the dead clause read as a live guard
    # (#10634 review P3, flagged twice); a renderer agrees it is `<pre><code>`.
    ("old version:\n\n\tCloses #100\n", [], "tab-indented code block"),
    # …and the mirror, which the same removal had to keep: a TAB after a quote
    # marker is ONE space of the blockquote's own indentation (§5.1), not four,
    # so the renderer emits `<blockquote><p>Closes #100` and GitHub fires it.
    ("> a\n>\tCloses #100\n", [100], "a tab after the quote marker is quoted prose"),
    # …and quoted PROSE stays a claim. Only CODE is ignored by GitHub.
    ("> Closes #5\n", [5], "a quoted paragraph is prose"),
    (">   Closes #5\n", [5], "a quote-indented PARAGRAPH is prose, not code"),
])
def test_a_quoted_indented_code_block_cannot_buy_a_pass(body, strict_expected, why):
    assert cl.parse_closing_refs(body, None, strict=True) == strict_expected, why


def test_the_lenient_arm_still_reports_the_quoted_indented_claim_it_cannot_refuse():
    """The documented TRADE, pinned where it is exercised: strict refuses an
    indented line because a lenient-only match there would be a PASS, while the
    finding arms stay lenient and over-report a quoted keyword (a false warning —
    the safe direction). Removing the strict arm would turn this into a `lands`."""
    body = "> old version:\n>\n>     Closes #100\n"
    assert cl.parse_closing_refs(body) == [100]
    assert cl.parse_closing_refs(body, None, strict=True) == []


def test_a_carrier_that_only_quotes_the_keyword_in_a_quoted_code_block_does_not_carry_it():
    """The `lands` direction, which is the one that must never fire: the CARRIER
    only QUOTES `Closes #N` in an indented code block inside a blockquote, so
    nothing will close the issue. The pre-fix tool reported `lands`."""
    stacked = _pr(73, SIBLING, "Closes #100")
    carrier = _pr(74, DEFAULT, "> old version:\n>\n>     Closes #100\n", head=SIBLING)
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert", finding
    assert finding.carrier == 74


@pytest.mark.parametrize("carrier_body,why", [
    ("Use `foo in the old version\n> note\n`Closes #100`\n",
     "a stray run paired with the opener of a span in a later blockquote"),
    ("Use `foo in the old version\n- note\n`Closes #100`\n",
     "…or in a later list item"),
    ("Use `foo in the old version\n# note\n`Closes #100`\n",
     "…or under a later ATX heading"),
])
def test_a_carrier_cannot_carry_through_a_span_THAT_CROSSES_A_BLOCK(carrier_body, why):
    """The `lands` arm again, through the INLINE strip rather than `strip_code`.

    A code span is inline content, so it cannot cross a BLOCK boundary — and a
    blockquote line, a list item, a heading, a thematic break or an HTML block
    all end the paragraph WITHOUT a blank line. The strict scan was bounded at
    the next BLANK line, so it paired the stray run in line 1 with the OPENER of
    the real span in line 3, blanked the wrong region, and exposed `Closes #100`
    — which the renderer puts inside `<code>`:

        Use `foo in the old version   ->  <p>Use `foo in the old version</p>
        > note                        ->  <blockquote><p>note
        `Closes #100`                 ->  <code>Closes #100</code></p></blockquote>

    Reported `lands`, exit 0, no annotation — the silent stranding #10597 exists
    to surface, on the CARRIER arm (#10634 review P2, fail-open, reproduced
    before the fix)."""
    stacked = _pr(81, SIBLING, "Closes #100")
    carrier = _pr(82, DEFAULT, carrier_body, head=SIBLING)
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert", (why, finding)
    assert finding.carrier == 82, why


@pytest.mark.parametrize("body", [
    "> a `Closes\n> #100` b\n",
    "> - item `Closes\n>   #100` here\n",
    "- item `Closes\n  #100` here\n",
])
def test_a_span_that_legitimately_crosses_lines_inside_ONE_container_is_stripped(body):
    """…and the CONTROLS for that fix: stopping at any line that so much as
    starts with `>` would refuse a span that legitimately crosses two lines of
    ONE paragraph, leaving a real quoted keyword visible — the under-report
    direction. The boundary is the CONTAINER, compared by depth."""
    assert cl.parse_closing_refs(body, None, strict=True) == []


def test_a_list_item_fence_cannot_buy_a_lands_on_the_default_base():
    pr = _pr(75, DEFAULT, "- item\n   ```\n  Closes #100\n")
    assert cl.classify(pr, _by_head(pr), DEFAULT) == []


@pytest.mark.parametrize("body,why", [
    ("> - ```\n>   Closes #100\n", "marker-line fence opened inside a blockquote"),
    ("> - ```py\n>\n>   Closes #100\n", "…after a blank quoted line (no content to dedent)"),
    ("> - ```\n>   Closes #100\n> ```\n", "…with its own quoted closer"),
])
def test_a_quoted_marker_line_fence_cannot_buy_a_lands(body, why):
    """The `lands` direction at its narrowest: a fenced block opened ON the list
    marker of an item that lives inside a blockquote. The item's content column
    is measured PAST the quote's marker, so a naive raw-indent comparison reads
    every one of the block's own content lines as a dedent, ends the block on
    the first, and reports `lands` for a keyword the renderer puts in
    `<pre><code>` — measured live at `ca92b373f`, fixed here. A pass may never
    rest on a shape the tool cannot show GitHub acts on."""
    pr = _pr(76, DEFAULT, body)
    assert cl.classify(pr, _by_head(pr), DEFAULT) == [], why


def test_a_quote_marker_inside_an_indented_code_block_cannot_buy_a_lands():
    r"""`    > Closes #100` is CODE, not a quote — BOTH ways a pass can be bought.

    §5.1 allows a blockquote marker at most THREE columns of preceding
    indentation; four make an INDENTED CODE BLOCK, so the renderer emits
    `<pre><code>&gt; Closes #100</code></pre>` and GitHub fires nothing. The
    container-prefix regex was `(?:\s*>\s?)*` — unbounded — so it absorbed the
    four columns, `rest_indent` came out `0`, the strict arm could not fire and
    the line was kept as PROSE. Both PASS arms then read the keyword
    (#10634 review P2, fail-open, reproduced before the fix):

    * the subject's own `lands` arm — a `main`-based PR exits 0 silently;
    * the CARRIER arm — a stacked PR is reported `lands` while nothing closes
      the issue, which is the silent stranding #10597 exists to surface.

    `    > ...` is the sibling of the already-pinned `>     Closes #100` (four
    columns AFTER the marker, :1596): one bound, consulted in the space the
    strict arm measures.
    """
    body = "old version:\n\n    > Closes #100\n"
    # The lenient arm still FINDS it (over-report direction, the safe one).
    assert cl.parse_closing_refs(body) == [100]
    # …and the strict arm — the PASS arm — refuses it.
    assert cl.parse_closing_refs(body, None, strict=True) == []
    pr = _pr(77, DEFAULT, body)
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved", finding
    assert "LENIENT parse" in finding.detail, finding.detail

    # the CARRIER arm: the carrier only QUOTES the keyword in indented code.
    stacked = _pr(78, SIBLING, "Closes #100")
    carrier = _pr(79, DEFAULT, body, head=SIBLING)
    (carried,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert carried.verdict == "inert", carried
    assert carried.carrier == 79


def test_a_three_space_indented_quote_marker_is_still_prose():
    """The bound is THREE, not fewer: `   > Closes #100` really is a quoted
    paragraph (the renderer emits `<blockquote><p>`), so GitHub DOES fire it and
    it must stay a pass. Bounding the allowance at 2 would turn a real closure
    into a warning — the under-report direction."""
    body = "old version:\n\n   > Closes #100\n"
    assert cl.parse_closing_refs(body, None, strict=True) == [100]
    pr = _pr(80, DEFAULT, body)
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "lands", finding


# ── the RENDERER as the oracle, so a divergence reds a test, not a reader ────
#
# `tools/closes_landing.py` is a second copy of a grammar GitHub owns, and its
# rationale for keeping the copy says a real CommonMark renderer checks it "shape
# by shape … so a divergence between what this tool calls code and what a
# renderer calls code reds a test rather than a reader". That sentence was true
# of a MANUAL pass and of nothing in this file (a review found no `markdown_it`
# import at all), which is precisely the re-staleable process claim this repo
# keeps paying for. These two directions are the module's own contract:
#
#   * the renderer calls it CODE  → the STRICT parse must not list it (a pass may
#     never rest on a shape the tool cannot show GitHub acts on);
#   * the renderer calls it PROSE → the LENIENT parse must list it (a REAL claim
#     must never be lost — an over-report costs a warning, an under-report costs
#     a silent miss).

# Both forms of CODE the renderer can produce: a BLOCK (`<pre><code>`, which
# `strip_code` owns) and an INLINE SPAN (`<code>`, which `_strip_inline_code`
# owns). Only the block form was removed here, so an inline-span shape read as
# PROSE and the oracle could not discriminate on the span grammar at all — which
# is how a span that crosses a line break bought a `lands` (#10634 review P2).
_CODE_BLOCK_RE = re.compile(r"<pre><code\b.*?</code></pre>|<code\b.*?</code>", re.S)
_FIRST_REF_RE = re.compile(r"#(\d+)")

_COMMONMARK_SHAPES = [
    # fences, plain and quoted
    ("```\nCloses #100\n```\n", "depth-0 fence"),
    ("   ```\nCloses #100\n   ```\n", "3-space opener is a fence"),
    ("    ```\nCloses #100\n", "4-space opener is an INDENTED code block"),
    ("\t```\n  Closes #100\n", "tab opener is an INDENTED code block"),
    ("> ```\n> Closes #100\n> ```\n", "quoted fence"),
    ("> ```\n> a\n>\n> Closes #100\n> ```\n", "blank quoted line stays in the quote"),
    ("```\n> ```\n```\nCloses #100\n", "a quoted fence line inside a fence is content"),
    ("```\na\n``` x\n```\nCloses #100\n", "a closer with an info string is content"),
    ("- ```\n  Closes #100\n  ```\n", "fence opened on a marker line"),
    ("- item\n  ```\n  Closes #100\n  ```\n", "fence on the line after a marker"),
    # the content-column shapes this fix is about
    ("- prose\n    ```\n  Closes #100\n", "opener 4 cols from col 0, inside the item"),
    ("- item\n   ```\n  Closes #100\n", "content below the run's own indent"),
    ("  ```\n- a\n  ```\n  ```\n- Closes #100\n", "marker line inside a fence is content"),
    ("  ```\n  a\n    ```\nCloses #100\n", "a 4-space closer at top level is CONTENT"),
    ("- item\n  ```\n  a\n    ```\nCloses #100\n",
     "a 4-space closer inside a 2-column item still closes"),
    ("    > Closes #100\n", "4-space indent before a quote marker is an INDENTED code block"),
    ("   > Closes #100\n", "3-space indent before a quote marker is still a quote"),
    # The bound is THREE, and the difference between 3 and 2 is a false PASS, not
    # a cosmetic one: at 2 the 3-column quoted FENCE below stops being a fence
    # (`` `_fence_open` `` measures from the container's content column, which the
    # unmatched `>` no longer establishes) and the keyword inside it reads as
    # prose. The `~~~` form is the one that discriminates — the ``` form is
    # accidentally protected by the inline strip pairing its own two runs — so
    # this row is what makes `{0,3}` impossible to tighten (#10634 review P3).
    ("   > ~~~\n   > Closes #100\n   > ~~~\n",
     "3-space indent before a quoted tilde fence is still a fence"),
    # indented code blocks, flat and quoted
    ("old version:\n\n    Closes #100\n", "flat indented code block"),
    ("> old version:\n>\n>     Closes #100\n", "indented code block inside a quote"),
    # a marker-line fence inside a quote, where the item's edge sits past the
    # quote marker and a blank quoted line carries no content
    ("> - ```\n>   Closes #100\n", "marker-line fence inside a quote"),
    ("> - ```py\n>\n>   Closes #100\n", "blank quoted line in a quoted item fence"),
    ("- ```\n   > Closes #100\n", "a literal `>` inside a depth-0 item fence"),
    ("- ```\n>   Closes #100\n", "a blockquote line ends a depth-0 item fence"),
    ("> - ```\n   > Closes #100\n", "indented quote marker ends a quoted item"),
    # …and a LIST MARKER inside a quote is a real item, so the fence opened under
    # it has an ITEM edge: when the item ends, the renderer ends the containing
    # block there AND reads the dedented fence line as a FRESH fence opener — so
    # the claim after it is CODE. Tracking only markers on the raw line never saw
    # `> - item`, the fence kept its block open, and the keyword read as prose:
    # `lands` on a body the renderer shows as `<pre><code>` (#10634 review P1).
    ("> - item\n>   ```\n> ```\n> Closes #100\n",
     "a quoted list item's fence ends with the item; the dedented fence re-opens"),
    ("> 1. item\n>    ```\n> ```\n> Closes #100\n",
     "…same shape with an ORDERED marker"),
    ("> - item\n>   ```\n  > ```\n>   Closes #100\n",
     "…and with a closer carrying a 2-column pre-marker indent"),
    # …and a CLOSER'S "up to three spaces" is measured after this line's own
    # markers, in the same space the containing block's column lives in. Measured
    # from a RAW column instead, the opener's `  > ` allowance (4 columns) was
    # charged against the closer's `> ` (2), so a 4-space-indented run came out 2
    # columns past the container, closed a fence the renderer keeps OPEN, and the
    # keyword after it read as prose: `lands`, exit 0, no annotation. Found by a
    # sweep against the CommonMark REFERENCE implementation (the Python `cmark`
    # port), not by a review cycle — every generated item/quote/fence shape now
    # agrees with it in both directions (#10634).
    ("  > item\n  > ```\n>     ```\n  > Closes #100\n",
     "a closer's marker allowance cannot shift its three-space budget"),
    ("  > item\n  >    ```\n>     ```\n  > Closes #100\n",
     "…same shape with the fence opened one column deeper"),
    # …and inside a QUOTE that sits inside an ITEM: the item's content column is
    # expressed in `rest` space, so a 4-column run there is NOT a fence (the
    # renderer shows <pre><code>) and the claim after it is PROSE. Passing the
    # item's RAW column into `_fence_open` read the run as a fence and blanked
    # the claim — an under-report (measured: 48 of 1008 item/quote shapes).
    ("- item\n  >     ```\n  > Closes #100\n",
     "4-column run inside a quote inside an item is an INDENTED code block"),
    # …and the OUTER container is matched FIRST: this line dedents below the
    # item's content column in RAW coordinates, so the item — and the fence
    # inside it — END, and the depth-2 quote after it is top-level prose.
    # Consuming the `>` before measuring the dedent kept the block open over it.
    ("- item\n  > ```\n> > Closes #100\n",
     "a RAW dedent ends the item and the fence it contains"),
    # a code span CROSSES A SOFT LINE BREAK: §6.1 converts line endings to
    # spaces, so opener and closer on two lines of ONE paragraph are one span.
    # A per-line closer search left these unstripped and both PASS arms read the
    # keyword — `lands` on a body whose keyword GitHub renders into <code>
    # (#10634 review P2, fail-open).
    ("Example: `Closes\n#10541` is the quoted form.\n",
     "a code span across a soft line break"),
    ("The body used ``Closes\n#10541`` in a quoted example.\n",
     "a two-backtick span across a soft line break"),
    ("- item `Closes\n  #100` here\n",
     "a span inside a list item across a soft line break"),
    # …but a span is INLINE content, so it cannot cross a BLOCK boundary — and a
    # blockquote / list item / heading / thematic break / HTML block all end the
    # paragraph WITHOUT a blank line. Bounding the strict scan at the next blank
    # line let an unmatched run pair with the OPENER of a real span in the next
    # block, blank the wrong region and expose that span's keyword: `lands` on
    # the CARRIER arm, exit 0, no annotation (#10634 review P2, fail-open).
    ("a`\n> `Closes #100`\n",
     "a stray run may not pair across a blockquote boundary"),
    ("a`\n- `Closes #100`\n",
     "…nor across a list-item boundary"),
    ("a`\n# `Closes #100`\n",
     "…nor across an ATX heading boundary"),
    ("a`\n---\n`Closes #100`\n",
     "…nor across a thematic break"),
    # …and the CONTROLS that must keep working: a span legitimately crosses a
    # soft line break INSIDE one container, so stopping at "any line starting
    # with >" would lose these real claims (the under-report direction).
    ("> a `Closes\n> #100` b\n", "a span across two lines of ONE paragraph"),
    # …and §5.1's LAZY CONTINUATION is still that one paragraph: a line carrying
    # NO `>` marker continues the quote's paragraph, so opener and closer are ONE
    # span and the renderer puts the keyword in <code>. Requiring an EQUAL depth
    # ended the span early, left both runs unmatched, and the keyword survived as
    # "prose" in BOTH arms — `lands`, exit 0, no annotation (#10634 review P1).
    ("> The old body said `Closes\n#100` and nothing fired.\n",
     "a span across a LAZY continuation line of a quote"),
    ("> > a `Closes\n> b #100` c\n",
     "…and across a lazy line one quote shallower"),
    # …and a run the LINE bound leaves literal is still closed by the next run of
    # its length in the paragraph, which is what the renderer does: this keyword is
    # PROSE, and a per-line scan that paired line 2's own runs blanked it away —
    # a lost claim in the FINDING arm (#10634 review P3).
    ("a`\n`Closes #100`\n",
     "a stray run pairs with the NEXT run of its length"),
    ("> - item `Closes\n>   #100` here\n",
     "a span across two lines of ONE quoted list item"),
    # …and the claims that ARE prose
    ("> Closes #100\n", "a quoted paragraph is prose"),
    ("- item\n  ```\nCloses #100\n", "a column-0 line ends the item"),
    ("- ```\n  a\n- Closes #100\n", "a new item ends a marker-line fence"),
]


@pytest.mark.parametrize("body,why", _COMMONMARK_SHAPES)
def test_the_parser_agrees_with_a_real_commonmark_renderer(body, why):
    html = MarkdownIt("commonmark").render(body)
    prose = _CODE_BLOCK_RE.sub("", html)
    match = _FIRST_REF_RE.search(body)
    assert match, (why, body)
    n = int(match.group(1))
    strict = cl.parse_closing_refs(body, None, strict=True)
    lenient = cl.parse_closing_refs(body)
    if f"#{n}" in prose:
        assert n in lenient, (
            f"{why}: the renderer puts #{n} in PROSE ({html!r}) and the lenient "
            f"parse — the FINDING arm — lost it. That is the under-report "
            f"direction: a real closing ref produces no warning at all.")
    else:
        assert n not in strict, (
            f"{why}: the renderer puts #{n} in <pre><code> ({html!r}) and the "
            f"strict parse — the PASS arm — reported it. A keyword GitHub ignores "
            f"cannot buy a `lands`.")


def test_the_commonmark_oracle_cannot_be_skipped_out_of_the_install():
    """The oracle above is only as real as the dependency being installed.

    It used to reach `markdown_it` through `pytest.importorskip`, so an install
    that lost the package reported `1 skipped` and left the file GREEN — while the
    mitigation `tools/closes_landing.py:76-84` leans on ("a divergence … reds a
    test rather than a reader") silently stopped existing. The import is now
    module-scope, so that state is a collection error; this pin closes the other
    half, the declarations a well-meaning tidy-up could drop.

    Both paths are pinned because both are installed in practice (#2985): CI's
    `pip install -e '.[test]'` and the canonical dev install `uv sync --extra
    embeddings --extra parity`, which does NOT include the `test` extra. That is
    the #8379 finding-3 rule this dependency was declared by in the first place.
    """
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    test_extra = pyproject["project"]["optional-dependencies"]["test"]
    dev_group = pyproject["dependency-groups"]["dev"]
    for where, declarations in (
        ("`project.optional-dependencies.test` (CI: pip install -e '.[test]')", test_extra),
        ("`dependency-groups.dev` (uv sync --extra embeddings --extra parity)", dev_group),
    ):
        assert any(d.startswith("markdown-it-py") for d in declarations), (
            f"markdown-it-py is missing from {where} — the renderer oracle in this "
            f"file is the ONLY check on the hand-rolled CommonMark copy in "
            f"tools/closes_landing.py, and with a module-scope import a dropped "
            f"declaration makes that install path lose the oracle. Declarations: "
            f"{declarations}")


def test_the_ITEM_EDGE_is_measured_in_the_space_the_item_lives_in():
    """Two shapes a single-space container refactor got wrong in ONE direction each.

    Both were found by driving bodies through a real CommonMark renderer rather
    than by reasoning about the grammar, and both are pinned by the measured
    shape (not a paraphrase of it):

    * the enclosing item's content column is expressed in `rest` space, so
      `  >     ``` ` inside a 2-column item is 4 columns — an INDENTED code
      block, not a fence — and the claim after it is PROSE;
    * a line that dedents below the item's content column in RAW coordinates
      ends the item BEFORE the fence's `>` markers are matched, so the fence and
      the item end together.

    Asserting the FINDING (lenient) arm here is the point: losing a real claim
    is the outcome the module's docstring calls the harmful one, and it is the
    one no `lands`-side test can see.
    """
    indented_in_quote_in_item = "- item\n  >     ```\n  > Closes #100\n"
    assert cl.parse_closing_refs(indented_in_quote_in_item) == [100], (
        "the 4-column run is an indented code block, so #100 is prose and the "
        "lenient arm must report it")

    raw_dedent = "- item\n  > ```\n> > Closes #100\n"
    assert cl.parse_closing_refs(raw_dedent) == [100], (
        "the raw dedent ends the item and its fence; #100 is a top-level quote "
        "and prose")

    # The direction that must NOT change: a marker-line fence has its item
    # INSIDE the quote, so the same line stays CONTENT and may not be reported.
    marker_line_fence_in_quote = "> - ```\n>   Closes #100\n"
    assert cl.parse_closing_refs(marker_line_fence_in_quote, None, strict=True) == []
    assert cl.parse_closing_refs(marker_line_fence_in_quote) == []


# ── MERGED-BUT-OPEN (#10698): from the merge to the issue's own state ────────
#
# The question these pin is NOT "will the keyword fire". It is: the merge already
# happened — did anything close the issue, and what is the evidence allowed to say?
#
# Measured on this repository on 2026-11-03 (43 open P0-blocker/P1-severe issues,
# 200 merged PRs): 0 had a closure record, 0 had even a firing `Closes` on the
# default branch — every "closure" a naive parse found was a QUOTED keyword
# (`PR #10519` says `` `Closes #10541` `` in a code span) or a keyword stranded on a
# sibling base (`PR #10568`, base `act-10465`). So the two directions that matter
# here are: a `done` may never rest on a keyword at all, and a reference inside a
# code span is not a reference.

def _mpr(n, base=DEFAULT, body="", sha=None, merged="2026-11-03T00:00:00Z",
         closes=()):
    return cl.MergedPR(number=n, base=base, body=body,
                       merge_sha=sha if sha is not None else f"sha{n:040d}",
                       merged_at=merged, closing_issues=tuple(closes))


def _issue(n, body="", state="OPEN"):
    return cl.IssueRecord(number=n, state=state, body=body)


def test_a_merge_with_GITHUBS_OWN_closure_record_is_done():
    link = cl.judge_issue(_issue(7), [_mpr(10, closes=(7,))],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=cl.Timeline())
    assert link.verdict == "done"
    assert link.reason == cl.REASON_CLOSURE
    assert link.pr == 10
    assert link.sha == f"sha{10:040d}"


def test_a_closing_keyword_that_DID_NOT_FIRE_cannot_buy_done():
    """The whole point: `lands` is a claim about the keyword, not about the issue.

    A body that says `Closes #7` and merged into `main` while `#7` is still open
    is GitHub having ignored the shape (keyword edited in after the merge) or a
    repo with automatic closing off — NOT evidence of completion. It must never
    report `done`.
    """
    link = cl.judge_issue(_issue(7), [_mpr(10, body="Closes #7")],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=cl.Timeline())
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_CLOSES_DID_NOT_FIRE


def test_a_claim_stranded_on_a_NON_default_base_is_named_as_that():
    """#10597's measured shape, reported at this layer: PR #10568 carried
    `Closes #10541` into base `act-10465`, so the keyword never fired and the
    work only rides to main through a carrier that names a different issue."""
    link = cl.judge_issue(_issue(10541),
                          [_mpr(10568, base=SIBLING, body="Closes #10541")],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=cl.Timeline())
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_CLOSES_NON_DEFAULT


def test_a_refs_only_merge_is_partial_and_NEVER_done():
    link = cl.judge_issue(_issue(7), [_mpr(10, body="Refs #7")],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=cl.Timeline())
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_REFS_ONLY


def test_a_QUOTED_closing_keyword_is_not_even_a_reference():
    """The measured false positive that motivated reusing this module's parser.

    `PR #10519`'s prose contains `` `Closes #10541` `` inside a backtick span. A
    naive `Closes|Fixes|Resolves` regex reads that as a claim; the renderer calls
    it code. Here it must produce NOTHING — not `done`, not a `partial` reason.
    """
    body = "rows 1 and 2 re-scored`Closes #10541` is claimed under, and the fork"
    link = cl.judge_issue(_issue(10541), [_mpr(10519, body=body)],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=cl.Timeline())
    assert link.verdict == "not-done"
    assert cl.parse_closing_refs(body, "owner/name", strict=True) == []


def test_a_quoted_REFS_is_not_a_reference_either():
    body = "the `Refs #7` this body used to carry was inherited"
    assert cl.parse_reference_refs(body) == []


def test_a_reference_only_in_the_issue_TIMELINE_is_a_mention():
    link = cl.judge_issue(_issue(7), [_mpr(10)], default_branch=DEFAULT,
                          repo="owner/name",
                          timeline=cl.Timeline(refs=(_mpr(11, body="nothing in the body"),)))
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_MENTION_ONLY
    assert link.pr == 11


def test_no_reference_anywhere_is_not_done_and_says_WHICH_window():
    link = cl.judge_issue(_issue(7), [_mpr(10)], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert link.verdict == "not-done"
    # The reason carries the window qualifier: `not-done` is a claim about the
    # merges that were READ, and a bare "no-merge-ref" reads as "never merged".
    assert link.reason == cl.REASON_NO_MERGE_REF == "no-merge-ref-in-window"


def test_done_outranks_a_refs_only_merge_on_an_older_PR():
    link = cl.judge_issue(
        _issue(7),
        [_mpr(10, body="Refs #7", merged="2026-11-01T00:00:00Z"),
         _mpr(12, closes=(7,), merged="2026-11-02T00:00:00Z")],
        default_branch=DEFAULT, repo="owner/name", timeline=cl.Timeline())
    assert (link.verdict, link.pr) == ("done", 12)


def test_the_NEWEST_merge_is_the_sha_cited():
    link = cl.judge_issue(
        _issue(7),
        [_mpr(10, body="Refs #7", merged="2026-11-01T00:00:00Z"),
         _mpr(11, body="Refs #7", merged="2026-11-02T00:00:00Z")],
        default_branch=DEFAULT, repo="owner/name", timeline=cl.Timeline())
    assert (link.verdict, link.pr, link.sha) == ("partial", 11, f"sha{11:040d}")


def test_an_UNREAD_timeline_never_produces_not_done():
    """`not-done` is a claim about an ABSENCE. With no timeline read there is no
    absence to claim, so the honest answer is the WEAKER `partial`."""
    prs = [_mpr(10)]
    for timelines in (None, {}):
        links = cl.judge_population([_issue(7)], prs, default_branch=DEFAULT,
                                    repo="owner/name", timelines=timelines)
        assert links[0].verdict == "partial", timelines
        assert links[0].reason == cl.REASON_UNMEASURED_TIMELINE


def test_a_READ_timeline_with_no_reference_does_produce_not_done():
    links = cl.judge_population([_issue(7)], [_mpr(10)], default_branch=DEFAULT,
                                repo="owner/name", timelines={7: cl.Timeline()})
    assert links[0].verdict == "not-done"


# ── the acceptance annotation (never a gate) ─────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ("- [x] a\n- [ ] b\n- [X] c", (3, 2)),
    ("* [x] a\n+ [ ] b\n1. [x] c", (3, 2)),      # every task-list marker GitHub renders
    ("- [x] a\n", (1, 1)),
    ("no list here", (0, 0)),
    ("- a plain bullet\n- another", (0, 0)),
])
def test_acceptance_counts_every_task_list_marker(body, expected):
    got = cl.acceptance_from_body(body)
    assert (got.total, got.checked) == expected


def test_a_QUOTED_checklist_is_not_acceptance():
    body = "this is the template:\n\n```\n- [ ] do the thing\n```\n"
    assert cl.acceptance_from_body(body) == cl.Acceptance(0, 0)


def test_acceptance_all_checked_requires_AT_LEAST_ONE_box():
    """An empty list is not "all checked": `total > 0` is what stops an empty
    denominator from reading as complete."""
    assert cl.Acceptance(0, 0).all_checked is False
    assert cl.Acceptance(2, 2).all_checked is True
    assert cl.Acceptance(2, 1).all_checked is False
    assert cl.Acceptance(0, 0).label() == "none"
    assert cl.Acceptance(2, 1).label() == "1/2"


# ── the report line, and the exit map ────────────────────────────────────────

def test_the_merged_line_is_the_documented_format():
    line = cl.MergeLink(issue=5, verdict="partial", reason="refs-only",
                        pr=10, sha="abc123").line()
    assert line == ("MERGED-BUT-OPEN(abc123 10 -> #5 verdict=partial "
                    "reason=refs-only acceptance=none)")
    assert "\n" not in line


def test_a_not_done_line_never_claims_a_merge():
    """A `not-done` issue has no merge, so printing a sha and a PR number would
    assert one that never happened — and a `grep MERGED-BUT-OPEN` would then
    collect rows nothing backs."""
    line = cl.MergeLink(issue=5, verdict="not-done",
                        reason="no-merge-ref-in-window").line()
    assert line.startswith("NO-MERGE-REF(#5 ")
    assert "MERGED-BUT-OPEN" not in line
    assert "verdict=not-done" in line


def test_merge_link_worst_is_its_OWN_vocabulary():
    """`worst_verdict` iterates the STACK vocabulary and returns `none` for every
    `partial`/`not-done` link — reusing it would exit 0 on a report that is
    entirely drift. This pins the separation in both directions."""
    partial = [cl.MergeLink(issue=1, verdict="partial", reason="refs-only")]
    not_done = [cl.MergeLink(issue=2, verdict="not-done", reason="no-merge-ref-in-window")]
    done = [cl.MergeLink(issue=3, verdict="done", reason="closure-record")]
    assert cl.merge_link_worst(partial) == "partial"
    assert cl.merge_link_worst(not_done) == "not-done"
    assert cl.merge_link_worst(done) == "done"
    assert cl.merge_link_worst([]) == "none"
    assert cl.merge_link_worst(partial + done) == "partial"      # worst wins
    assert cl.merge_link_worst(done + not_done) == "not-done"
    assert cl.worst_verdict([]) == "none"


# ── the live layer, stubbed at `_gh` (the same seam the suite already uses) ──

def _merged_gh(default=DEFAULT, opening=(), merged_rows=(), timeline=()):
    """A `_gh` stub answering the three calls this mode makes."""
    import json as _json

    by_number = {row["number"]: row for row in opening}

    def fake(args):
        if args[:2] == ["pr", "list"]:
            return _json.dumps(list(merged_rows))
        if args[:2] == ["issue", "list"]:
            return _json.dumps(list(opening))
        if args[:2] == ["issue", "view"]:
            return _json.dumps(by_number[int(args[2])])
        if args[:1] == ["api"]:
            return _json.dumps([list(timeline)])
        if args[:2] == ["repo", "view"]:
            return _json.dumps({"defaultBranchRef": {"name": default},
                                "nameWithOwner": "owner/name"})
        raise AssertionError(f"unexpected gh call: {args}")

    return fake


def test_cli_merged_but_open_exits_3_on_drift(monkeypatch, capsys):
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[{"number": 7, "state": "OPEN", "body": ""}],
        merged_rows=[{"number": 10, "baseRefName": DEFAULT, "body": "Refs #7",
                      "mergedAt": "2026-11-02T00:00:00Z",
                      "mergeCommit": {"oid": "a" * 40},
                      "closingIssuesReferences": []}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT])
    out = capsys.readouterr().out
    assert code == 3
    assert "MERGED-BUT-OPEN(" in out
    assert "reason=refs-only" in out
    assert "population=1" in out            # the denominator is always printed


def test_cli_merged_but_open_exits_0_when_everything_has_a_closure_record(
        monkeypatch, capsys):
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[{"number": 7, "state": "OPEN", "body": ""}],
        merged_rows=[{"number": 10, "baseRefName": DEFAULT, "body": "Closes #7",
                      "mergedAt": "2026-11-02T00:00:00Z",
                      "mergeCommit": {"oid": "a" * 40},
                      "closingIssuesReferences": [{"number": 7, "repository": {"id": "R", "name": "name", "owner": {"id": "U", "login": "owner"}}, "url": "https://github.com/owner/name/issues/7"}]}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT])
    out = capsys.readouterr().out
    assert code == 0
    # The WHOLE census prints, `done` rows included: the line format carries
    # `verdict=done`, so a reader must be able to grep it.
    assert "verdict=done" in out
    assert "population=1 done=1" in out


def test_cli_merged_but_open_exits_2_when_gh_cannot_answer(monkeypatch, capsys):
    def boom(_args):
        raise cl.Unmeasurable("gh is down")
    monkeypatch.setattr(cl, "_gh", boom)
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT])
    assert code == 2
    assert "UNMEASURED" in capsys.readouterr().err


def test_a_FOREIGN_repos_closure_record_cannot_mint_done_here(monkeypatch):
    """`#7` in another repository is not `#7` here, and this arm decides whether
    an issue may be CLOSED. GitHub's `closingIssuesReferences` carries the owning
    repository; comparing only the number let `Closes other/repo#7` mark local
    open #7 as `done`."""
    rows = [{"number": 10, "baseRefName": DEFAULT, "body": "",
             "mergedAt": "2026-11-02T00:00:00Z",
             "mergeCommit": {"oid": "a" * 40},
             # ⛔ the REAL shape: `gh pr view --json closingIssuesReferences`
             # emits `repository:{id,name,owner:{login}}` + a github.com `url`.
             # It carries NO `nameWithOwner` and NO `repository_url` — a first
             # version of the guard read only those two, so it was INERT in
             # production while this test (built from a shape production never
             # produces) passed.
             "closingIssuesReferences": [{
                 "number": 7,
                 "repository": {"id": "R_x", "name": "other",
                                "owner": {"id": "U_x", "login": "someone-else"}},
                 "url": "https://github.com/someone-else/other/issues/7"}]},
            {"number": 11, "baseRefName": DEFAULT, "body": "",
             "mergedAt": "2026-11-02T00:00:00Z",
             "mergeCommit": {"oid": "b" * 40},
             "closingIssuesReferences": [{
                 "number": 8,
                 "repository": {"id": "R_y", "name": "name",
                                "owner": {"id": "U_y", "login": "owner"}},
                 "url": "https://github.com/owner/name/issues/8"}]}]
    monkeypatch.setattr(cl, "_gh",
                        _merged_gh(opening=[{"number": 7, "state": "OPEN", "body": ""},
                                            {"number": 8, "state": "OPEN", "body": ""}],
                                   merged_rows=rows))
    prs = cl.fetch_merged_prs("owner/name", 48)
    assert prs[0].closing_issues == ()          # the foreign ref is dropped
    assert prs[1].closing_issues == (8,)        # the local one survives
    links = {lk.issue: lk for lk in cl.merged_but_open(
        "owner/name", DEFAULT, labels=["P1-severe"], since_hours=48, prs=prs,
        timelines=False)}
    assert links[7].verdict != "done"


@pytest.mark.parametrize("node,repo,expected", [
    # the exact payload `gh pr view 8174 --json closingIssuesReferences` emitted
    ({"number": 7, "repository": {"id": "R", "name": "tortoise",
                                  "owner": {"id": "U", "login": "daniel-ospina"}},
      "url": "https://github.com/daniel-ospina/tortoise/issues/7"},
     "daniel-ospina/tortoise", True),
    ({"number": 7, "repository": {"id": "R", "name": "other",
                                  "owner": {"id": "U", "login": "someone-else"}},
      "url": "https://github.com/someone-else/other/issues/7"},
     "daniel-ospina/tortoise", False),
    # a foreign repo whose NAME is a suffix of ours must not pass on the name
    ({"number": 7, "repository": {"name": "tortoise", "owner": {"login": "x"}}},
     "daniel-ospina/tortoise", False),
    ({"number": 7, "repository": {"nameWithOwner": "owner/name"}},
     "owner/name", True),
    ({"number": 7, "repository_url": "https://api.github.com/repos/owner/name"},
     "owner/name", True),
    # NO identification at all: fail CLOSED, because this arm decides `done`
    ({"number": 7}, "owner/name", False),
    # …unless there is no repo to compare against, where local is the default
    ({"number": 7}, None, True),
    ("not a dict", "owner/name", False),
])
def test_a_reference_is_scoped_by_REPOSITORY_not_only_by_number(
        node, repo, expected):
    """`#7` in another repository is not `#7` here, and this arm decides whether
    an issue may be CLOSED.

    The parametrisation is deliberately built from the payload shapes MEASURED
    against the live API — an earlier version of this guard read only
    `repository.nameWithOwner` / `repository_url`, neither of which `gh` emits for
    `closingIssuesReferences`, so it was inert in production while a test built
    from an invented shape passed.
    """
    assert cl.ref_is_local(node, repo) is expected


def test_a_trailing_foreign_closing_ref_cannot_mint_done_end_to_end():
    foreign = {"number": 7,
               "repository": {"id": "R", "name": "other",
                              "owner": {"id": "U", "login": "someone-else"}},
               "url": "https://github.com/someone-else/other/issues/7"}
    pr = cl._to_merged_pr({"number": 9001, "baseRefName": DEFAULT, "body": "",
                           "mergedAt": "2026-11-02T00:00:00Z",
                           "mergeCommit": {"oid": "c" * 40},
                           "closingIssuesReferences": [foreign]},
                          "owner/name")
    assert pr.closing_issues == ()
    link = cl.judge_issue(_issue(7), [pr], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert link.verdict != "done"


def test_step_two_reads_Closes_STRICTLY_so_a_broken_claim_is_not_a_claim():
    """`Closes` on its own line with the number after a blank line does NOT parse
    strictly. Step 2 must use the strict grammar: reporting `closes-did-not-fire`
    for something that is not a claim is a false warning, and it is the arm the
    lenient grammar would silently upgrade. Mutation-tested: flipping
    `strict=True` to the lenient default left every other test passing."""
    body = "Closes\n\n#7"
    assert cl.parse_closing_refs(body, "owner/name", strict=True) == []
    assert cl.parse_closing_refs(body, "owner/name") == [7]
    pr = cl.MergedPR(number=10, base=DEFAULT, body=body, merge_sha="a" * 40,
                     merged_at="2026-11-02T00:00:00Z")
    link = cl.judge_issue(_issue(7), [pr], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert link.reason != "closes-did-not-fire"


@pytest.mark.parametrize("call", [
    lambda: cl.fetch_merged_prs("owner/name", 24),
    lambda: cl.fetch_open_issues(["P1-severe"], "owner/name"),
    lambda: cl.fetch_issues_by_number([7], "owner/name"),
])
@pytest.mark.parametrize("payload", ['{"message": "Bad credentials"}', "null"])
def test_a_200_that_is_not_a_json_ARRAY_is_UNMEASURABLE_not_a_traceback(
        monkeypatch, call, payload):
    """`_gh` raises on a non-zero exit, but a 200 carrying an error object is the
    same failure and must land on the documented exit 2. It used to escape
    `main()` as an `AttributeError`/`TypeError` traceback — exit 1, which the
    header does not define."""
    monkeypatch.setattr(cl, "_gh", lambda _args: payload)
    with pytest.raises(cl.Unmeasurable):
        call()


def test_a_FOREIGN_repos_timeline_cross_reference_is_not_attributed(monkeypatch):
    """The timeline arm has the same hole: `cross-referenced` carries
    `source.issue.repository_url`, and matching only `number` credits OUR PR #10
    with a mention that came from another repository."""
    timeline = [
        {"event": "cross-referenced",
         "source": {"issue": {"number": 10,
                              "repository_url": "https://api.github.com/repos/x/y"}}},
        {"event": "cross-referenced",
         "source": {"issue": {"number": 11,
                              "repository_url": "https://api.github.com/repos/owner/name"}}},
    ]
    monkeypatch.setattr(cl, "_gh", lambda _a: __import__("json").dumps([timeline]))
    tl = cl.fetch_timeline(7, "owner/name", {10: _mpr(10), 11: _mpr(11)})
    assert [r.number for r in tl.refs] == [11]


def test_the_no_timeline_FLAG_says_partial_not_not_done(monkeypatch, capsys):
    """`--no-timeline` is the only way a caller can produce an unmeasured census,
    so it must print `partial`/`unmeasured-timeline` -- NOT `not-done`, which is a
    claim about an absence nobody measured.

    Two rows on purpose: `#7` is settled by its BODY (it stays `refs-only`
    whatever the flag says), and `#8` is settled by nothing, so it is the row the
    flag actually moves. With one row only, mutating
    `timelines=not args.no_timeline` to `timelines=True` left every test passing.
    """
    def fake(args):
        if args[:2] == ["pr", "list"]:
            return "[]"
        if args[:1] == ["api"]:            # a READ timeline that found nothing
            return "[[]]"
        if args[:2] == ["repo", "view"]:
            return '{"defaultBranchRef": {"name": "main"}, "nameWithOwner": "owner/name"}'
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(cl, "_gh", fake)
    monkeypatch.setattr(cl, "fetch_open_issues",
                        lambda _l, _r: [_issue(7), _issue(8)])
    monkeypatch.setattr(cl, "fetch_merged_prs", lambda _r, _h: [
        cl.MergedPR(number=10, base=DEFAULT, body="Refs #7",
                    merge_sha="a" * 40, merged_at="2026-11-02T00:00:00Z")])

    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT, "--label", "P1-severe",
                    "--no-timeline"])
    out = capsys.readouterr().out
    assert code == 3                        # drift is still drift
    assert "reason=refs-only" in out        # the body settled #7
    assert "reason=unmeasured-timeline" in out
    assert "verdict=not-done" not in out    # nothing was measured as absent

    # ...and WITHOUT the flag the same row IS measured, so the flag is load-bearing
    capsys.readouterr()
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT, "--label", "P1-severe"])
    assert code == 3
    assert "NO-MERGE-REF(#8 verdict=not-done reason=no-merge-ref-in-window" \
        in capsys.readouterr().out


def test_the_open_issue_population_FULL_is_UNMEASURABLE(monkeypatch):
    """A FULL `issue list` page is a TRUNCATED population, and a truncated
    population cannot report `not-done` over the rows it never read. Deleting
    this guard left every test passing."""
    import json as _json

    def fake(args):
        if args[:2] == ["issue", "list"]:
            return _json.dumps([{"number": n, "state": "OPEN", "body": ""}
                                for n in range(5)])
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl.fetch_open_issues(["P1-severe"], "owner/name", limit=5)
    assert "FULL" in str(exc.value)


def test_an_EMPTY_merged_window_is_UNMEASURABLE(monkeypatch):
    """No merged PRs in the window is a question the tool did not answer, not a
    clean census. Deleting this guard left every test passing."""
    def fake(_args):
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl.merged_but_open("owner/name", DEFAULT, labels=["P1-severe"],
                           since_hours=48)
    assert "empty window is UNMEASURED" in str(exc.value)


def test_the_merged_window_is_pinned_to_MERGE_TIME_not_created_order(monkeypatch):
    """`gh pr list --state merged` orders by `createdAt`. A mutant that drops the
    `merged:` qualifier silently samples created-order, so a PR opened a week ago
    and merged an hour ago falls out of a 48 h window — the argv has to be pinned
    or the mutation survives."""
    calls = []

    def fake(args):
        calls.append(list(args))
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake)
    assert cl.fetch_merged_prs("owner/name", 48) == []
    argv = calls[0]
    assert "--search" in argv
    assert argv[argv.index("--search") + 1].startswith("merged:2")
    assert ".." in argv[argv.index("--search") + 1]      # a bounded RANGE, not `>=`
    assert "--state" in argv and argv[argv.index("--state") + 1] == "merged"


def test_a_FULL_merged_window_is_unmeasurable_not_a_shorter_report(monkeypatch):
    """Reaching `--limit` cannot tell "nothing references this issue" from "the
    reference is in the part I did not read"."""
    def fake(_args):
        import json as _json
        return _json.dumps([{"number": n, "baseRefName": DEFAULT, "body": "",
                             "mergedAt": "2026-11-02T00:00:00Z",
                             "mergeCommit": {"oid": "a" * 40},
                             "closingIssuesReferences": []}
                            for n in range(5)])

    monkeypatch.setattr(cl, "_gh", fake)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl.fetch_merged_prs("owner/name", 48, limit=5)
    assert "FULL" in str(exc.value)


def test_the_window_is_PAGED_by_day_so_a_week_never_becomes_one_payload(monkeypatch):
    """`--since-hours 168` used to be a single 157-row body query that blew the
    60 s per-call bound (measured twice). It is paged now, and the pages must be
    bounded ranges or the fix is cosmetic."""
    calls = []

    def fake(args):
        calls.append(list(args))
        return "[]"

    monkeypatch.setattr(cl, "_gh", fake)
    cl.fetch_merged_prs("owner/name", 168)
    assert len(calls) == 7, f"expected 7 day-sized pages, got {len(calls)}"
    for argv in calls:
        window = argv[argv.index("--search") + 1].split(" sort=")[0]
        lo, _, hi = window.partition("merged:")[2].partition("..")
        assert lo and hi and lo < hi, window


def test_a_PR_appearing_in_two_overlapping_pages_is_counted_ONCE(monkeypatch):
    """Datetime ranges do not overlap, but the dedupe is what makes that a
    property of the CODE rather than of the query string."""
    def fake(_args):
        import json as _json
        return _json.dumps([{"number": 10, "baseRefName": DEFAULT,
                             "body": "Closes #7", "state": "MERGED",
                             "mergedAt": "2026-11-02T00:00:00Z",
                             "mergeCommit": {"oid": "a" * 40},
                             "closingIssuesReferences": [{"number": 7, "repository": {"id": "R", "name": "name", "owner": {"id": "U", "login": "owner"}}, "url": "https://github.com/owner/name/issues/7"}]}])

    monkeypatch.setattr(cl, "_gh", fake)
    prs = cl.fetch_merged_prs("owner/name", 48)
    assert [p.number for p in prs] == [10]


def test_the_timeline_read_needs_a_slug():
    """No slug ⇒ no timeline ⇒ no `not-done`. Fail closed, never guess."""
    with pytest.raises(cl.Unmeasurable):
        cl.fetch_timeline(7, "", {})


def test_the_timeline_reads_EVERY_page(monkeypatch):
    """One measured issue carried 63 `referenced` + 27 `cross-referenced` events;
    a first-page-only read would answer `not-done` over references it never saw."""
    calls = []

    def fake(args):
        import json as _json
        calls.append(list(args))
        return _json.dumps([[{"event": "cross-referenced",
                              "source": {"issue": {
                                  "number": 10,
                                  "repository_url":
                                      "https://api.github.com/repos/owner/name"}}}]])

    monkeypatch.setattr(cl, "_gh", fake)
    tl = cl.fetch_timeline(7, "owner/name", {10: _mpr(10)})
    assert [r.number for r in tl.refs] == [10]
    assert "--paginate" in calls[0] and "--slurp" in calls[0]


@pytest.mark.parametrize("payload", [
    '{"message": "Bad credentials", "status": "401"}',   # an error OBJECT
    "null",                                              # no body at all
    '[{"event": "reopened"}]',                           # events, but no PAGES
    '[[{"event": "reopened"}], {"event": "reopened"}]',  # one page is not a list
])
def test_a_timeline_payload_this_code_cannot_parse_FAILS_CLOSED(
        monkeypatch, payload):
    """An empty `Timeline` means "read it, found nothing" — and that is the only
    state `not-done` is allowed to rest on. So a body the parser does not
    understand must raise, exactly as the module's own doctrine says
    ("An unreadable timeline is UNMEASURABLE, never 'no references'").

    It previously fell through to `events = []`, which made a 200 carrying an
    error object indistinguishable from a complete read, and produced
    `verdict=not-done reason=no-merge-ref-in-window` from a document never read.
    """
    monkeypatch.setattr(cl, "_gh", lambda _args: payload)
    with pytest.raises(cl.Unmeasurable) as exc:
        cl.fetch_timeline(7, "owner/name", {})
    assert "unreadable timeline is UNMEASURED" in str(exc.value)


def test_the_two_documented_line_kinds_are_the_two_lines_PRINTED():
    """The module header calls these "the fleet-readable contract", and a
    consumer implements against THAT text. The `NO-MERGE-REF` shape drifted from
    it once — documented `reason=no-merge-ref`, emitted
    `reason=no-merge-ref-in-window acceptance=none` — and only one of the two
    kinds was pinned, so nothing caught it."""
    merged = cl.MergeLink(issue=7, verdict="partial", reason="refs-only",
                          pr=10, sha="a" * 40,
                          acceptance=cl.Acceptance(2, 1))
    bare = cl.MergeLink(issue=7, verdict="not-done",
                        reason=cl.REASON_NO_MERGE_REF)
    assert merged.line() == (
        f"MERGED-BUT-OPEN({'a' * 40} 10 -> #7 verdict=partial reason=refs-only "
        f"acceptance=1/2)")
    assert bare.line() == (
        "NO-MERGE-REF(#7 verdict=not-done reason=no-merge-ref-in-window "
        "acceptance=none)")

    header = (Path(cl.__file__).read_text())
    for line in (merged.line(), bare.line()):
        kind = line.split("(")[0]
        shape = [ln.strip() for ln in header.splitlines()
                 if ln.strip().startswith(f"{kind}(")]
        assert shape, f"{kind} is not documented in the module header"
        documented = shape[0]
        # every field the code prints must be in the documented contract — and
        # where the contract states a LITERAL (not a `<placeholder>`), the code
        # must print that literal. A name-only check would have passed
        # `reason=no-merge-ref` against a printed `no-merge-ref-in-window`.
        for field in line[line.index("(") + 1:-1].split():
            if "=" not in field:
                continue
            key, _, value = field.partition("=")
            pieces = [p.partition("=")[2] for p in documented.split()
                      if p.startswith(key + "=")]
            assert pieces, f"{kind}: documented shape omits `{key}=`"
            for stated in pieces:
                if "<" not in stated:
                    assert stated == value, (
                        f"{kind}: contract says `{key}={stated}`, code prints "
                        f"`{key}={value}`")


def test_a_reference_in_the_timeline_from_a_NON_default_base_PR_is_not_counted(
        monkeypatch):
    def fake(_args):
        import json as _json
        return _json.dumps([[{"event": "cross-referenced",
                              "source": {"issue": {
                                  "number": 10,
                                  "repository_url":
                                      "https://api.github.com/repos/owner/name"}}}]])

    monkeypatch.setattr(cl, "_gh", fake)
    sibling = _mpr(10, base=SIBLING)
    link = cl.judge_issue(_issue(7), [sibling], default_branch=DEFAULT,
                          repo="owner/name",
                          timeline=cl.fetch_timeline(7, "owner/name",
                                                     {10: sibling}))
    assert link.verdict == "not-done"



def test_an_EMPTY_census_is_unmeasurable_not_a_clean_sweep(monkeypatch, capsys):
    """`--label P9-nonsense` must not exit 0: an empty population is UNMEASURED,
    and a 0 exit over nothing at all is the false PASS this module refuses."""
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[],
        merged_rows=[{"number": 10, "baseRefName": DEFAULT, "body": "Refs #7",
                      "mergedAt": "2026-11-02T00:00:00Z",
                      "mergeCommit": {"oid": "a" * 40},
                      "closingIssuesReferences": []}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT, "--label", "P9-nonsense"])
    assert code == 2
    assert "EMPTY" in capsys.readouterr().err


def test_the_annotation_arm_stays_drift_only(monkeypatch, capsys):
    """A `done` row is not a warning. If it were annotated, a repo that is fine
    would emit one ::warning per healthy issue."""
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[{"number": 7, "state": "OPEN", "body": ""}],
        merged_rows=[{"number": 10, "baseRefName": DEFAULT, "body": "Closes #7",
                      "mergedAt": "2026-11-02T00:00:00Z",
                      "mergeCommit": {"oid": "a" * 40},
                      "closingIssuesReferences": [{"number": 7, "repository": {"id": "R", "name": "name", "owner": {"id": "U", "login": "owner"}}, "url": "https://github.com/owner/name/issues/7"}]}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT, "--annotate"])
    out = capsys.readouterr().out
    assert code == 0
    assert "::warning" not in out
    assert out.strip() == ""      # nothing to warn about, and no prose either


def test_an_UNMERGED_pr_cannot_have_failed_to_close_anything():
    """A keyword on an OPEN PR has not failed to fire — it has not fired YET, and
    `closes-did-not-fire` would tell a reader a merge happened and lost the
    closure. The pure function must refuse the claim for an unmerged PR."""
    open_pr = cl.MergedPR(number=10, base=DEFAULT, body="Closes #7",
                          merge_sha="", merged_at=None)
    assert open_pr.merged is False
    link = cl.judge_issue(_issue(7), [open_pr], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert link.verdict == "not-done"
    # ...and an unmerged PR on a sibling base is #10597's LIVE case, not a claim
    # about a merge that already happened.
    live_sibling = cl.MergedPR(number=11, base=SIBLING, body="Closes #7",
                               merge_sha="", merged_at=None)
    link = cl.judge_issue(_issue(7), [live_sibling], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert link.verdict == "not-done"


def test_a_merged_sibling_branch_PR_IS_a_named_claim():
    merged_sibling = cl.MergedPR(number=10568, base=SIBLING,
                                 body="Closes #7", merge_sha="b" * 40,
                                 merged_at="2026-11-02T00:00:00Z")
    link = cl.judge_issue(_issue(7), [merged_sibling], default_branch=DEFAULT,
                          repo="owner/name", timeline=cl.Timeline())
    assert (link.verdict, link.reason) == ("partial", cl.REASON_CLOSES_NON_DEFAULT)


def test_a_COMMIT_MENTION_in_the_timeline_is_attributed_by_merge_sha(monkeypatch):
    """The documented squash/merge-commit path: a `referenced` event carries only
    `commit_id`, so it is attributed through the PR's `merge_commit_sha`. Without
    this the commit-message keyword path is fetched and silently dropped."""
    def fake(_args):
        import json as _json
        return _json.dumps([[{"event": "referenced", "commit_id": "c" * 40,
                              "source": None}]])

    monkeypatch.setattr(cl, "_gh", fake)
    tl = cl.fetch_timeline(7, "owner/name",
                           {10: _mpr(10, sha="c" * 40,
                                     merged="2026-11-02T00:00:00Z")})
    assert [r.number for r in tl.refs] == [10]


def test_a_commit_mention_that_matches_NO_merge_sha_is_not_attributed(monkeypatch):
    """A branch commit (not the merge commit) cannot be mapped to a PR without a
    lookup per event. Missing it costs a `mention-only`; it must never manufacture
    a claim about some other PR."""
    def fake(_args):
        import json as _json
        return _json.dumps([[{"event": "referenced", "commit_id": "d" * 40,
                              "source": None}]])

    monkeypatch.setattr(cl, "_gh", fake)
    tl = cl.fetch_timeline(7, "owner/name", {10: _mpr(10, sha="c" * 40)})
    assert tl.refs == ()


# ── the reopen guard: the ONE way a closure record must not become `done` ────
#
# Measured on this repository 2026-11-03 over 1000 merged PRs (back to
# 2026-10-12): exactly TWO of the 43 open P0/P1 issues appear in a merged PR's
# `closingIssuesReferences` — `#9548 <- PR #9585` and `#10000 <- PR #10011`.
# Both are OPEN. The keyword fired, the issue closed, a human reopened it.
#
# So the naive rule ("closure record ⇒ done") has two live false positives in the
# population this lane reports on, and closing them would override a human's
# judgement — the single most expensive thing this tool could do.

def test_a_closure_record_over_a_REOPENED_issue_is_not_done():
    reopened = cl.Timeline(last_reopened_at="2026-11-02T12:00:00Z")
    link = cl.judge_issue(_issue(9548), [_mpr(9585, closes=(9548,),
                                              merged="2026-11-01T00:00:00Z")],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=reopened)
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_REOPENED
    # ...and the row still cites the merge that fired, so a reader can go look.
    assert (link.pr, link.sha) == (9585, f"sha{9585:040d}")


def test_a_reopen_BEFORE_the_merge_does_not_block_done():
    """An issue reopened LAST MONTH and closed correctly by this merge is done."""
    old_reopen = cl.Timeline(last_reopened_at="2026-10-01T00:00:00Z")
    link = cl.judge_issue(_issue(7), [_mpr(10, closes=(7,),
                                           merged="2026-11-01T00:00:00Z")],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=old_reopen)
    assert link.verdict == "done"


def test_a_closure_record_with_NO_timeline_read_withholds_done():
    """A reopen can only be ruled out from a timeline that was read. This is the
    guard that makes the two measured reopens impossible to mistake for `done`
    even when the caller skips the timeline (`--no-timeline`)."""
    link = cl.judge_issue(_issue(7), [_mpr(10, closes=(7,))],
                          default_branch=DEFAULT, repo="owner/name",
                          timeline=None)
    assert link.verdict == "partial"
    assert link.reason == cl.REASON_UNMEASURED_TIMELINE


def test_a_reopen_with_no_comparable_merge_stamp_counts_as_AFTER():
    """Conservative by design: an unorderable pair must not buy a `done`."""
    tl = cl.Timeline(last_reopened_at="2026-11-02T12:00:00Z")
    assert tl.reopened_after(None) is True
    assert tl.reopened_after("2026-11-01T00:00:00Z") is True
    assert tl.reopened_after("2026-11-03T00:00:00Z") is False
    assert cl.Timeline().reopened_after("2026-11-01T00:00:00Z") is False


def test_the_timeline_read_records_the_LAST_reopen(monkeypatch):
    def fake(_args):
        import json as _json
        return _json.dumps([[
            {"event": "reopened", "created_at": "2026-11-01T00:00:00Z"},
            {"event": "closed", "created_at": "2026-11-01T06:00:00Z"},
            {"event": "reopened", "created_at": "2026-11-02T00:00:00Z"},
            {"event": "referenced", "commit_id": "c" * 40, "source": None},
        ]])

    monkeypatch.setattr(cl, "_gh", fake)
    tl = cl.fetch_timeline(7, "owner/name", {10: _mpr(10, sha="c" * 40)})
    assert tl.last_reopened_at == "2026-11-02T00:00:00Z"
    assert [r.number for r in tl.refs] == [10]


def test_a_DONE_row_is_cited_with_its_merge_and_reverifies_end_to_end(monkeypatch, capsys):
    """The integration shape of the whole mode: one issue whose closure record
    fired after the last reopen must print `verdict=done` with a real sha."""
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[{"number": 7, "state": "OPEN", "body": "- [x] shipped"}],
        merged_rows=[{"number": 10, "baseRefName": DEFAULT, "body": "Closes #7",
                      "mergedAt": "2026-11-02T00:00:00Z",
                      "mergeCommit": {"oid": "a" * 40},
                      "closingIssuesReferences": [{"number": 7, "repository": {"id": "R", "name": "name", "owner": {"id": "U", "login": "owner"}}, "url": "https://github.com/owner/name/issues/7"}]}],
        timeline=[{"event": "reopened", "created_at": "2026-11-01T00:00:00Z"}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT])
    out = capsys.readouterr().out
    assert code == 0
    assert "MERGED-BUT-OPEN(" + "a" * 40 + " 10 -> #7 verdict=done" in out
    assert "population=1 done=1" in out


def test_a_REOPENED_issue_prints_partial_and_exits_3(monkeypatch, capsys):
    monkeypatch.setattr(cl, "_gh", _merged_gh(
        opening=[{"number": 9548, "state": "OPEN", "body": ""}],
        merged_rows=[{"number": 9585, "baseRefName": DEFAULT, "body": "Closes #9548",
                      "mergedAt": "2026-11-01T00:00:00Z",
                      "mergeCommit": {"oid": "b" * 40},
                      "closingIssuesReferences": [{"number": 9548, "repository": {"id": "R", "name": "name", "owner": {"id": "U", "login": "owner"}}, "url": "https://github.com/owner/name/issues/9548"}]}],
        timeline=[{"event": "reopened", "created_at": "2026-11-02T00:00:00Z"}]))
    code = cl.main(["--merged-but-open", "--repo", "owner/name",
                    "--default-branch", DEFAULT])
    out = capsys.readouterr().out
    assert code == 3
    assert "reason=reopened" in out
def test_a_stray_run_costs_the_RENDERER_its_pairing_not_a_lost_claim():
    """The LENIENT arm's line bound must still honour the run the renderer paired.

    The renderer pairs a stray run left open on one line with the FIRST run of the
    same length after it — which can be the opener of a balanced pair on the next
    line — so the keyword here is PROSE and a per-line scan that blanked line 2
    reported nothing:

        a`
        `Closes #100`      ->  <p>a<code> </code>Closes #100`</p>

    That was a lost claim (no warning for a real closure) in the FINDING arm, the
    direction `_span_limit`'s docstring exists to protect, and it is what
    `_strip_inline_code`'s `pending` set closes (#10634 review P3).
    """
    body = "a`\n`Closes #100`\n"
    assert cl.parse_closing_refs(body) == [100]          # the FINDING arm sees it
    assert cl.parse_closing_refs(body, None, strict=True) == [100]   # …and it is prose
    # The pairing is per PARAGRAPH, not global: a blank line ends it, so the
    # keyword in the next paragraph's real code span stays a quotation. Without
    # this the `pending` set would trade the under-report for an over-report that
    # outlives the paragraph it belongs to.
    assert cl.parse_closing_refs("a`\n\n`Closes #100`\n") == []


if __name__ == "__main__":  # pragma: no cover
    # The module docstring advertises `Run standalone: python3 tests/test_closes_landing.py`.
    # This is the ONE block that makes that command real — it must stay at the END
    # of the file and be the only one. A lane left a second copy mid-file, so this
    # one never executed and its comment claimed a fix that the other block was
    # already providing; both are now one block, here.
    #
    # The carve-out is not optional: this file opens no graph, so it is exactly the
    # #1647 P4 carve-out case, and without it the session gate refuses every test
    # with "default pytest requires TORTOISE_DB_URI" — a standalone run that reds
    # for a reason that has nothing to do with what it tests.
    import os

    os.environ.setdefault("TORTOISE_TEST_CARVE_OUT", "1")
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), "-q"]))


def test_a_whitespace_only_line_does_not_carry_the_previous_line_s_per_line_state():
    """Round 38 review, P1 — a CRASH, and a regression against `origin/main`.

    `pushed`, `container` and `own_gap` are per-LINE state, but they were assigned only inside
    `if line.strip():`, so a whitespace-only line kept the PREVIOUS line's `pushed=True` while the
    `indent_code` branch had already popped the items that flag referred to. Two shapes then raised
    `IndexError`, and `main` answers both:

        '- [ ] b' + 8 spaces + blank                     strict=True -> pop from empty list
        '- [ ] criterion' + 5 spaces + 5 spaces + 'Closes #N'        -> list index out of range

    ⛔ A crash is not a narrow blast radius here: `sweep()` has no per-PR guard, so ONE such PR body
    aborts the whole sweep and the run exits 1 against a documented 0/3/2 contract — every other
    PR's result is lost with it. The state is reset at the top of the iteration, where "this line's
    own" begins, and these bodies are the pin because the shipped sweeps cannot see the shape (each
    of their families supplies at most one whitespace-only line).
    """
    bodies = ["- [ ] b\n        \n\n", "- [ ] criterion one\n     \n     \nCloses #100\n",
              "- item\n        \n        \n", "> - [ ] a\n        \n\n", "\t\n\t\n- [ ] b\n"]
    for body in bodies:
        for kw in ({}, {"strict": True}, {"indented_code": True}, {"strict": True, "indented_code": True}):
            cl.strip_code(body, **kw)          # must not raise
            cl.parse_closing_refs(body, None, strict=kw.get("strict", False))
    # …and the answer is `main`'s, not a crash-shaped placeholder: the indented blank tail is
    # dropped and the paragraph before it survives.
    assert cl.strip_code("- [ ] b\n        \n\n", strict=True) == "- [ ] b\n\n"


def test_a_paragraph_one_container_deeper_than_its_line_is_STILL_another_container():
    """Round 38 review, P2 — `_lazy_marker`'s depth term could not see INTERIOR nesting.

    The term was the line's own PREFIX depth, which misses two shapes where the open paragraph sits
    a container deeper than that prefix shows — and in both the renderer opens a NEW list, so the
    wide-gap content is CODE and crediting it is a `lands` GitHub never fires:

        '- > q' + '  2.     Closes #100'   the `> ` sits AFTER the marker -> paragraph at depth 1
        '> Notes' + 'lazy' + '2.     …'    a lazy line has no `>` prefix, so depth 0 was recorded
                                           and the open quote was forgotten

    The depth carried is now the PARAGRAPH's: a marker line adds the depth of its own content, and a
    line shallower than the paragraph it continues keeps the paragraph's depth (that is what lazy
    means). Control, which must stay prose: the same depth on both sides.
    """
    md = MarkdownIt("commonmark")

    def token_type(body: str) -> str | None:
        for token in md.parse(body):
            if "Closes #100" in getattr(token, "content", ""):
                return token.type
        return None

    for body, why in (("- > q\n  2.     Closes #100\n", "the `> ` is after the marker"),
                      ("> Notes\nlazy\n2.     Closes #100\n", "the lazy line dropped its `>`"),
                      ("- > q\n  10.     Closes #100\n", "same, a two-digit marker")):
        assert token_type(body) == "code_block", (why, body)
        assert cl.parse_closing_refs(body) == [], (why, body)
        assert cl.parse_closing_refs(body, None, strict=True) == [], (why, body)
    # The control: SAME container depth on both sides is still a lazy continuation.
    body = "> Notes\n> 2.     Closes #100\n"
    assert token_type(body) == "inline", body
    assert cl.parse_closing_refs(body) == [100], body
