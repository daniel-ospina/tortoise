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


def _decoy_and_ours() -> tuple[str, str]:
    """The `**N fixed · N open · N unknown**` headline: the document's OWN, and a wrong one.

    The decoy is derived from `parse_summary` on the real document rather than
    frozen, so the two check-6 controls keep their discriminating power through any
    count move: a decoy that EQUALS §2 is what makes the whole-body mutant
    (`_INDEX_COUNTS.search(body)`) agree and therefore FAIL, while the fixed check
    reads the entry row and still reports the disagreement.
    """
    text = DOC.read_text(encoding="utf-8")
    counts = pmc.parse_summary(text)
    assert counts, "the document has no §2 summary — the fixtures cannot be built"
    decoy = (f"**{counts['VERIFIED-FIXED']} fixed · {counts['OPEN']} open · "
             f"{counts['UNKNOWN']} unknown**")
    ours = (f"**{counts['VERIFIED-FIXED'] + 1} fixed · {counts['OPEN'] - 1} open · "
            f"{counts['UNKNOWN']} unknown**")
    return decoy, ours


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
    assert counts["VERIFIED-FIXED"] == sum(1 for r in rows if r.is_fixed) == 21
    assert counts["OPEN"] == sum(1 for r in rows if r.status == "OPEN") == 5
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


def test_a_fix_claim_with_no_PIN_is_drift(tmp_path):
    """Fails when a VERIFIED-FIXED row loses its pin.

    Reachable, and a MUTATION survivor until this control existed: emptying the pin
    cell (the mirror of emptying the anchor cell, which already had a control) made
    the guard certify `CLEAN` — a fixed claim with nothing in the tree that fails if
    the fix is undone. The reviewer's mutant `if not row.pins: pass` went unnoticed
    by every other test.
    """
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(copy.read_text(), 2788,
                        "`tests/test_event_state_carrier_2788.py`", "``")
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2788" in e and "no pin" in e for e in errors), errors


def test_a_duplicated_row_is_drift(tmp_path):
    """Fails when the same issue appears in two rows.

    Reachable: the map's tables are appended to by hand, and a duplicated row would
    double-count one defect in §2 while a reader sees two. MUTATION survivor until
    this control existed (`if count > 1:` could be deleted silently).
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text()
    marker = "| #2696 |"
    line = next(ln for ln in text.splitlines(keepends=True) if ln.startswith(marker))
    copy.write_text(text.replace(line, line + line, 1), encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("appears in 2 rows" in e for e in errors), errors


def test_a_missing_status_row_in_the_summary_is_drift(tmp_path):
    """Fails when §2 omits one of the three counts.

    Reachable: deleting a count is the easy way to stop it disagreeing (the same
    instinct the `Total tracked` control exists for). MUTATION survivor until this
    control existed — `if declared is None: continue` could be deleted silently.
    """
    copy = _doc_copy(tmp_path)
    kept = [ln for ln in copy.read_text().splitlines(keepends=True)
            if not ln.startswith("| OPEN |")]
    copy.write_text("".join(kept), encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("missing a row for OPEN" in e for e in errors), errors


def test_is_shallow_returns_None_when_git_cannot_answer(tmp_path):
    """Fails if an unreadable `--is-shallow-repository` is read as False.

    Reachable and cheap: a directory that is not a git repo makes `git rev-parse`
    exit non-zero. `False` there would certify the ancestor check, so the answer
    must be `None` — a MUTATION survivor until this control existed (the
    `returncode != 0` leg had no test).
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert pmc.is_shallow(plain) is None


def test_is_shallow_returns_None_for_an_unrecognised_answer(monkeypatch):
    """Fails if a `--is-shallow-repository` answer the parser does not know is False.

    Reachable: git has printed `true`/`false` for this flag for years, but a future
    or wrapped git could print anything; treating an unknown answer as "not
    shallow" turns a state error into a drift verdict on a true document. MUTATION
    survivor until this control existed (the unrecognised-answer leg had no test).
    """
    monkeypatch.setattr(
        pmc, "_git",
        lambda repo, *args: subprocess.CompletedProcess(args, 0, stdout="maybe\n", stderr=""),
    )
    assert pmc.is_shallow(REPO) is None


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


def test_a_pin_that_is_a_DIRECTORY_is_drift(tmp_path):
    """Fails when a pin points at a directory instead of a file.

    Reachable, and found by the FIFTH review round: `exists()` is true for a
    directory, so the pin check silently accepted one — a row could be
    VERIFIED-FIXED against `tests/` and nothing in that row could ever fail. The
    check now reports a non-file pin.
    """
    copy = _doc_copy(tmp_path)
    text = _rewrite_row(copy.read_text(), 2813, "`tests/test_capture_session.py`", "`tests/`")
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("#2813" in e and "is not a file" in e for e in errors), errors


def test_a_summary_count_that_disagrees_with_the_table_is_drift(tmp_path):
    """Fails when §2's total is edited without the rows.

    Reachable: the counts are prose in a table, and the whole document exists
    because a summary drifted from its table (the 2026-09-10 body).
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text().replace("| VERIFIED-FIXED | 21 |", "| VERIFIED-FIXED | 25 |", 1)
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
        "# index\n| the map | **22 fixed · 4 open · 0 unknown** | `" + DOC.name + "` |\n",
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
    # ⛔ THE DECOY CARRIES THE DOCUMENT'S OWN COUNTS, DERIVED. A frozen literal
    # (this control shipped `20 fixed · 6 open`, true when it was written) stops
    # discriminating the moment §2 moves: with the real 21/5 the "ours" row is
    # ALREADY wrong, so BOTH the fixed check and the whole-body mutant report a
    # disagreement and the assertion passes either way — a control that cannot
    # fail for the reason it states (#2820 review round 11, measured by mutating
    # `_INDEX_COUNTS.search(body)` back in). Deriving the decoy from `parse_summary`
    # makes the fixture impossible to age out that way.
    decoy, ours = _decoy_and_ours()
    index.write_text(
        "# index\n"
        f"| decoy map | {decoy} | `some-other-map.md` |\n"
        f"| ours | {ours} | `{DOC.name}` |\n",
        encoding="utf-8",
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "the two disagree" in errors[0], errors


def test_an_earlier_MENTION_of_the_path_cannot_satisfy_the_count_check(tmp_path):
    """Fails if a cross-reference to this document supplies the counts.

    Reachable, and found by the THIRD review round on the version that had
    already been fixed once for the same class: selecting the first line that
    merely NAMES the file let a ``| supersedes `MEMORY-SUBSTRATE-PROBLEM-MAP.md`
    — **N fixed · N open · N unknown** |`` cross-reference answer for a row that
    was stale. `docs/00_index.md` rows already cross-reference other docs by bare
    filename, so this is one "see also" from a false PASS. The registration is the
    row whose LAST cell is the path.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    index = repo / pmc.INDEX_REL
    decoy, ours = _decoy_and_ours()
    index.write_text(
        "# index\n"
        f"| another map | supersedes `{DOC.name}` — {decoy} |\n"
        f"| the map | {ours} | `{DOC.name}` |\n",
        encoding="utf-8",
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "the two disagree" in errors[0], errors


def test_two_rows_ending_with_the_path_are_reported_as_ambiguous(tmp_path):
    """Fails when two index rows both END with the document's path.

    Reachable: the index is `merge=union` and appended to from many lanes; a
    duplicated registration row is the shape that produces this, and picking one
    of the two would silently decide which counts are authoritative.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    index = repo / pmc.INDEX_REL
    index.write_text(
        "# index\n"
        "| a | **20 fixed · 6 open · 0 unknown** | `" + DOC.name + "` |\n"
        "| b | **20 fixed · 6 open · 0 unknown** | `" + DOC.name + "` |\n",
        encoding="utf-8",
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "must be exactly one row" in errors[0], errors


def test_an_index_entry_with_no_headline_counts_is_drift(tmp_path):
    """Fails when the index row omits the counts entirely.

    Reachable: a one-line registration ("| the map | path |") is the minimal way
    to satisfy a filename check, and it would leave the counts unverifiable while
    reading as registered.
    """
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / pmc.INDEX_REL).write_text(
        "# index\n| x | no counts here | `" + DOC.name + "` |\n", encoding="utf-8"
    )
    errors = pmc.check_registration(repo, DOC, DOC.read_text(encoding="utf-8"))
    assert errors and "states no" in errors[0], errors


def test_a_wrong_TOTAL_row_is_drift(tmp_path):
    """Fails when §2's `Total tracked` disagrees with the row count.

    Reachable, and found by the FOURTH review round: the other three counts were
    guarded and this one was not, so a document could say "Total tracked 30" over
    a 26-row table and `--check` still answered CLEAN. Every count the document
    prints is compared, or it is not a count.
    """
    copy = _doc_copy(tmp_path)
    text = copy.read_text().replace(
        "| **Total tracked** | **26** |", "| **Total tracked** | **30** |", 1
    )
    copy.write_text(text, encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("Total tracked says 30" in e for e in errors), errors


def test_a_missing_TOTAL_row_is_drift(tmp_path):
    """Fails when the total row is deleted rather than corrected.

    Reachable: deleting a count is the tempting way to make it stop disagreeing.
    """
    copy = _doc_copy(tmp_path)
    kept = [line for line in copy.read_text().splitlines(keepends=True)
            if "**Total tracked**" not in line]
    copy.write_text("".join(kept), encoding="utf-8")
    errors = pmc.run(REPO, copy).errors
    assert any("missing its `| **Total tracked**" in e for e in errors), errors


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


def test_a_corrupt_object_store_reads_as_no_answer_not_as_drift(tmp_path):
    """Fails if git's 128 is read as "no such commit" when the STORE is broken.

    `cat-file -e` exits 128 for both of its real negatives ("no such object", "not
    a commit") AND for a corrupt loose object — where git prints `error: inflate:
    data stream error` / `error: unable to unpack … header` / `fatal: loose
    object … is corrupt` while
    `rev-parse --is-shallow-repository` still answers `false`. That combination is
    the false DRIFT this tri-state exists to stop: `check_anchors` takes its
    `shallow is False` branch and blames the document.

    Built with real git, on a real corrupted object — no monkeypatch, because the
    thing under test IS git's rc/stderr contract. The missing-object half is
    asserted alongside it, so the arm cannot be satisfied by answering "no
    answer" to every 128.
    """
    repo = _git_repo(tmp_path)
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)}

    def g(*a: str) -> str:
        return subprocess.run(
            ["git", *a], cwd=repo, capture_output=True, text=True, env=env
        ).stdout

    head = g("rev-parse", "HEAD").strip()
    assert pmc.commit_exists(repo, head) is True
    # A real miss: git looked and did not find it. This must stay `False`.
    assert pmc.commit_exists(repo, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") is False

    # Corrupt the commit's loose object in place. `cat-file -e` then exits 128 with
    # git's own inflate/unpack complaint — and `rev-parse` still answers, which is
    # exactly the state that used to be reported as a document defect.
    obj = repo / ".git" / "objects" / head[:2] / head[2:]
    assert obj.exists(), f"loose object missing at {obj} — repo layout changed"
    obj.chmod(0o644)  # git writes loose objects read-only (0444)
    obj.write_bytes(b"not a zlib stream\n")
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{head}^{{commit}}"],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    assert probe.returncode == 128, probe.returncode
    assert pmc.is_shallow(repo) is False
    assert pmc.commit_exists(repo, head) is None, probe.stderr


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


def test_a_shallow_clone_HOLDING_the_anchor_still_reports_unverified(monkeypatch):
    """Fails if the `shallow is True` arm of `check_anchors` is deleted.

    Reachable, and found by the MUTATION-ATTRIBUTION round: the test above patches
    `is_shallow` only, with an anchor object that is ABSENT, so it leaves via the
    "not in this clone" branch — a `raise` inside the `shallow is True` arm left the
    whole suite green. In a real `--depth 1` clone that DID fetch the anchor, the
    arm is the one that runs, and deleting it flips a shallow clone from UNVERIFIED
    (2) to DRIFT (1) — a false red on a document that is not known to be wrong.
    The three patches are the only state that enters the arm.
    """
    monkeypatch.setattr(pmc, "is_shallow", lambda repo: True)
    monkeypatch.setattr(pmc, "commit_exists", lambda repo, sha: True)
    monkeypatch.setattr(pmc, "is_ancestor", lambda repo, sha: False)
    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == []
    assert report.unverified and "shallow" in report.unverified[0]
    report.errors = []
    assert report.code == 2


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


def test_a_missing_document_is_a_state_error_not_drift(tmp_path, capsys):
    """Fails if a document the run could not READ is reported as a document that is WRONG.

    Reachable: `--doc` pointed at a renamed or mistyped path (the docstring's own
    example), or a checkout where the page was moved. The old behaviour appended
    to `errors`, so the run answered DRIFT (exit 1) over a file it never opened —
    the diagnosis that sends a reader to fix a document that is not the problem.
    The docstring publishes exit 2 for exactly this state, so the code must too.
    """
    missing = tmp_path / "renamed-away.md"
    assert pmc.main(["--repo", str(REPO), "--doc", str(missing)]) == 2
    out = capsys.readouterr().out
    assert "NO ANSWER" in out
    assert "DRIFT" not in out


def test_a_document_that_cannot_be_READ_is_a_state_error_not_drift(tmp_path):
    """The other half of the same contract: present but unreadable.

    Reachable three ways, and all three used to raise out of `run()` into python's
    traceback exit — **1**, the DRIFT code, about a file nothing ever parsed:
    `--doc` pointed at a DIRECTORY (`IsADirectoryError`), at an unreadable file
    (`PermissionError`), or at a non-UTF-8 file (`UnicodeDecodeError`).
    """
    assert pmc.main(["--repo", str(REPO), "--doc", str(tmp_path)]) == 2

    binary = tmp_path / "binary.md"
    binary.write_bytes(b"\xff\xfe not utf-8")
    assert pmc.main(["--repo", str(REPO), "--doc", str(binary)]) == 2

    report = pmc.run(REPO, binary)
    assert report.errors == []
    assert report.state_errors and "could not be read" in report.state_errors[0]


def test_a_cat_file_that_cannot_answer_withholds_the_verdict(monkeypatch):
    """The reachable failing state the no-git control does NOT cover.

    `test_git_absent_is_a_state_error_not_a_traceback` makes EVERY call raise, so
    `is_shallow` also answers `None` and the `shallow is not False` arm fires no
    matter what `commit_exists` does. The state that reaches the hole is ONE call
    answering and another failing: `rev-parse --is-shallow-repository` answers
    `false` normally, and `cat-file` cannot answer (a timeout, a signalled
    process). With `commit_exists` a bare `== 0`, that produced **29 DRIFT errors
    and exit 1** — "anchor X is not a commit" about a correct document, over a run
    that measured no history. Both callers must be tri-state.
    """
    def _fake_run(argv, **kwargs):
        if "cat-file" in argv:
            raise pmc.subprocess.TimeoutExpired(cmd=argv, timeout=60)
        if "--is-shallow-repository" in argv:
            return pmc.subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="false\n", stderr=""
            )
        return pmc.subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(pmc.subprocess, "run", _fake_run)
    assert pmc.commit_exists(REPO, "deadbeefdeadbeef") is None

    report = pmc.run(REPO, DOC)
    assert report.errors == [], report.errors[:3]
    assert report.state_errors == []
    assert report.unverified and "could not answer whether the object exists" in (
        report.unverified[0]
    )
    assert report.code == 2


def test_a_signalled_cat_file_is_no_answer_not_a_missing_object(monkeypatch):
    """Fails if `commit_exists` is a DENY-list over one sentinel instead of an allow-list.

    `cat-file -e` has exactly two verdicts (0 = present, 128 = no such object / not
    a commit). Every other rc is "git did not answer", and the ones that actually
    occur are NOT exceptions `_git` can catch: a child killed by a signal returns a
    NEGATIVE rc (`-9` for SIGKILL/OOM, `-15` for SIGTERM — measured), and a PATH
    wrapper's own exit code is arbitrary. Mapping those to `False` reproduces the
    exact defect the sentinel was added for: 29 DRIFT errors, exit 1, about a
    correct document.
    """
    def _fake_run(argv, **kwargs):
        if "cat-file" in argv:
            # A SIGKILL'd child: subprocess.run RETURNS this, it does not raise.
            return pmc.subprocess.CompletedProcess(
                args=argv, returncode=-9, stdout="", stderr=""
            )
        if "--is-shallow-repository" in argv:
            return pmc.subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="false\n", stderr=""
            )
        return pmc.subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(pmc.subprocess, "run", _fake_run)
    assert pmc.commit_exists(REPO, "deadbeefdeadbeef") is None

    report = pmc.run(REPO, DOC)
    assert report.errors == [], report.errors[:3]
    assert report.code == 2

    # …and git's REAL negative verdict still answers `False`, so the allow-list
    # cannot be satisfied by sending every non-zero rc to `None`.
    monkeypatch.setattr(
        pmc.subprocess,
        "run",
        lambda argv, **kw: pmc.subprocess.CompletedProcess(
            args=argv, returncode=128, stdout="", stderr=""
        ),
    )
    assert pmc.commit_exists(REPO, "deadbeefdeadbeef") is False


def test_git_absent_is_a_state_error_not_a_traceback(monkeypatch):
    """Fails if a `git` that cannot be EXECUTED escapes as python's exit 1.

    Reachable: a scratch image, a container without git, a PATH replaced by a
    wrapper. `subprocess.run(["git", …])` then raises `FileNotFoundError`, which
    used to propagate out of `main()` — python's traceback exit is **1**, which in
    this tool's vocabulary means "the document disagrees with the tree". A tool
    that could not run git has measured nothing, so the answer is 2.
    """
    def _no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(pmc.subprocess, "run", _no_git)
    report = pmc.run(REPO, DOC)  # must not raise
    assert report.errors == [], report.errors
    assert report.code == 2


def test_an_unanswerable_ancestry_answer_withholds_the_verdict(monkeypatch):
    """Fails if a git FAILURE is reported as "the anchor did not land".

    Reachable: `merge-base --is-ancestor` exits 128 (not 1) for "that is not a
    commit", "HEAD is unborn", or an unreadable object store. Collapsing 128 into
    `False` attributed a git failure to the DOCUMENT — `the fix did not land on
    this branch` — a drift claim with a wrong cause. The arm is entered only with
    the object PRESENT (an absent object `continue`s earlier) and history
    readable, which is what the three patches below set.

    ⚠️ This one patches `is_ancestor` itself, which is the CONSUMER half. The
    rc→`None` mapping is proved separately and against the real function in
    `test_the_real_is_ancestor_maps_rc_128_to_no_answer` — without that case,
    reverting `is_ancestor` to `return rc == 0` left every test green.
    """
    monkeypatch.setattr(pmc, "is_shallow", lambda repo: False)
    monkeypatch.setattr(pmc, "commit_exists", lambda repo, sha: True)
    monkeypatch.setattr(pmc, "is_ancestor", lambda repo, sha: None)
    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == []
    assert report.unverified and "could not answer the ancestry" in report.unverified[0]
    assert report.code == 2


def test_the_real_is_ancestor_maps_rc_128_to_no_answer(monkeypatch):
    """The PRODUCER half: a real `_git` answering 128 must become `None`, not `False`.

    Fails if `is_ancestor` is written as `return _git(...).returncode == 0` — the
    shape this tool shipped with. That version maps EVERY non-zero rc to "not an
    ancestor": rc 1 ("not an ancestor", a real answer) and rc 128 ("that is not a
    commit" / unborn HEAD / unreadable object store, NO answer) become the same
    thing, and the document is blamed for a git failure.

    The run is driven through the REAL `_git` with only `subprocess.run` faked, so
    the mapping — not a monkeypatched collaborator — is what is measured.
    """
    real_run = pmc.subprocess.run

    def _fake_run(argv, **kwargs):
        if "merge-base" in argv:
            return pmc.subprocess.CompletedProcess(
                args=argv, returncode=128, stdout="", stderr="fatal: bad object"
            )
        if "--is-shallow-repository" in argv:
            return pmc.subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="false\n", stderr=""
            )
        return pmc.subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="", stderr=""
        )

    monkeypatch.setattr(pmc.subprocess, "run", _fake_run)
    assert pmc.is_ancestor(REPO, "deadbeefdeadbeef") is None

    report = pmc.Report()
    row = pmc.Row(issue=1, defect="d", status="VERIFIED-FIXED",
                  anchors=["deadbeefdeadbeef"], pins=[], line=1)
    errors = pmc.check_anchors(REPO, [row], report)
    assert errors == [], errors
    assert report.unverified and "could not answer the ancestry" in report.unverified[0]
    assert report.code == 2

    # The REAL rc-1 arm still answers `False` (a genuine "not an ancestor"), so the
    # tri-state cannot be satisfied by sending every non-zero rc to `None`.
    monkeypatch.setattr(
        pmc.subprocess,
        "run",
        lambda argv, **kw: pmc.subprocess.CompletedProcess(
            args=argv, returncode=1, stdout="", stderr=""
        ),
    )
    assert pmc.is_ancestor(REPO, "deadbeefdeadbeef") is False
    assert real_run is not None


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
    text = copy.read_text().replace("| OPEN | 5 |", "| OPEN | 3 |", 1)
    copy.write_text(text, encoding="utf-8")
    assert pmc.main(["--repo", str(REPO), "--doc", str(copy)]) == 1


@pytest.mark.parametrize("status", ["VERIFIED-FIXED", "OPEN", "UNKNOWN"])
def test_every_status_token_is_in_the_vocabulary(status):
    """The vocabulary is closed and complete — a token outside it is a typo."""
    assert status in pmc.STATUSES
    assert len(pmc.STATUSES) == 3
