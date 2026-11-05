"""Tests for tools/issue_process_lint.py — the two advisory arms, their negative controls, and the
adversarial shapes a first version of this tool got wrong.

RED-FIRST NOTE (why each arm has a pair)
----------------------------------------
The tool exists to flag exactly two shapes and to *not* flag two others. A test file that only
proved the flags would be satisfied by a tool that flags everything, so every arm is paired with the
legitimate population it must leave alone:

    flagged:        PR CLAIMS an issue, only a linking word resolves it  <->  control: claims none
    flagged:        issue has WIP, no `## Satisfies` checklist           <->  control: fresh issue

The pairs are the requirement, not decoration: a false positive points bureaucracy at legitimate
work, which is worse than the defect it would catch. A review of the first version measured 19 of
28 live open PRs flagged (68 %) because it counted *mentions* — criterion ids, a PR's own number, a
cited precedent, a command in inline code — so the CLAIM-vs-MENTION tests below are regression pins
for a real, measured false-positive population.

Nothing here needs a database, and nothing here touches the network: the `gh` seam is monkeypatched
(or given an impossible repo) so `rc=2 != rc=0` is pinned without a live call.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "issue_process_lint.py"
sys.path.insert(0, str(ROOT / "tools"))

import issue_process_lint as L  # noqa: E402


# --- arm 1: a PR that CLAIMS an issue which nothing resolves ------------------------------
def test_a_pr_that_only_says_refs_is_flagged():
    """The measured shape: 18 of 18 P0/P1-naming PRs used `Refs` (#10534)."""
    findings = L.classify_pr("This fixes the thing.\n\nRefs #10534\n", 1234)
    assert len(findings) == 1
    assert findings[0].arm == L.ARM_PR
    assert findings[0].evidence["unresolved"] == [10534]


@pytest.mark.parametrize("body", [
    "Closes #10534\n",
    "closes #10534\n",
    "Fixes #10534\n",
    "Resolves: #10534\n",
    "Part of the programme.\n\nRefs #4282 and Closes #4282\n",
])
def test_a_closing_keyword_resolves_the_claim(body):
    assert L.classify_pr(body, 1) == []


def test_satisfies_resolves_the_claim_for_a_partial_pr():
    """`Satisfies #N: <ids>` is what a partial PR writes instead of `Closes`."""
    assert L.classify_pr("Satisfies #4282: 3.2a, 3.2b\nRefs #4282\n", 1) == []


def test_a_keyword_for_a_different_issue_does_not_resolve_the_claimed_one():
    """THE #10534 SHAPE EXACTLY: a closing keyword elsewhere does not close the issue it serves."""
    findings = L.classify_pr("Refs #4282\n\nCloses #9999\n", 1)
    assert [f.evidence["unresolved"] for f in findings] == [[4282]]
    assert findings[0].evidence["resolved_by_closing"] == [9999]


def test_a_pr_that_claims_no_issue_is_not_flagged():
    """NEGATIVE CONTROL: not every change serves an issue. Flagging this is the bureaucracy."""
    body = "chore: bump the pinned ruff version\n\nNo issue: a dependency bump.\n"
    assert L.classify_pr(body, 999) == []


# --- mention vs claim: the measured 68 % false-positive population -----------------------
@pytest.mark.parametrize("body,why", [
    ("This reds only #10 and #11, the two criterion ids.\n", "criterion id in prose"),
    ("Pins the source count (#9) and the guard #2981 cited above.\n", "cited precedent"),
    ("Ported from the review on #11064.\n", "its own PR number, quoted"),
    ("A fence example:\n\n```markdown\nRefs #1234\n```\n\nNo claim here.\n", "fenced quote"),
    ("Run `gh pr view #1234` to see it.\n", "inline code"),
    ("<!-- tracking: Refs #4282 -->\nNothing claimed.\n", "HTML comment"),
])
def test_a_mention_is_not_a_claim(body, why):
    assert L.classify_pr(body, 11064) == [], why


def test_a_declined_closing_reference_is_the_authors_answer_not_a_defect():
    """PR #11054, measured: `Refs #10602 — deliberately **not** `Closes`.` A measurement PR that
    serves an issue without fixing it says so, and that sentence must clear the flag."""
    body = "Refs #10602 — deliberately **not** `Closes`.\n"
    assert L.classify_pr(body, 11054) == []
    # the same sentence on the next line does NOT clear a different line's claim
    assert [f.evidence["unresolved"] for f in L.classify_pr("Refs #1\n\nNot closing this one.\n", 2)] == [[1]]


def test_the_prs_own_number_is_never_a_claim():
    assert L.classify_pr("Refs #77\n", 77) == []


def test_a_foreign_repo_claim_is_not_counted_but_a_self_qualified_one_is():
    body = "Refs premise-labs/other#12\n"
    assert L.classify_pr(body, 42, repo="daniel-ospina/tortoise") == []  # another repo's issue
    findings = L.classify_pr("Refs daniel-ospina/tortoise#12\n", 42, repo="daniel-ospina/tortoise")
    assert [f.evidence["unresolved"] for f in findings] == [[12]]


def test_an_unterminated_fence_is_reported_not_silently_swallowed(tmp_path):
    """A miss in the silent direction is the failure mode; the note makes it visible."""
    body = tmp_path / "body.md"
    body.write_text("Refs #1234\n\n```markdown\nRefs #999\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--body-file", str(body)],
                       capture_output=True, text=True)
    assert "unterminated fence" in r.stdout


# --- the fence grammar belongs to closes_landing, not to a weaker copy here (review P2) ---
#
# The first version recognised a fence only when the first NON-SPACE character of the line was a
# backtick/tilde, so a quoted example written as `- ```markdown` (the fence sitting ON the
# list-marker line) or `> ```markdown` (inside a blockquote) never opened one: every line of the
# block was appended as PROSE, and a `Refs #N` the author QUOTED became a claim. That is the exact
# false-positive direction this module's docstring says was already paid for, in the tool it cites.
# The mature copy is `tools/closes_landing.py::strip_code` — the one a real CommonMark renderer is
# checked against (`tests/test_closes_landing.py`) — so this module imports it instead of carrying
# a third grammar. Each shape below is asserted from BOTH sides, so the two cannot drift apart.
_CODE_BLOCK_RE = re.compile(r"<pre><code\b.*?</code></pre>|<code\b.*?</code>", re.S)
_FIRST_REF_RE = re.compile(r"#(\d+)")

_CONTAINER_FENCE_SHAPES = [
    ("- ```markdown\n  Refs #1234\n  ```\n\nNo claim here.\n", "a fence ON the list-marker line"),
    ("1. ```markdown\n   Refs #1234\n   ```\n", "an ORDERED-list marker-line fence"),
    ("> ```markdown\n> Refs #1234\n> ```\n\nNo claim here.\n", "a fence inside a blockquote"),
    ("> - ```markdown\n>   Refs #1234\n>   ```\n\nNo claim.\n", "a marker-line fence inside a quote"),
]


@pytest.mark.parametrize("body,why", _CONTAINER_FENCE_SHAPES)
def test_a_quoted_example_in_any_container_is_code_not_a_claim(body, why):
    html = MarkdownIt("commonmark").render(body)
    prose = _CODE_BLOCK_RE.sub("", html)
    n = int(_FIRST_REF_RE.search(body).group(1))
    assert f"#{n}" not in prose, (why, html)   # the oracle: a renderer calls this CODE
    assert L.classify_pr(body, 1) == [], why    # …so the lint must not flag what it quoted


def test_a_fence_inside_a_quote_ends_when_the_quote_ends():
    """The OTHER direction — and the reason this is an import rather than a `>`-stripping fix.

    A blanket "consume any blockquote markers, then look for a fence run" would keep this block
    open to EOF and silently swallow the REAL claim after it. CommonMark ends a block with its
    CONTAINER, so `Refs #9999` below is prose — and losing it is the silent direction.

    This case passes on the PRE-FIX code too (a fence the old scan never opened hid nothing), so it
    is not the non-vacuity pin for the fix: it is the control that CONSTRAINS it. `> Refs #9999` as
    plain quoted prose must stay a claim — GitHub acts on a keyword in a quoted paragraph — which is
    what `closes_landing`'s depth comparison gets right and a prefix-stripping shortcut does not.
    """
    body = "> ```markdown\n> quoted\nRefs #9999\n"
    assert "#9999" in _CODE_BLOCK_RE.sub("", MarkdownIt("commonmark").render(body))
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[9999]]


def test_the_hidden_line_count_is_the_UNTERMINATED_fence_only():
    """The note is only honest if a block that CLOSES hides nothing."""
    assert L.strip_code("- ```markdown\n  Refs #1\n")[1] == 1
    assert L.strip_code("- ```markdown\n  Refs #1\n  ```\n")[1] == 0


def test_the_unterminated_fence_note_survives_a_sweep(monkeypatch, capsys):
    """Only the two OFFLINE branches collected the note; `--sweep` — the one mode that reads a
    POPULATION — silently swallowed the one thing that says the scan could not see a whole body."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: [
        (7, "Refs #1\n\n```markdown\nRefs #2\n", []),
    ])
    assert L.main(["--sweep"]) == L.RC_FINDINGS
    out = capsys.readouterr().out
    assert "unterminated fence" in out
    assert "#7" in out


# --- arm 2: an issue with WIP and no `## Satisfies` checklist -----------------------------
SATISFIES = """## Satisfies

- [ ] `3.2a` — the five `run_onboarding` tools are gone; `tools/surface-target-check.py` prints 76
- [x] `3.2b` — the retired names warn; tests/test_retired_names.py
"""

IN_FLIGHT = "IN FLIGHT: 3.2c — branch feat/surface-3-2c — box cell-4 — since 2026-11-05T09:00Z"


def test_wip_without_satisfies_is_flagged():
    findings, measured = L.classify_issue(["opening note", IN_FLIGHT], 4282)
    assert [f.arm for f in findings] == [L.ARM_ISSUE]
    assert measured["wip_source"] == "comment-marker"
    assert measured["marker_count"] == 1
    assert findings[0].evidence["populations_not_consulted"] == ["branches", "worktrees", "ref-claims"]


def test_wip_with_satisfies_is_not_flagged():
    assert L.classify_issue([IN_FLIGHT, SATISFIES], 4282)[0] == []


def test_a_fresh_issue_with_no_criteria_is_not_flagged():
    """NEGATIVE CONTROL: criteria do not exist at creation — that is normal, not a defect."""
    findings, measured = L.classify_issue(["filed from the agent_finding form", "discussion"], 11111)
    assert findings == []
    assert measured["marker_count"] == 0  # the OUTPUT must be able to say this


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: 3.2c — branch b",
    "**IN FLIGHT:** 3.2c — branch b",
    "- IN FLIGHT: 3.2c — branch b",
    "IN-FLIGHT: 3.2c — branch b",
])
def test_the_natural_markdown_shapes_are_still_markers(marker):
    assert L.in_flight_markers([marker]) == ["3.2c — branch b"]


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: ",
    "IN FLIGHT: none — released 2026-11-05",
    "IN FLIGHT: released",
    "IN FLIGHT: <ids> — branch <b> — box <c> — since <UTC>",   # the block being QUOTED
])
def test_a_release_or_a_quoted_convention_is_not_wip(marker):
    assert L.in_flight_markers([marker]) == []


def test_a_satisfies_heading_with_no_checkbox_is_not_a_checklist():
    findings, _ = L.classify_issue([IN_FLIGHT, "## Satisfies\n\nTo be derived.\n"], 4282)
    assert [f.arm for f in findings] == [L.ARM_ISSUE]


def test_a_tick_in_a_later_comment_still_counts():
    """Criteria are ticked once proved; requiring heading+boxes in ONE comment re-flags a finished issue."""
    findings, _ = L.classify_issue([IN_FLIGHT, "## Satisfies\n\n- [ ] a\n- [ ] b\n", "- [x] a\n"], 1)
    assert findings == []


def test_an_injected_wip_verdict_is_honoured_and_attributed():
    """`--wip` is how a caller forwards tools/who_is_on.py's branch/worktree verdict."""
    findings, measured = L.classify_issue([], 4282, wip=True)
    assert measured["wip_source"] == "injected"
    assert [f.arm for f in findings] == [L.ARM_ISSUE]
    assert findings[0].evidence["populations_not_consulted"] == []


def test_an_injected_no_wip_verdict_clears_the_marker():
    assert L.classify_issue([IN_FLIGHT], 4282, wip=False)[0] == []


# --- CLI contract: advisory, exit-coded, never a gate ------------------------------------
def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def test_cli_offline_pr_exit_codes(tmp_path):
    flagged = tmp_path / "body.md"
    flagged.write_text("Refs #10534\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("Closes #10534\n", encoding="utf-8")
    r = _run("--body-file", str(flagged))
    assert r.returncode == L.RC_FINDINGS, r.stderr
    assert "advisory only" in r.stdout
    assert _run("--body-file", str(clean)).returncode == L.RC_CLEAN


def test_cli_offline_issue_mode_with_injected_wip(tmp_path):
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([{"body": "a comment"}]), encoding="utf-8")
    body = tmp_path / "issue.md"
    body.write_text("The problem.\n", encoding="utf-8")
    r = _run("--issue-body-file", str(body), "--comments-file", str(comments), "--wip")
    assert r.returncode == L.RC_FINDINGS, r.stderr
    assert _run("--issue-body-file", str(body), "--comments-file", str(comments)).returncode == L.RC_CLEAN


def test_cli_reads_the_issue_body_file_not_only_the_comments(tmp_path):
    """`--issue-body-file` was decoration in the first version: the file was never read."""
    body = tmp_path / "issue.md"
    body.write_text(IN_FLIGHT + "\n", encoding="utf-8")
    comments = tmp_path / "c.json"
    comments.write_text("[]", encoding="utf-8")
    r = _run("--issue-body-file", str(body), "--comments-file", str(comments))
    assert r.returncode == L.RC_FINDINGS, r.stdout + r.stderr
    # and the same body carrying the checklist clears it
    body.write_text(IN_FLIGHT + "\n\n" + SATISFIES, encoding="utf-8")
    assert _run("--issue-body-file", str(body), "--comments-file", str(comments)).returncode == L.RC_CLEAN


def test_the_live_issue_path_reads_the_body_not_only_the_comments(monkeypatch):
    """`--issue` DISCARDED `fetch_issue`'s body while the offline sibling included it, so identical
    content got two different verdicts depending on whether it came from gh or from a file —
    *WIP with no checklist* on the live path, *clean* offline (review P2)."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "fetch_issue", lambda number, repo: (IN_FLIGHT + "\n", []))
    assert L.main(["--issue", "4282"]) == L.RC_FINDINGS
    # …and the checklist in that same body still clears it (the pair, not just the flag)
    monkeypatch.setattr(L, "fetch_issue",
                        lambda number, repo: (IN_FLIGHT + "\n\n" + SATISFIES, []))
    assert L.main(["--issue", "4282"]) == L.RC_CLEAN


def test_the_issue_sweep_reads_the_body_too(monkeypatch):
    """`--sweep-issues` discarded it the same way, so the whole population was measured smaller
    than the offline path measures one issue."""
    def fake_gh(args, timeout=60):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 7}])
        return json.dumps({"body": IN_FLIGHT, "comments": [{"body": "a comment"}]})

    monkeypatch.setattr(L, "_gh", fake_gh)
    assert L.sweep_issues(None, 10, None) == [(7, [IN_FLIGHT, "a comment"])]


def test_cli_refuses_an_injected_verdict_for_a_population():
    r = _run("--sweep-issues", "--wip")
    assert r.returncode == 2  # argparse usage error: one verdict cannot speak for a population
    assert "cannot be applied" in (r.stderr + r.stdout)


def test_cli_json_shape(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    payload = json.loads(_run("--body-file", str(body), "--json").stdout)
    assert payload["population"] == "1 body (offline)"
    assert payload["findings"][0]["arm"] == L.ARM_PR


def test_cli_annotate_emits_warning_lines(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    r = _run("--body-file", str(body), "--annotate")
    assert "::warning title=issue-process-lint" in r.stdout
    assert r.returncode == L.RC_FINDINGS


def test_an_unmeasurable_read_is_not_a_pass(monkeypatch):
    """rc=2 is distinct from rc=0 on purpose. Pinned WITHOUT a network call."""
    def boom(args, timeout=60):
        raise L.Unmeasurable("gh not found: simulated")
    monkeypatch.setattr(L, "_gh", boom)
    assert L.main(["--pr", "1"]) == L.RC_UNMEASURABLE


# --- the pin that keeps it advisory ------------------------------------------------------
def test_the_lint_is_never_wired_as_a_required_check():
    """Per-STEP parse, not a string that could sit on some unrelated step."""
    workflows = ROOT / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                blob = json.dumps(step)
                if "issue_process_lint" not in blob:
                    continue
                assert step.get("continue-on-error") is True, (
                    f"{path.name}:{job_name} mounts issue_process_lint without "
                    f"`continue-on-error: true` — this lint is advisory by ruling "
                    f"(#10534 / closes-coherence precedent)")
                # and it must not be able to sit in the required-aggregate's `needs`
                gate = (doc.get("jobs") or {}).get("python-ci-gate") or {}
                assert job_name not in (gate.get("needs") or [])
    mergify = ROOT / ".mergify.yml"
    if mergify.exists():
        assert "issue_process_lint" not in mergify.read_text(encoding="utf-8")


def test_the_doc_block_matches_both_templates():
    """The block is copied into two YAML templates. A byte-equal pin is what stops the drift."""
    doc = (ROOT / "docs" / "ops" / "ISSUE-WORKING-PROCESS.md").read_text(encoding="utf-8")
    m = L.re.search(r"```markdown\n(## Working this issue — process.*?)```", doc, L.re.DOTALL)
    assert m, "the doc no longer carries the paste-ready block in a ```markdown fence"
    block = m.group(1).strip()
    template = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "agent_finding.yml").read_text(encoding="utf-8"))
    fields = [b for b in template["body"] if b.get("id") == "working_process"]
    assert fields, "agent_finding.yml no longer carries the working_process field"
    assert fields[0]["attributes"]["value"].strip() == block
