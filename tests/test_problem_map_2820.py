"""Hermetic tests for `tools/problem_map_check.py` (#2820).

The memory-substrate problem map (`docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md`)
answers *"is this still broken?"* for 26 named defects. The issue it re-cuts went
stale for eight weeks — its body read as 26 problems waiting while the tree had
fixed 21 — so the map is only worth having if something can tell when its answers
age out. This file pins the guard that makes its machine-checkable half
falsifiable.

No network, no DB, no `gh`: the TRACKER half of the map is deliberately not
guarded (a status is a dated fact — §9 of the document says so), so nothing here
depends on GitHub.

EVERY NEGATIVE CONTROL MUTATES A COPY OF THE REAL DOCUMENT, and each test's
docstring states (a) the exact value/state that makes it fail and (b) why that
state is reachable. A guard whose negative case cannot be reached is a test that
cannot fail — it proves the code ran, not that the tree agrees with the document.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file; a docs-only edit runs `tier1` only, which is why the file
is dual-registered).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import problem_map_check as pmc  # noqa: E402

DOC = REPO / pmc.DOC_REL


# ── helpers ──────────────────────────────────────────────────────────────────


def _doc_copy(tmp_path: Path) -> Path:
    """A writable copy that KEEPS the document's name.

    The name matters: registration is checked by `doc.name` appearing in the
    index, so a copy called `doc.md` would fail check 6 for the wrong reason and
    mask whatever the test was actually about.
    """
    copy = tmp_path / DOC.name
    copy.write_text(DOC.read_text(encoding="utf-8"), encoding="utf-8")
    return copy


def _rewrite_row(text: str, issue: int, old: str, new: str) -> str:
    """Replace `old` with `new` on the row naming `issue`, and only there."""
    out, hit = [], 0
    for line in text.splitlines(keepends=True):
        if line.startswith(f"| #{issue} "):
            assert old in line, f"row #{issue} does not contain {old!r}"
            line = line.replace(old, new)
            hit += 1
        out.append(line)
    assert hit == 1, f"expected exactly one row for #{issue}, found {hit}"
    return "".join(out)


# ── the shipped document ────────────────────────────────────────────────────


def _history_is_available() -> bool:
    """Whether THIS clone can answer the anchor-ancestry claim (#5086's problem).

    `python-ci.yml`'s `test` job checks out at depth 1, so the anchors' objects
    are absent and the guard answers UNVERIFIED (2) — the document is not wrong,
    the clone is shallow. A test that demanded exit 0 there would red the
    required `test` job on a true document, which is a FALSE RED: measured on
    #10406 for the sibling guard, and measured here by cloning this worktree at
    depth 1 (3 failed, 17 passed) before this helper existed.
    """
    return pmc.is_shallow(REPO) is False


def test_the_shipped_map_is_clean():
    """Fails if any claim in the shipped document stops matching this tree.

    The drift assertions hold in EVERY clone (they are file reads). The
    ancestry assertion holds only where history is available; in a shallow clone
    the guard must answer UNVERIFIED, and asserting 0 there would be the false
    red above rather than a finding.
    """
    report = pmc.run(REPO, DOC)
    assert report.errors == []
    assert len(report.rows) == len(pmc.TRACKED) == 26
    if _history_is_available():
        assert report.code == 0, report.unverified
    else:
        assert report.code == 2, report.errors
        assert report.unverified


def test_the_taxonomy_counts_are_what_the_summary_declares():
    """Fails if §2's counts drift from the rows — the exact staleness class.

    Reachable: the counts are hand-written prose, so editing a row's status
    without editing §2 is a one-word change no other check would see.
    """
    rows, _ = pmc.parse_rows(DOC.read_text(encoding="utf-8"))
    counts = pmc.parse_summary(DOC.read_text(encoding="utf-8"))
    assert counts["VERIFIED-FIXED"] == sum(1 for r in rows if r.is_fixed) == 20
    assert counts["OPEN"] == sum(1 for r in rows if r.status == "OPEN") == 6
    assert counts["UNKNOWN"] == 0


def test_a_dropped_row_is_drift(tmp_path):
    """Fails when a tracked issue is silently missing from every table.

    Reachable: a table row is a line in a markdown file — deleting one while
    fixing a typo nearby leaves a 25-row map claiming 26, which is a CLEAN over
    nothing.
    """
    copy = _doc_copy(tmp_path)
    kept = [line for line in copy.read_text().splitlines(keepends=True)
            if not line.startswith("| #2813 ")]
    copy.write_text("".join(kept), encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2813" in e and "MISSING" in e for e in errors), errors


def test_a_row_outside_the_taxonomy_is_drift(tmp_path):
    """Fails when a row names an issue #2820 never tracked.

    Reachable: adding a 27th row is exactly how a map grows a duplicate of an
    existing entry (the fleet's 76-peer-issues class) — it must be a deliberate
    edit to `TRACKED`, not a silent table append.
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text()
    marker = "| #2734 |"
    assert marker in text
    dup = text.replace(marker, "| #9999 | invented row | OPEN | `` | `` |\n" + marker, 1)
    copy.write_text(dup, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#9999" in e and "outside the tracked taxonomy" in e for e in errors), errors


def test_a_fourth_status_spelling_is_drift(tmp_path):
    """Fails when a status is not one of the three tokens.

    Reachable: "VERIFIED-FIXED (partly)" is the natural thing to write for
    #2782/#2684, and it would make the map uncountable — the reason the
    vocabulary is closed.
    """
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(copy.read_text(), 2813, "| VERIFIED-FIXED |", "| FIXED? |")
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2813" in e and "not one of" in e for e in errors), errors


def test_a_fix_claim_with_no_anchor_is_drift(tmp_path):
    """Fails when a VERIFIED-FIXED row loses its commit.

    Reachable: an "obvious" fix is tempting to record without a sha ("it's in
    main"), which is the claim the anchor column exists to make impossible.
    """
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(copy.read_text(), 2813, "`0643bba86`", "``")
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2813" in e and "no anchor" in e for e in errors), errors


def test_a_nonexistent_pin_is_drift(tmp_path):
    """Fails when a pin path does not exist at this head.

    Reachable: a test module can be renamed or deleted by an unrelated lane, and
    the row would keep asserting it pins the fix.
    """
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(
        copy.read_text(), 2788, "`tests/test_event_state_carrier_2788.py`",
        "`tests/test_gone_forever_2788.py`",
    )
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2788" in e and "does not exist" in e for e in errors), errors


def test_a_pin_that_is_a_test_module_without_tests_is_drift(tmp_path):
    """Fails when a test module pin carries no module-level `def test_`.

    Reachable: `tests/_embedded.py` is a real module under `tests/` with no test
    function, and pins get moved by unrelated lanes — a row repointed at it would
    pass an "exists" check while pinning nothing.

    ⚠️ The check is a FLOOR, and the fixture beside it is the proof: it is a
    line-anchored `^def test_`, which a pytest FIXTURE named `test_*` also
    satisfies (`tests/conftest.py:1048` defines `def test_user`). So the control
    uses a module with none at all, which is the state that actually occurs.
    """
    assert (REPO / "tests" / "_embedded.py").exists()
    assert not pmc._DEF_TEST.search(
        (REPO / "tests" / "_embedded.py").read_text(encoding="utf-8")
    )
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(
        copy.read_text(), 2788, "`tests/test_event_state_carrier_2788.py`",
        "`tests/_embedded.py`",
    )
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2788" in e and "pins nothing" in e for e in errors), errors


def test_a_summary_count_that_disagrees_with_the_table_is_drift(tmp_path):
    """Fails when §2's total is edited without the rows.

    Reachable: the counts are prose in a table, and the whole document exists
    because a summary drifted from its table (the 2026-09-10 body).
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text().replace("| VERIFIED-FIXED | 20 |", "| VERIFIED-FIXED | 25 |", 1)
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("summary says 25" in e for e in errors), errors


def test_an_index_entry_whose_counts_disagree_is_drift(tmp_path):
    """Fails when `docs/00_index.md` states counts the map's §2 does not have.

    Reachable, and MEASURED on this page's first landing: the index row said
    `22 fixed · 4 open` for eight of the thirteen minutes between the doc being
    written and the verification gate reading it, and `--check` was CLEAN —
    because registration was a filename match. The index is the artifact most
    readers meet first, so a disagreeing count there is the same defect as a
    disagreeing §2.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    index = repo / pmc.INDEX_REL
    index.write_text(
        "# index\n| x | `" + DOC.name + "` — **22 fixed · 4 open · 0 unknown** |\n",
        encoding="utf-8",
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "the two disagree" in errors[0], errors


def test_a_decoy_index_ROW_cannot_satisfy_the_count_check(tmp_path):
    """Fails if the count is read from ANY line instead of this document's row.

    Reachable, and found by fresh review on the first landing: the check scanned
    the whole index, so an EARLIER row carrying the same phrasing satisfied it
    while this document's row was stale — a false PASS inside the check written
    to stop stale counts. The index is a file of long summary rows, and the
    sibling maps are the kind that could carry these words first.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    index = repo / pmc.INDEX_REL
    index.write_text(
        "# index\n"
        "| decoy map | `some-other-map.md` — **21 fixed · 5 open · 0 unknown** |\n"
        "| ours | `" + DOC.name + "` — **22 fixed · 4 open · 0 unknown** |\n",
        encoding="utf-8",
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "the two disagree" in errors[0], errors


def test_an_index_entry_with_no_headline_counts_is_drift(tmp_path):
    """Fails when the index row omits the counts entirely.

    Reachable: a one-line registration ("| the map | path |") is the minimal way
    to satisfy a filename check, and it would leave the counts unverifiable while
    reading as registered.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / pmc.INDEX_REL).write_text(
        "# index\n| x | `" + DOC.name + "` |\n", encoding="utf-8"
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "states no" in errors[0], errors


def test_an_unregistered_document_is_drift(tmp_path):
    """Fails when the page is not listed in `docs/00_index.md`.

    Reachable: a new architecture page lands without its index row all the time;
    the page is then committed but unreachable, which for a map is the same as
    not existing.
    """
    copy = _doc_copy(tmp_path)
    # The index lives in the repo, not beside the copy — so the registration
    # check is exercised against a tmp REPO whose index lacks the row.
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / pmc.INDEX_REL).write_text("# index\n", encoding="utf-8")
    errors = pmc.check_registration(repo, copy)
    assert errors and "not listed" in errors[0], errors


def test_a_row_with_an_extra_pipe_is_reported(tmp_path):
    """Fails when a cell contains a literal `|`.

    Reachable: writing a `|` inside a defect description is an ordinary typo and
    would silently shift the status into the pin column — a mis-grade, not a
    missing row, so it is reported as its own error.
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text().replace(
        "| #2813 | v2 product capture", "| #2813 | v2 | product capture", 1
    )
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("cells, expected 5" in e for e in errors), errors


# ── git-backed checks, on a REAL throwaway repo ─────────────────────────────


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "gitrepo"
    repo.mkdir()

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
            env={
                "PATH": "/usr/bin:/bin:/usr/local/bin",
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
                "HOME": str(tmp_path),
            },
        ).stdout

    run("init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n")
    run("add", "a.txt")
    run("commit", "-q", "-m", "first")
    (repo / "a.txt").write_text("ab\n")
    run("commit", "-q", "-am", "second")
    return repo


def test_an_anchor_on_a_non_ancestor_commit_is_drift(tmp_path):
    """Fails when the anchor exists but the fix is not on this branch.

    The sharpest reachable case: a commit object that exists (so `cat-file -e`
    passes) but sits on a branch that never merged. This is the "a branch
    somewhere mentions the issue" failure the ancestor half exists for.

    Built with real git in a throwaway repo: `first` -> `second`, then HEAD is
    reset to `first`, leaving `second` present-but-not-ancestor.
    """
    repo = _git_repo(tmp_path)
    g = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)},
    ).stdout
    second = g("rev-parse", "HEAD").strip()
    g("reset", "-q", "--hard", "HEAD~1")
    assert pmc.commit_exists(repo, second)
    assert not pmc.is_ancestor(repo, second)

    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=[second], pins=[], line=1)
    report = pmc.Report()
    errors = pmc.check_anchors(repo, [row], report)
    assert errors and "NOT an ancestor" in errors[0], (errors, report.unverified)


def test_an_ancestor_anchor_passes_on_the_same_repo(tmp_path):
    """The positive control for the test above — same fixture, no drift.

    Without it, the drift assertion could pass because the helper always
    returns False.
    """
    repo = _git_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)},
    ).stdout.strip()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=[head], pins=[], line=1)
    report = pmc.Report()
    assert pmc.check_anchors(repo, [row], report) == []
    assert report.unverified == []


def test_a_shallow_clone_reports_unverified_never_drift(monkeypatch):
    """Fails if a shallow clone is allowed to call a TRUE claim drift.

    Reachable and MEASURED in this repo's own CI: `actions/checkout` fetches one
    commit, so `git cat-file -e` for an anchor from 2026-09 fails — and the
    document is not wrong, the clone is shallow. The status must be the state
    error (2), never drift (1).
    """
    monkeypatch.setattr(pmc, "is_shallow", lambda repo: True)
    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == []
    assert report.unverified and "shallow" in report.unverified[0]
    report.errors = []
    assert report.code == 2


def test_an_unreadable_completeness_answer_withholds_the_verdict(monkeypatch):
    """Fails if an unreadable `--is-shallow-repository` certifies drift.

    Reachable: `git rev-parse --is-shallow-repository` can exit non-zero (no git,
    a bare tmp dir) or print something unexpected. Reading that as `False` would
    let a state error red a TRUE document, so the verdict is withheld.

    ⚠️ The three patches are the state the arm requires, and the first version of
    this test missed it: it patched `commit_exists -> False` as well, so control
    never reached the `is_shallow is None` arm at all (an absent object `continue`s
    earlier) and deleting that arm kept every test green — a control with no
    reachable failing state, found by fresh review. `commit_exists` and
    `is_ancestor` are therefore patched to the only combination that enters the
    arm: object PRESENT, not an ancestor, completeness unreadable.
    """
    monkeypatch.setattr(pmc, "is_shallow", lambda repo: None)
    monkeypatch.setattr(pmc, "commit_exists", lambda repo, sha: True)
    monkeypatch.setattr(pmc, "is_ancestor", lambda repo, sha: False)
    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == []
    assert report.unverified and "completeness" in report.unverified[0]
    report.errors = []
    assert report.code == 2


def test_an_absent_anchor_object_in_a_shallow_clone_is_unverified(monkeypatch):
    """The other unverified arm, kept separate so each has its own control.

    Reachable and measured in CI: at depth 1 the anchor object is simply not in
    the clone, so `git cat-file -e` fails. That is a missing object, not a
    missing commit — the map may be right — so it must read UNVERIFIED, not drift.
    """
    monkeypatch.setattr(pmc, "is_shallow", lambda repo: True)
    monkeypatch.setattr(pmc, "commit_exists", lambda repo, sha: False)
    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == []
    assert report.unverified and "not in this clone" in report.unverified[0]
    report.errors = []
    assert report.code == 2


# ── the CLI contract ────────────────────────────────────────────────────────


def test_the_cli_agrees_or_declares_itself_unverified(capsys):
    """The rail-facing contract: 0 = agree, 2 = cannot answer, and the line says which.

    In a full clone that is 0 + "CLEAN"; at depth 1 it is 2 + "UNVERIFIED". Both
    are correct answers about the same document — what must never happen is the
    third code, a drift verdict on a shallow clone.
    """
    code = pmc.main(["--repo", str(REPO)])
    out = capsys.readouterr().out
    assert "26 row(s) parsed" in out
    if _history_is_available():
        assert code == 0 and "CLEAN" in out
    else:
        assert code == 2 and "UNVERIFIED" in out


def test_the_cli_exits_one_on_drift(tmp_path):
    """Drift must be a non-zero, unambiguous exit code for a scripted caller.

    The mutation is deliberately a SUMMARY edit, not an anchor edit: the summary
    count is checkable with no history at all, so this control exercises exit 1
    in a depth-1 clone too (an anchor mutation would answer 2 there and the test
    would be measuring the clone, not the guard).
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text().replace("| OPEN | 6 |", "| OPEN | 4 |", 1)
    copy.write_text(text, encoding="utf-8")
    assert pmc.main(["--repo", str(REPO), "--doc", str(copy)]) == 1


@pytest.mark.parametrize("status", ["VERIFIED-FIXED", "OPEN", "UNKNOWN"])
def test_every_status_token_is_in_the_vocabulary(status):
    """The vocabulary is closed and complete — a token outside it is a typo."""
    assert status in pmc.STATUSES
    assert len(pmc.STATUSES) == 3
