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

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

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

def test_a_base_branch_with_no_pr_is_unresolved_not_a_pass():
    pr = _pr(40, SIBLING, "Closes #100")
    (finding,) = cl.classify(pr, _by_head(pr), DEFAULT)
    assert finding.verdict == "unresolved"
    assert finding.carrier is None


def test_a_cycle_is_unresolved_not_a_pass():
    b = _pr(51, "a-branch", "", head="b-branch")
    a = _pr(50, "b-branch", "Closes #100", head="a-branch")
    (finding,) = cl.classify(a, _by_head(a, b), DEFAULT)
    assert finding.verdict == "unresolved"


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
    stacked = _pr(70, SIBLING, "Closes #100")
    carrier = _pr(71, DEFAULT, "", head=SIBLING, state="MERGED",
                  merged_at="2026-11-02T14:07:51Z")
    (finding,) = cl.classify(stacked, _by_head(stacked, carrier), DEFAULT)
    assert finding.verdict == "inert"
    assert "ALREADY merged" in finding.detail
    assert "Close #100 by hand" in finding.detail
    assert "Re-state the keyword" not in finding.detail


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


# ── cycle 3: a CLOSED subject on a SIBLING base is judged by its carrier ────

def test_a_closed_subject_on_a_sibling_base_is_judged_by_its_carrier():
    subject = _pr(63, SIBLING, "Closes #100", state="CLOSED")
    carrier = _pr(64, DEFAULT, "Closes #100", head=SIBLING)
    (finding,) = cl.classify(subject, _by_head(subject, carrier), DEFAULT)
    assert finding.verdict == "lands"
    assert finding.carrier == 64


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
    assert "code block" in finding.detail


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
