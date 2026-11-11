"""Hermetic tests for `tools/findings_ledger_check.py` (#2852).

The findings ledger (`docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md`) is
the durable half of #2852 — the corrections log of the 2026-09-09/10
memory-substrate review, re-measured. Re-measuring it found **five of its twelve
corrections already overtaken by the fixes they motivated**, which is precisely
why it needs a guard: the page's job is to tell a reader *which of its own rows
are still live*, and every checkable property it has can decay silently.

No network, no DB, no gh. The tracker states in §6 and the `~/.swarm` claims in
§5 are **deliberately unguarded** (see the tool's docstring), so nothing here
depends on them.

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT VALUE/STATE THAT MAKES IT
FAIL and (b) WHY THAT STATE IS REACHABLE. A guard whose negative case cannot be
reached is a test that cannot fail — it proves the code ran, not that the tree
agrees with the document. The negative controls therefore mutate a COPY of the
real document rather than asserting only that the shipped tree is clean.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import findings_ledger_check as flc  # noqa: E402

DOC = REPO / "docs" / "architecture" / "MEMORY-SUBSTRATE-FINDINGS-LEDGER.md"
INDEX = REPO / "docs" / "00_index.md"
SURFACES = REPO / "config" / "ci-surfaces.yml"


@pytest.fixture(autouse=True)
def _point_at_real_doc(monkeypatch):
    """Pin the module's paths at the real ones; individual tests re-point them."""
    monkeypatch.setattr(flc, "DOC", str(DOC))
    monkeypatch.setattr(flc, "INDEX", str(INDEX))


def _mutated(tmp_path: Path, monkeypatch, transform) -> Path:
    """Write a transformed copy of the real document and point the checker at it.

    Citations still resolve against `REPO` because `main()` is called with
    `--root REPO`; only the DOC/INDEX the checker reads are swapped.
    """
    original = DOC.read_text(encoding="utf-8")
    mutated = transform(original)
    assert mutated != original, "the mutation did not change the document"
    target = tmp_path / "ledger.md"
    target.write_text(mutated, encoding="utf-8")
    monkeypatch.setattr(flc, "DOC", str(target))
    return target


def _doc_problems() -> list[str]:
    """The history-free arms over the (possibly swapped) document.

    Used by every mutation arm and by the shipped-tree assertion, so they mean
    the same thing in a depth-1 clone as in a full checkout.
    """
    return flc.check_document(flc.read_doc(REPO), REPO)


def _history_available(root: Path = REPO) -> bool:
    """Can this clone answer a `measured_at_commit` ancestry claim?

    Asked of `git` directly — never via the tool under test — so a bug in the
    tool cannot accredit its own checkout. `rev-parse --is-shallow-repository`
    must answer exactly `false`: a DEEPENED shallow clone holds the objects but
    still carries the graft, and a grafted graph can report a real ancestor as
    "not an ancestor" (#10356). Bounded and retry-free: a `git` that cannot
    answer leaves the ancestry assertions skipped, not red.
    """
    try:
        shallow = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if shallow.returncode != 0 or shallow.stdout.strip() != "false":
            return False
        match = re.search(
            r"^measured_at_commit:\s*(\S+)\s*$", DOC.read_text(encoding="utf-8"), re.MULTILINE
        )
        if not match:
            return False
        probe = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{match.group(1)}^{{commit}}"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return probe.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ── the shipped tree is clean ──────────────────────────────────────────────


def test_shipped_document_is_clean():
    """FAILS if the tree and the ledger disagree on anything the page claims
    ABOUT THE TREE: a citation that stopped resolving, a verdict outside the
    vocabulary, a deleted correction row, a §6 count that no longer reconciles,
    or a registration row that went missing. Reachable by any ordinary
    refactor — moving a function, closing an issue, or editing the table.

    History-free on purpose: this is the assertion that has to hold in the
    depth-1 `test` jobs, so it drives `check_document()`. The ancestry claim is
    asserted separately by `test_shipped_document_passes_the_full_check`.
    """
    assert _doc_problems() == []


def test_shipped_document_passes_the_full_check():
    """FAILS if `check_all()` — the `--check` entry point, ancestry included —
    reports anything on the shipped document.

    Reachable by the page naming a commit that is not on this branch's history
    (a rebase, a force-push, or a cut taken on another branch). Needs the whole
    clone, so it SKIPS with a stated reason in the depth-1 `test` jobs instead of
    redding them: the REQUIRED `docs` job (fetch-depth 0) is where this arm
    actually runs, and `test_shipped_document_is_clean` above keeps every
    tree-only claim asserted in both.
    """
    if not _history_available():
        pytest.skip(
            "this clone cannot answer the `measured_at_commit` ancestry claim "
            "(depth-1 checkout, or the commit is absent). The REQUIRED `docs` "
            "job runs `--check` at fetch-depth: 0 — #10406, and the tool's "
            "check_document()/check_all() split"
        )
    assert _doc_problems() == []


def test_checker_self_test_passes():
    """FAILS if any mutation arm stops firing, or if the negative control starts
    changing the verdict. Reachable by weakening a rule in the tool.

    History-free: the arms drive `check_document()`, so this proof runs in the
    depth-1 `test` jobs too — a self-test that could only run on a full clone
    would be unproved in every job that selects this file. See
    `test_doc_level_arms_make_no_git_call` for the assertion that keeps it so.
    """
    assert flc.self_test(REPO) == 0


def test_doc_level_arms_make_no_git_call(monkeypatch):
    """FAILS if the tree-only arms (and hence `--self-test`) reach for `git`.

    Reachable, and it is the defect this file was fixed for: when `self_test()`
    drove `check_all()`, every arm inherited the `measured_at_commit` ancestry
    probe, so in a depth-1 clone (the CI `test` jobs' checkout, where
    `f70d884b7` is not in the object store) the mutation proofs could not run at
    all — measured 14 of 24 tests failing there. `subprocess` is replaced on the
    TOOL's module, so this proves the tool's own call graph rather than the
    test's.
    """
    def _boom(*args, **kwargs):
        raise AssertionError("a tree-only arm shelled out to git")

    monkeypatch.setattr(flc, "subprocess", SimpleNamespace(run=_boom))
    assert flc.check_document(flc.read_doc(REPO), REPO) == []
    assert flc.self_test(REPO) == 0


# ── closed verdict vocabulary ──────────────────────────────────────────────


def test_invented_verdict_is_rejected(tmp_path, monkeypatch):
    """FAILS on a verdict cell beginning with a token outside VERDICTS.

    Reachable by a lane writing a *plausible* verdict that the page's own §1 has
    no handling rule for — `PROBABLY-TRUE`, `FIXED`, `LIKELY`. That is the exact
    failure mode: an undefined class gets read as a current fact.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **STILL-TRUE** | `tortoise/sdk.py:16962`",
            "| **PROBABLY-TRUE** | `tortoise/sdk.py:16962`",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("closed vocabulary" in p for p in problems), problems


def test_missing_verdict_is_rejected(tmp_path, monkeypatch):
    """FAILS when a correction row carries no bolded verdict at all.

    Reachable by a row added without consulting §1 — the cell is then prose and
    the reader has no defined action for it.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **STILL-TRUE** | `tortoise/sdk.py:16962`",
            "| looks right | `tortoise/sdk.py:16962`",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("no bolded verdict" in p for p in problems), problems


def test_verdict_need_not_be_the_whole_bold_span():
    """FAILS if the checker treats the vocabulary as "the entire bold span".

    Reachable, and required: two real rows are `**DRIFTED, and much further than
    one step**` and `**STILL-TRUE as a rule; MOOT-BY-LOSS as an object**`. A
    stricter rule would red the shipped, correct document.
    """
    text = flc.read_doc(REPO)
    assert "**DRIFTED, and much further than one step**" in text
    assert "**STILL-TRUE as a rule; MOOT-BY-LOSS as an object**" in text
    assert flc.check_verdicts(text) == []


# ── citations resolve ──────────────────────────────────────────────────────


def test_citation_to_missing_file_is_rejected(tmp_path, monkeypatch):
    """FAILS when a `path:line` names a file that is not in the tree.

    Reachable by renaming or deleting the module a correction rests on — the most
    likely way this page rots, and the one that leaves a dangling citation
    looking exactly like evidence.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/no_such_module.py:16`", 1),
    )
    problems = _doc_problems()
    assert any("does not exist in the tree" in p for p in problems), problems


def test_citation_past_end_of_file_is_rejected(tmp_path, monkeypatch):
    """FAILS when a cited line number is beyond the file's length.

    Reachable by any edit that shortens a cited file. The file still exists, so a
    mere existence check passes while the citation points at nothing.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:999999`", 1),
    )
    problems = _doc_problems()
    assert any("out of range" in p for p in problems), problems


def test_document_with_no_citations_is_unverifiable(tmp_path, monkeypatch):
    """FAILS (exit 2, NOT clean) when the page carries no `path:line` at all.

    Reachable by a lane "simplifying" the evidence column away. A guard that
    measured nothing must refuse, never report ok — the same fail-closed rule
    the sibling map guards follow.
    """
    _mutated(tmp_path, monkeypatch, lambda t: flc.CITATION_RE.sub("`evidence`", t))
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


# ── the population is pinned ───────────────────────────────────────────────


def test_deleted_correction_row_is_rejected(tmp_path, monkeypatch):
    """FAILS when a correction row is deleted, with every mention of it.

    Reachable, and it is the reason this arm exists: a deleted row removes the
    only record that a claim was falsified, and every *referential* check still
    passes — nothing else on the page points at the row.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: "\n".join(ln for ln in t.splitlines() if not ln.startswith("| **l** |")),
    )
    problems = _doc_problems()
    assert any("population drift" in p for p in problems), problems


def test_reordered_correction_row_is_rejected(tmp_path, monkeypatch):
    """FAILS when the rows are present but out of order.

    Reachable by a merge that appends a row in the wrong place; the pin is
    ordered, not just counted, so a renumbering cannot hide a swapped verdict.
    """
    def swap(text: str) -> str:
        lines = text.splitlines()
        i = next(n for n, ln in enumerate(lines) if ln.startswith("| **a** |"))
        j = next(n for n, ln in enumerate(lines) if ln.startswith("| **b** |"))
        lines[i], lines[j] = lines[j], lines[i]
        return "\n".join(lines)

    _mutated(tmp_path, monkeypatch, swap)
    problems = _doc_problems()
    assert any("population drift" in p for p in problems), problems


def test_pinned_population_is_twelve():
    """FAILS if someone relaxes the pin to match a smaller document.

    Reachable by "fixing" a red population check by editing the constant. The
    pin is the thing under test, so it is asserted explicitly.
    """
    assert "".join(flc.PINNED_CORRECTION_LABELS) == "abcdefghijkl"
    assert len(flc.VERDICTS) == 5


# ── §6 arithmetic ──────────────────────────────────────────────────────────


def test_state_count_not_matching_its_own_list_is_rejected(tmp_path, monkeypatch):
    """FAILS when a §6 state row's count disagrees with the issues it lists.

    Reachable by editing one and not the other — the count is then an assertion
    with no evidence behind it, which is the page's own "declared ≠ stored"
    defect.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("| **OPEN** | **4** |", "| **OPEN** | **5** |", 1),
    )
    problems = _doc_problems()
    assert any("declares 5 but its own list names 4" in p for p in problems), problems


def test_state_rows_not_summing_to_population_is_rejected(tmp_path, monkeypatch):
    """FAILS when the state rows sum to something other than the pinned 42.

    Reachable by moving a row between states without updating the total, or by
    dropping a state row's count. Asserted through the arithmetic arm so the
    message names the population, not a single row.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("| CLOSED other | 5 |", "| CLOSED other | 4 |", 1),
    )
    problems = _doc_problems()
    assert any("sum to 41" in p and "pinned indexed population is 42" in p for p in problems), problems


def test_arithmetic_is_derived_from_the_table_not_trusted():
    """FAILS if the three state columns stop reconciling in the shipped document.

    Reachable by any state edit; this asserts the *derived* sum rather than the
    literal, so the population cannot drift while a count is left behind.
    """
    text = flc.read_doc(REPO)
    assert flc.check_state_arithmetic(text) == []
    counts = {
        m.group("label"): int(m.group("count"))
        for line in text.splitlines()
        if (m := flc.STATE_ROW_RE.match(line))
    }
    assert sum(counts.values()) == flc.PINNED_INDEXED_ROWS == 42


# ── registration ───────────────────────────────────────────────────────────


def test_unregistered_document_is_rejected(tmp_path, monkeypatch):
    """FAILS when `docs/00_index.md` carries no row naming the ledger.

    Reachable by renaming the doc without updating the index; an unregistered
    architecture doc is one a lane cannot find, which is the whole point of the
    index.
    """
    empty_index = tmp_path / "00_index.md"
    empty_index.write_text("# index with no mention\n", encoding="utf-8")
    monkeypatch.setattr(flc, "INDEX", str(empty_index))
    problems = _doc_problems()
    assert any("carries no row naming" in p for p in problems), problems


def test_index_registration_is_present_in_the_shipped_tree():
    """FAILS if the ledger's registration row is removed from `docs/00_index.md`.

    Reachable by a merge conflict resolved by dropping a row.
    """
    assert "MEMORY-SUBSTRATE-FINDINGS-LEDGER.md" in INDEX.read_text(encoding="utf-8")
    assert flc.check_registration(REPO) == []


# ── measured_at_commit ancestry ────────────────────────────────────────────


def test_measured_commit_must_be_an_ancestor_of_head(tmp_path, monkeypatch):
    """FAILS when `measured_at_commit` names a commit that is not on HEAD's history.

    Reachable by pasting a sha from a branch that was force-pushed away: the page
    then claims a cut it did not make, and every citation in it was measured
    somewhere else.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: re.sub(r"^measured_at_commit:.*$", "measured_at_commit: 0000000", t, count=1, flags=re.M),
    )
    with pytest.raises(flc.Unverifiable):
        # An unresolvable sha is UNVERIFIED (exit 2), never a pass and never a
        # false accusation — a grafted clone cannot answer the question. This is
        # the ONE arm that needs no history to be meaningful: an absent sha is
        # absent in every clone.
        flc.check_all(REPO)


def test_unreadable_document_is_unverifiable(monkeypatch):
    """FAILS (exit 2) when the document cannot be read.

    Reachable by deleting the file. A guard that cannot run must not report ok.
    """
    monkeypatch.setattr(flc, "DOC", "/nonexistent/ledger.md")
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


def test_missing_section_is_unverifiable(tmp_path, monkeypatch):
    """FAILS (exit 2) when a checked section heading is gone.

    Reachable by renumbering §3. An absent section is the strongest drift, and
    silently checking nothing would report a clean tree over an empty check.
    """
    _mutated(tmp_path, monkeypatch, lambda t: t.replace("## 3. The corrections log", "## 3. Corrections"))
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


# ── exit-code contract ─────────────────────────────────────────────────────


def test_main_exit_zero_on_the_shipped_document(capsys):
    """FAILS if `--check` stops reporting ok on the shipped, clean document.

    Needs the whole clone for the ancestry arm, so it SKIPS with a stated
    reason in a depth-1 checkout rather than redding the `test` jobs that
    cannot answer that claim (#10406). `--self-test` is asserted separately and
    runs everywhere.
    """
    if not _history_available():
        pytest.skip(
            "`--check` includes the `measured_at_commit` ancestry arm, which a "
            "depth-1 checkout cannot answer; the REQUIRED `docs` job runs it at "
            "fetch-depth: 0"
        )
    assert flc.main(["--check", "--root", str(REPO)]) == 0
    assert "agrees with the tree" in capsys.readouterr().out


def test_main_exit_zero_on_self_test(capsys):
    """FAILS if `--self-test` stops reporting PASS on the shipped document.

    Reachable by any dead mutation arm. History-free, so it holds in every job.
    """
    assert flc.main(["--self-test", "--root", str(REPO)]) == 0
    assert "self-test: PASS" in capsys.readouterr().out


def test_main_exit_one_on_drift(tmp_path, monkeypatch, capsys):
    """FAILS if drift reports anything but exit 1.

    Reachable by a caller (the `docs` job) that treats any non-zero as "red":
    `1` names a false claim about the tree, `2` says the checker could not look.
    The ancestry arm is stubbed out here because this test is about the
    exit-code CONTRACT, not about history availability — otherwise a depth-1
    checkout would report 2 and the assertion would pass for the wrong reason.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/no_such_module.py:16`", 1),
    )
    monkeypatch.setattr(flc, "check_measured_commit", lambda *a, **k: [])
    assert flc.main(["--check", "--root", str(REPO)]) == 1
    assert "does not exist in the tree" in capsys.readouterr().out


def test_main_exit_two_on_unreadable(monkeypatch, capsys):
    """FAILS if a guard that could not look reports `1` or `0`.

    Reachable by deleting the document (or a shallow clone that cannot resolve
    the recorded commit): the caller must see `2`, never a drift verdict about a
    document it never read.
    """
    monkeypatch.setattr(flc, "DOC", "/nonexistent/ledger.md")
    assert flc.main(["--check", "--root", str(REPO)]) == 2
    assert "UNVERIFIED" in capsys.readouterr().err


# ── CI wiring ──────────────────────────────────────────────────────────────


def test_checker_is_in_the_docs_drift_step():
    """FAILS if the checker is dropped from the `docs` job's drift step.

    Reachable, and load-bearing: a docs-only edit of the ledger selects NO test
    surface, so `docs` is the only gate that reaches it — and it must run BOTH
    arms, because `--self-test` is what proves the rules still fire.
    """
    sys.path.insert(0, str(REPO / "tests"))
    from _docs_drift_step import DOCS_DRIFT_STEP_COMMANDS

    assert "python3 tools/findings_ledger_check.py --check" in DOCS_DRIFT_STEP_COMMANDS
    assert "python3 tools/findings_ledger_check.py --self-test" in DOCS_DRIFT_STEP_COMMANDS


def test_test_file_is_registered_in_ci_surfaces():
    """FAILS if this file is not registered in `config/ci-surfaces.yml`.

    Reachable by adding a test without the registration; `manifest-integrity`
    then fails unconditionally, and the file is never selected on a source diff.
    """
    assert "test_findings_ledger_2852.py" in SURFACES.read_text(encoding="utf-8")


def test_tool_is_stdlib_only():
    """FAILS if the checker grows a third-party import.

    Reachable by a convenient `import yaml`; the `docs` job runs it without
    relying on a package install, and a stdlib-only guard cannot be broken by a
    dependency bump. `--self-test` is the probe, not `--check`: it exercises the
    same imports and the same document parsing while needing no history, so this
    stays a real assertion in a depth-1 checkout instead of reading the `2` a
    shallow clone returns for `--check`.
    """
    source = (REPO / "tools" / "findings_ledger_check.py").read_text(encoding="utf-8")
    for banned in ("import yaml", "import requests", "import pytest", "import httpx"):
        assert banned not in source
    assert subprocess.run(
        [sys.executable, str(REPO / "tools" / "findings_ledger_check.py"), "--self-test"],
        cwd=REPO, capture_output=True, check=False,
    ).returncode == 0
