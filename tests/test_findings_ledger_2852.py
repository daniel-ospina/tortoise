"""Hermetic tests for `tools/findings_ledger_check.py` (#2852).

The findings ledger (`docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md`) is
the durable half of #2852 — the corrections log of the 2026-09-09/10
memory-substrate review, re-measured. Re-measuring it found **seven of its twelve
rows no longer read as written** (four of them overtaken by the fixes they
motivated), which is precisely
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

Registered in `config/ci-surfaces.yml` — `core` is the classification
`manifest-integrity` requires, and `tier1` is the row that ACTUALLY selects this
module on a docs-only or `tools/`-only diff (both prefixes are dropped by
`_keep_changed()`, so `select()` takes its docs-only early return, which emits
`tier1` only). Both rows are asserted by
`test_test_file_is_registered_in_ci_surfaces` /
`test_test_file_is_selected_on_the_diffs_it_guards`.
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
    clone, so it SKIPS with a stated reason in the depth-1 `test` jobs.

    ⛔ Do not read that skip as "covered elsewhere by a test": the REQUIRED `docs`
    job runs NO pytest, so this assertion is inert there and skipped in every job
    that runs pytest. What the docs job DOES prove is the same claim through the
    tool itself (`--check` IS `check_all`), and the not-ancestor BRANCH is pinned
    in every pytest job by
    `test_measured_commit_not_ancestor_of_head_is_rejected`, which stubs `git`
    and therefore needs no history. This test is the real-document half of that
    pair; calling `check_document()` here (the pre-fix shape) asserted nothing
    the ancestry skip was about.
    """
    if not _history_available():
        pytest.skip(
            "this clone cannot answer the `measured_at_commit` ancestry claim "
            "(depth-1 checkout, or the commit is absent). The REQUIRED `docs` "
            "job runs `--check` at fetch-depth: 0 — #10406, and the tool's "
            "check_document()/check_all() split"
        )
    assert flc.check_all(REPO) == []


def test_mutation_population_is_pinned():
    """FAILS if the self-test arm population is shrunk, or the floor with it.

    Reachable by the shape a weakening edit takes: `MUTATIONS = ()` is a
    ONE-place edit, and it used to print `self-test: PASS (0 failing arm(s))` and
    exit 0 — keeping the REQUIRED `docs` job and
    `test_checker_self_test_passes` green while proving nothing. The floor is
    pinned here as well as in the tool so neither can be lowered alone.

    #2852's P3 added the two survivor-sentence arms (a `three` over two survivor
    bullets; a total that contradicts the OPEN row) — the exact defect the page
    shipped green — and the review of #11902 added four more (an unclassifiable §3
    row, an emptied evidence column, an unclassifiable §6 row, a citation shape the
    resolver declines), plus the DECOY arm, which is the only pin on
    `check_survivor_arithmetic`'s `len(matches) > 1` branch: deleting that branch left
    every other arm, every test and `--check` green (measured).

    The population is 18 after the #11902 review cycles: the §6 survivor-sentence arms,
    the four fail-open arms (unclassifiable §3 row, emptied evidence column,
    unclassifiable §6 row, declined citation shape), the §3 tally arm, the three
    citation-shape arms (space in the path, trailing space, trailing space on a
    shorthand) and the verdict-attribution arm. The floor is pinned WITH it — a
    floor below the population is the "shrunk population proves nothing" shape it
    exists to catch. Re-derive by counting `caught` lines in `--self-test`.
    """
    assert len(flc.MUTATIONS) == 18
    assert flc.MIN_SELF_TEST_MUTATIONS == 18


def test_self_test_floor_catches_a_shrunk_population(monkeypatch):
    """FAILS if an emptied mutation population still reports PASS (exit 0).

    Reachable by the `MUTATIONS = ()` edit above; this drives the real
    `self_test()` with it doctored, so the assertion is on the behaviour rather
    than on the constant. Pre-fix this returned 0 and printed PASS.
    """
    monkeypatch.setattr(flc, "MUTATIONS", ())
    assert flc.self_test(REPO) == 1


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
    all — measured 14 failing tests in a depth-1 clone. `subprocess` is replaced on the
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


# ── an unrecognised row shape is refused, not skipped ──────────────────────
#
# Both tables are pinned by shape, so a row shaped differently from the pin used
# to be INVISIBLE: not rejected, not counted — simply not seen. Measured before
# the fix: appending a thirteenth correction row, or a fifth §6 bucket, left the
# guard reporting CLEAN with the population pin still reading `abcdefghijkl`.
# These arms are what make the pin a pin.


def test_a_thirteenth_correction_row_cannot_hide_behind_label_shape(tmp_path, monkeypatch):
    """FAILS if a row whose label cell is not `**<lowercase letter>**` is skipped.

    Reachable by the ordinary act of appending a correction the way a lane writing
    prose would (`**M**`, `**m** (new)`, `13`), or by NOT giving one a label at
    all. Any of those adds a claim to the log that §8's "pinned to twelve rows"
    does not cover.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **l** |",
            "| **M** | An undeclared claim | **False** | **FIXED-IT** | `tortoise/pipeline.py:16` |\n| **l** |",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("is not `**<lowercase letter>**`" in p for p in problems), problems


def test_a_fifth_state_bucket_cannot_hide_behind_label_shape(tmp_path, monkeypatch):
    """FAILS if a §6 row the arithmetic cannot name is silently dropped.

    Reachable by any re-bucketing that invents a bucket (`CLOSED NOT_PLANNED`,
    `CLOSED stale`) — the row is then neither counted nor flagged, so the four
    pinned buckets still sum to the pinned 42 while a real count has moved.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| MERGED (both were PRs) |",
            "| CLOSED NOT_PLANNED | 0 | |\n| MERGED (both were PRs) |",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("state label" in p and "is not one of" in p for p in problems), problems


# ── an evidence cell must name something ───────────────────────────────────


def test_an_evidence_cell_naming_nothing_is_rejected(tmp_path, monkeypatch):
    """FAILS if a §3 evidence cell can name no citation, issue, command or §N.

    Reachable by a lane rewording a row and leaving the evidence column as prose.
    §1 states the rule in the page's own words; this is that rule, enforced.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **STILL-TRUE** | `tortoise/sdk.py:16962` —",
            "| **STILL-TRUE** | checked, trust me —",
            1,
        ).replace("`tortoise/sdk.py:17361`", "the call", 1),
    )
    problems = _doc_problems()
    assert any("names no `path:line`, issue, command or §N" in p for p in problems), problems


def test_emptying_the_whole_evidence_column_is_rejected(tmp_path, monkeypatch):
    """FAILS if the evidence column can be deleted into greenness.

    This is the bypass that matters: a real drift reds the REQUIRED `docs` job,
    and deleting the citation turns the red green while the verdict still asserts
    a fix. Measured before the fix: every evidence cell blanked -> CLEAN, exit 0.
    """
    def blank_evidence(text: str) -> str:
        return flc._empty_evidence_cells(text)

    _mutated(tmp_path, monkeypatch, blank_evidence)
    problems = _doc_problems()
    offenders = [p for p in problems if "names no `path:line`, issue, command or §N" in p]
    assert len(offenders) == 12, problems


# ── no citation shape is exempt ────────────────────────────────────────────


def test_a_citation_outside_the_path_character_class_is_refused(tmp_path, monkeypatch):
    """FAILS if a backticked `token:LINE` the pattern declines is skipped.

    Reachable by writing a citation the old extension allow-list or character
    class did not match — a `.txt` path, an extension-less path, or a leading-dot
    path. Skipping it is scope the guard silently did not measure while §8 said
    it did.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "`tests/eval/write_path/test_write_path_relation_lane.py:9`",
            "`tests/eval/write_path/test_write_path_relation_lane.py%20:9`",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("not a recognised citation" in p for p in problems), problems


def test_a_leading_dot_citation_is_measured_not_skipped(tmp_path, monkeypatch):
    """FAILS if a `.github/...`-style citation is exempt from resolution.

    Reachable by citing a workflow line, which is exactly what a page about CI
    gates does. Before the fix such a path matched NOTHING, so an out-of-range one
    passed; here the SAME mutation must now be reported as out of range, which is
    what "measured" means.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`.github/workflows/ci.yml:999999`", 1
        ),
    )
    problems = _doc_problems()
    assert any("out of range" in p for p in problems), problems


def test_a_non_python_extension_citation_is_measured_not_skipped(tmp_path, monkeypatch):
    """FAILS if the old `.py|.md|.yml|…` allow-list still gates resolution.

    Reachable by citing a `.txt`, `.toml` or `.cfg` path. Measured before the fix:
    substituting one for a resolving citation left the guard CLEAN, i.e. a red
    could be defused by reformatting the citation it pointed at.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`notes/thing.txt:16`", 1),
    )
    problems = _doc_problems()
    assert any("does not exist in the tree" in p for p in problems), problems


def test_a_citation_with_a_space_in_its_path_is_refused(tmp_path, monkeypatch):
    """FAILS if one inserted space makes a citation invisible instead of refused.

    This is the one-keystroke bypass: with a whitespace-excluding token class the
    mangled token matched neither the resolver nor the refusal, so `--check`
    exited 0 over a citation nobody was checking — including §2's own, because the
    scan had been scoped to §3/§5.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/pipeline .py:16`", 1),
    )
    problems = _doc_problems()
    assert any("not a recognised citation" in p for p in problems), problems


def test_a_citation_with_a_space_after_the_colon_is_refused(tmp_path, monkeypatch):
    """FAILS if `path: 16962` is silently skipped — the second space shape."""
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/sdk.py:16962`", "`tortoise/sdk.py: 16962`", 1),
    )
    problems = _doc_problems()
    assert any("not a recognised citation" in p for p in problems), problems


def test_a_yaml_key_value_is_not_a_citation():
    """FAILS if the whole-page scan treats `key: value` as a citation.

    A false red here would red the REQUIRED `docs` job on correct prose — the
    shipped page contains `fetch-depth: 0` inside a code span. The population is
    "has a path marker or the `:LINE` shorthand", not "contains a colon".
    """
    text = flc.read_doc(REPO)
    assert "`fetch-depth: 0`" in text
    assert flc.check_citation_shapes(text) == []


def test_a_host_and_port_in_prose_is_not_resolved_as_a_path(tmp_path, monkeypatch):
    """FAILS if a `word:digits` token with no path marker is resolved as a file.

    Measured before the fix: a `localhost:6379` example reported "localhost does not
    exist in the tree" and red the required, path-filter-free `docs` job — the
    fail-closed friction that teaches a lane to route around a guard.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`localhost:6379`", 1),
    )
    problems = _doc_problems()
    assert not any("citation" in p for p in problems), problems


def test_a_citation_with_a_trailing_character_is_refused(tmp_path, monkeypatch):
    """FAILS if a trailing space or full stop makes a citation invisible.

    The one-keystroke bypass again, one character further out: a trailing space on
    row (e)'s anchor, on the self-test's own `:792` target, and a period after an
    out-of-range number all matched neither the resolver nor the refusal, so
    `--check` exited 0 while a whole row's evidence became unverified (its `:27` /
    `:66` shorthands then re-anchored to the previous row's path and resolved).
    CommonMark strips a space on both sides of a code span, so the mangled form
    does not even look mangled to a reader.
    """
    for old, new in (
        ("`tortoise/sdk.py:36042-36046`", "`tortoise/sdk.py:36042-36046 `"),
        ("`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:16 `"),
    ):
        _mutated(tmp_path, monkeypatch, lambda t, o=old, n=new: t.replace(o, n, 1))
        problems = _doc_problems()
        assert any("not a recognised citation" in p for p in problems), (old, problems)


def test_a_shorthand_with_a_trailing_character_is_refused(tmp_path, monkeypatch):
    """FAILS if a trailing space or period after a `:LINE` shorthand is skipped."""
    for old, new in (("`:792`", "`:792 `"), ("`:3115`", "`:999999.`")):
        _mutated(tmp_path, monkeypatch, lambda t, o=old, n=new: t.replace(o, n, 1))
        problems = _doc_problems()
        assert any("not a recognised citation" in p for p in problems), (old, problems)


# ── the headline tally is derived, not asserted ────────────────────────────


def test_the_shipped_tally_matches_the_verdict_cells():
    """FAILS if the page's "N of twelve" sentence stops agreeing with the table."""
    text = flc.read_doc(REPO)
    assert flc.check_verdict_tally(text) == []
    verdicts = flc._verdicts_by_label(text)
    non_still = [lbl for lbl, v in verdicts.items() if v != "STILL-TRUE"]
    assert len(non_still) == 7, verdicts
    overtaken = [lbl for lbl, v in verdicts.items() if v in ("SUPERSEDED-BY-FIX", "ADOPTED")]
    assert len(overtaken) == 4, verdicts


def test_a_flipped_verdict_that_leaves_the_tally_stale_is_rejected(tmp_path, monkeypatch):
    """FAILS if a verdict cell can be flipped without the tally sentence reddening.

    Reachable by any ordinary re-measure that upgrades one row: the prose keeps its
    old count and letter list, and every reader who quotes the summary (including
    `ci.yml`, `config/ci-surfaces.yml` and `docs/00_index.md`, which repeat it) is
    told a number the table beside it contradicts. Measured before the fix: rc 0.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **DRIFTED** — `Entity` 3", "| **STILL-TRUE** — `Entity` 3", 1
        ),
    )
    problems = _doc_problems()
    assert any("the paragraph does not say" in p for p in problems), problems
    assert any("'six of the twelve" in p or "six of the twelve" in p for p in problems), problems


def test_a_tally_that_names_the_wrong_letters_is_rejected(tmp_path, monkeypatch):
    """FAILS if the letter list can drift from the cells it summarises."""
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("**(l)** are **DRIFTED**", "are **DRIFTED**", 1),
    )
    problems = _doc_problems()
    assert any("letter list that disagrees" in p for p in problems), problems


def test_a_verdict_moved_between_classes_is_rejected(tmp_path, monkeypatch):
    """FAILS if two verdict classes can be swapped without the sentence reddening.

    Comparing the letters as a SET and the overtaken rows as a COUNT let a cell move
    between classes with the quoted sentence unchanged — measured: (c)
    SUPERSEDED-BY-FIX -> ADOPTED, (e) ADOPTED -> SUPERSEDED-BY-FIX and (k) DRIFTED ->
    MOOT-BY-LOSS all left rc 0. §1 gives those classes different handling rules ("do
    not re-derive it" vs "treat the new behaviour as the norm" vs "do not assert it
    either way"), so a swapped class sends the reader to the wrong action.
    """
    cases = (
        ("| **SUPERSEDED-BY-FIX** — the Point fold now persists",
         "| **ADOPTED** — the Point fold now persists"),
        ("| **ADOPTED** — both axes are shipped",
         "| **SUPERSEDED-BY-FIX** — both axes are shipped"),
        ("| **DRIFTED** — the count is now a closed-book",
         "| **MOOT-BY-LOSS** — the count is now a closed-book"),
    )
    for old, new in cases:
        _mutated(tmp_path, monkeypatch, lambda t, o=old, n=new: t.replace(o, n, 1))
        problems = _doc_problems()
        assert any("assigns a different verdict" in p for p in problems), (old, problems)


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


def test_shipped_shorthand_citations_are_matched_not_skipped():
    """FAILS if a bare `:LINE` shorthand stops being matched at all.

    Reachable, and it is the pre-fix shape: `CITATION_RE` required `<path>.<ext>`
    before `:LINE`, so row (b)'s `:792` / `:1002` / `:429-438` (its only
    commitments about `tortoise/commit_schema.py` beyond `:395`), row (c)'s
    `:3115` and row (e)'s `:66` were skipped entirely while §8 told the reader
    every `path:line` in §3/§5 is checked. Skipped evidence presents as checked
    evidence, which is the defect this page is about.
    """
    matched = {m.group(0) for m in flc.CITATION_RE.finditer(flc.read_doc(REPO))}
    for shorthand in ("`:792`", "`:1002`", "`:429-438`", "`:3115`", "`:66`"):
        assert shorthand in matched, f"{shorthand} is not matched, so it is not checked"


def test_shorthand_citation_out_of_range_is_rejected(tmp_path, monkeypatch):
    """FAILS when a shorthand `:LINE` points past the end of its ANCHOR's file.

    Reachable by any edit that shifts the cited classes down the file (inserting
    lines above them), or by a shorthand copied from a different file. It asserts
    the anchor too: the message must name `tortoise/commit_schema.py` (the cell's
    own preceding full-path citation), not whatever file was cited last.
    """
    _mutated(tmp_path, monkeypatch, lambda t: t.replace("`:792`", "`:999999`", 1))
    problems = _doc_problems()
    assert any(
        "out of range" in p and "tortoise/commit_schema.py" in p for p in problems
    ), problems


def test_shorthand_without_a_preceding_full_path_is_rejected():
    """FAILS if a shorthand is silently accepted when nothing anchors it.

    Reachable by writing `:66` as the first citation in the page: the guard can
    then only guess, and a guess is how a citation that points at nothing passes
    as evidence. It must refuse to resolve instead.
    """
    problems = flc.check_citations("a claim with only a shorthand `:66` in it", REPO)
    assert any("shorthand with no preceding" in p for p in problems), problems


def test_escaped_pipe_inside_a_cell_does_not_shift_the_verdict_column(tmp_path, monkeypatch):
    """FAILS (as a false red) if a legal `\\|` inside a §3 cell breaks the row split.

    Reachable by any row whose claim legitimately contains a literal pipe. A
    plain `line.split("|")` splits there too, shifts the verdict column and
    reports a correct row as "no bolded verdict" — a false red the reader cannot
    act on, which is how a guard gets routed around. Asserted on that arm only:
    the mutated copy lives outside the tree, so `check_registration` is expected
    to complain about it either way.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "| **False.** Extraction writes through the SDK doors",
            "| **False.** a \\| b Extraction writes through the SDK doors",
            1,
        ),
    )
    problems = _doc_problems()
    assert not any("no bolded verdict" in p for p in problems), problems


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
        lambda t: t.replace("| CLOSED other | 4 |", "| CLOSED other | 3 |", 1),
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


def test_survivor_sentence_is_derived_from_its_own_bullets():
    """FAILS if §6's survivor sentence stops reconciling with the table and bullets.

    The sentence's two numbers are DERIVED: `four` is the OPEN row's own count and
    `two` is the number of bullets naming an OPEN issue (the third bullet names
    `#2730`, which the same table puts in `CLOSED COMPLETED`; it is printed
    because *issue closure is not evidence of a fix*, not because it survives).

    Reachable — and reached, in the shipped page: the sentence read `three` over
    two survivor bullets plus one CLOSED issue while §7 said `two`. Two reviewers
    then disagreed about which side was stale, and the fix one of them proposed
    would have swapped a true `two` for a false `three`. This asserts the derived
    values, so a future `correction` in either direction reds here.
    """
    text = flc.read_doc(REPO)
    # ⛔ BEFORE the `check_survivor_arithmetic(text) == []` assert, deliberately: that assert also
    # reds on a two-sentence §6, so it would SHADOW this one and this pin would never be the row
    # that reports (#11902 review, P3). Ordered first, it is the assertion that fails when §6 grows
    # a second matching sentence — and it names the reason. It is still not the pin for the CODE
    # branch; the `a second survivor sentence that could take the match` arm is.
    section = flc._region(text, "## 6. The issue index", "## 7. What this changes")
    assert len(flc.SURVIVOR_RE.findall(section)) == 1, (
        "§6 must carry exactly one sentence matching SURVIVOR_RE — a second one takes the match "
        "and the guard cannot tell which sentence a lane reads"
    )
    assert flc.check_survivor_arithmetic(text) == []
    open_row = next(
        ln for ln in section.splitlines()
        if flc.STATE_ROW_RE.match(ln) and flc.STATE_ROW_RE.match(ln).group("label") == "OPEN"
    )
    open_ids = set(flc.ISSUE_ID_RE.findall(flc.STATE_ROW_RE.match(open_row).group("which")))
    match = flc.SURVIVOR_RE.search(section)
    assert match is not None, "the survivor sentence is gone — the guard's subject is missing"
    assert flc.NUMBER_WORDS[match.group("total")] == len(open_ids), (
        "the sentence's survivor total must be the OPEN row's own count"
    )
    bullets = flc.SURVIVOR_BULLET_RE.findall(section[match.end():])
    # Measured on the shipped page: three bullets, of which exactly two are OPEN.
    assert len(bullets) == 3
    assert len([i for i in bullets if i in open_ids]) == 2
    assert flc.NUMBER_WORDS[match.group("count")] == 2


def test_survivor_count_not_matching_the_bullets_is_rejected(tmp_path, monkeypatch):
    """FAILS when the sentence's count disagrees with the bullets beneath it.

    This re-injects the EXACT shipped defect: `three` over two survivor bullets
    plus one CLOSED issue. It is the reachable case, not a hypothetical — the
    page carried it, and the guard reported OK over it, which is why the wrong
    side looked authoritative enough for a reviewer to propose `two` -> `three`
    on §7 to match it.
    """
    def to_three(t: str) -> str:
        # ⛔ Target the sentence's LEAD-IN, not a phrase from its tail: the previous target quoted
        # `the two bullets below`, so rewording the sentence made this replace a no-op and the arm
        # died silently behind `_mutated`'s own "mutation did not change the document" assert
        # (#11902 review). The lead-in is the same clause `SURVIVOR_RE` keys on.
        return t.replace(
            "survivors, the two survivor bullets below",
            "survivors, the three survivor bullets below",
            1,
        )

    _mutated(tmp_path, monkeypatch, to_three)
    problems = _doc_problems()
    assert any("says 3 of them are already re-measured" in p for p in problems), problems


def test_survivor_total_not_matching_the_open_row_is_rejected(tmp_path, monkeypatch):
    """FAILS when the sentence's survivor TOTAL drifts from the OPEN row.

    Reachable by moving an issue between states without re-reading the sentence
    above the table, which leaves the page telling a lane there are five
    survivors when its own OPEN row names four.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("of the four survivors", "of the five survivors", 1),
    )
    problems = _doc_problems()
    assert any("says there are 5 survivors" in p for p in problems), problems


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


def test_measured_commit_not_ancestor_of_head_is_rejected(monkeypatch):
    """FAILS when `measured_at_commit` resolves but is NOT on HEAD's history.

    Reachable by pasting a sha from a branch that was force-pushed away, or by a
    cut taken on another branch — the page then claims a cut it did not make, and
    every citation in it was measured somewhere else.

    `git` is stubbed rather than producing a real off-history commit: a shallow
    pytest checkout cannot make one, which is exactly why this branch was
    unpinned before (`0000000` dies earlier, at `rev-parse --verify`, and takes
    the UNVERIFIED path instead). The stub records the calls so the assertion is
    that the real `merge-base --is-ancestor` arm ran, not that a helper was
    bypassed.
    """
    class _Result:
        def __init__(self, returncode: int, stdout: str = "") -> None:
            self.returncode, self.stdout, self.stderr = returncode, stdout, ""

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "rev-parse" in cmd:
            return _Result(0, "f70d884b7f51\n")
        return _Result(1)  # merge-base --is-ancestor: exit 1 == NOT an ancestor

    monkeypatch.setattr(flc, "subprocess", SimpleNamespace(run=fake_run))
    problems = flc.check_measured_commit(flc.read_doc(REPO), REPO)
    assert any("NOT an ancestor of HEAD" in p for p in problems), problems
    assert any("--is-ancestor" in cmd for cmd in calls), calls


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
    then fails unconditionally. Classification only — it says nothing about
    whether a DIFF selects the file, which is what the test below pins.
    """
    assert "test_findings_ledger_2852.py" in SURFACES.read_text(encoding="utf-8")


def test_test_file_is_selected_on_the_diffs_it_guards():
    """FAILS if the guard is not SELECTED on the two change classes it guards.

    Reachable, and measured: with only the `core` row,
    `select(["docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md"])` returned
    `surfaces: []` with this module ABSENT (a docs-only diff takes `select()`'s
    early return, which emits `tier1` only), and the same for
    `tools/findings_ledger_check.py` (`tools/` is a flat `NON_PYTHON_PREFIXES`
    entry with no `SOURCE_PATTERNS` match, so `changed == []`). A `core`-only row
    is classification, not selection — the page's own guard ran ZERO times on an
    edit to the page. The `tools/` arm is closed by `CORE_ALSO` in
    `tools/ci_selection.py`, the `docs/` arm by `tier1`.
    """
    sys.path.insert(0, str(REPO))
    from tools.ci_selection import load_manifest, select

    manifest = load_manifest()
    docs = select(
        ["docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md"], "pull_request", manifest
    )
    assert docs["surfaces"] == [], docs["surfaces"]
    assert "test_findings_ledger_2852.py" in docs["test_files"], (
        "a docs-only edit of the ledger must run the guard that re-measures it — "
        "that is the change class that can falsify the page's citations"
    )

    tool = select(["tools/findings_ledger_check.py"], "pull_request", manifest)
    assert tool["surfaces"] == ["core"], tool["surfaces"]
    assert "test_findings_ledger_2852.py" in tool["test_files"], (
        "a tools-only edit must run the guard's own negative controls — without the "
        "CORE_ALSO entry `tools/` falls through to the docs-only early return"
    )


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
