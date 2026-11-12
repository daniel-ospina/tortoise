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
`manifest-integrity` requires — plus TWO selection mechanisms, which do different
jobs and were easy to conflate (an earlier revision of this paragraph did):

- the `tier1` row is what selects this module on a **docs-only** diff. Both
  `docs/` and `tools/` are dropped by `_keep_changed()`, so `select()` takes its
  docs-only early return and emits `tier1` only: measured `surfaces: []`, 89 test
  files, this module present;
- the `CORE_ALSO` entry in `tools/ci_selection.py` is what a **`tools/`-only**
  diff resolves to the full `core` surface instead of that same tier-1 smoke:
  measured `surfaces: ['core']`, 819 test files. It is not what keeps this module
  selected there — the `tier1` row already does, at 89 files (measured with the
  entry removed: `surfaces: []`, 89 files, module present).

Both counts move with `main`, so re-derive them (`select()` in-process, as this
paragraph did) rather than carrying them; the SURFACES above are what the pins
assert. Both the classification row and the selection arms are asserted by
`test_test_file_is_registered_in_ci_surfaces` /
`test_test_file_is_selected_on_the_diffs_it_guards`.
"""
from __future__ import annotations

import ast
import inspect
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
    """FAILS if the self-test arm population drops below the declared floor.

    Reachable by the shape a weakening edit takes: `MUTATIONS = ()` is a
    ONE-place edit, and it used to print `self-test: PASS (0 failing arm(s))` and
    exit 0 — keeping the REQUIRED `docs` job and
    `test_checker_self_test_passes` green while proving nothing. The floor is
    pinned here as well as in the tool so neither can be lowered alone.

    #2852's P3 added the two survivor-sentence arms (a `three` over two survivor
    bullets; a total that contradicts the OPEN row) — the exact defect the page
    shipped green — and the review of #11902 added four more (an unclassifiable §3
    row, an emptied evidence column, an unclassifiable §6 row, a citation shape the
    resolver declines), plus the DECOY arm. (Which arm pins which branch of
    `check_survivor_arithmetic` is recorded in the tool, on the arm's own comment; this
    docstring does not restate it, because a second copy of that mapping is a claim that
    can drift from the code and nothing here tests it.)

    #11902's review added the arm where a marker-less `host:port` prose token used to be
    installed as the shorthand ANCHOR before the marker test ran, so inserting one in
    front of a bad shorthand defused its red (measured green).

    #11952 added the `#L` permalink arm: the GitHub permalink spelling (path, `#`, `L`,
    line number) matched NO pattern in this module, so both an out-of-range and an
    in-range permalink left the page CLEAN — the likeliest real-world citation form was
    also the one shape that looked verified and was not. The population and the floor
    move together.

    The #11902 re-verification added four more (the population was 31 at the head it
    re-measured), one per fail-open it found still open on the merged head: a `~`-rooted
    citation outside `~/.swarm/` (one keystroke defused the resolver), a two-digit `§N`
    that named no heading (read as its first digit), a §1 row that is not a
    `**VERDICT**` row (a claim class with no handling rule, invisible to the vocabulary
    comparison), and a second tally sentence in the same paragraph (a decoy supplying the
    letters while the real one went stale).

    This lane's is the *uncoloned* arm's carve-outs, which were tested against
    `CITATION_UNCOLONED_SHAPE_RE.search()`'s FIRST match only, so one exempt token
    certified every citation beside it in the same code span — an HTTP status beside a
    page name hid an out-of-range line, or a citation of a file that does not exist
    (measured at `6413d5e8`). The `#L` permalink arm one branch above was fixed for
    this same shape in an earlier round; this is the arm that was left behind.

    The third #11902 review round added TWO, both for the same defect one arm over from
    the letter lists: the two FIGURES §3 states in words ("N of the M rows no longer read
    as written", "N of the M were") were compared with `expected in paragraph`, so a
    decoy sentence satisfied the comparison while the sentence a lane quotes went stale —
    measured with a falsified count beside a list-free decoy, and a falsified overtaken
    figure beside a compliant one. A derived figure must live in exactly ONE sentence,
    which is the rule the letter lists already carried.

    ⛔ NO SENTENCE IN THIS PARAGRAPH NAMES AN ARM BY A NUMBER — ordinal OR bare. An arm
    position is a count: it moves whenever an arm is inserted above it, and nothing tests
    it (the terminal count below IS tested, against `len(MUTATIONS)`, which is why it can
    stay). Name the arm by what it does, or read the position off `MUTATIONS`. This rule
    is here because arm numbers were written in this paragraph repeatedly: some shifted
    when an arm was inserted above them, some were written from the population size
    instead of a position so they were wrong on arrival, and the ones that were right had
    nothing holding them true. Name the arm; do not number it.

    Two further arms close two holes the review of that pass found in the fixes
    themselves: a §1 row whose first cell merely BEGINS with `Verdict` (the header skip
    was a prefix test, so the row-shape arm beside it was defeated by a plural) and a
    second `still read as written` sentence (the complement list was joined across
    sentences and compared as a set-union, so the page could assert 7 + 1 letters
    against a table of twelve). The same review also proposed a `~/.swarm/../…` escape
    guard: it is absent here because the reconciliation commit removed the `~` carve-out
    outright, which refuses that spelling by construction and carries its own arm.

    One further arm closes the header slot itself, and it was a RED→GREEN on the way in:
    exempting row 0 by POSITION rather than by the header's SHAPE made the slot
    content-blind — measured on the parent tool, REPLACING the header with an unbolded
    invented claim class left `check_document()` at `[]` where the parent returned a
    problem — so the exemption is the shape `| Verdict | … |`, and a reworded header is a
    deliberate two-place edit rather than a silent one.
    The round-8 review of #11902 added the two group-orientation mutants (a governed
    complement naming a STILL-TRUE row behind an adverb; a marked cross-reference
    certifying a missing complement member), because the arms that review changed had no
    killing mutant and `--self-test` stayed green over both fail-opens.
    49 was the population before the second pass; 62 is the population at this head.
    The second pass of the same review added eight arms for the render-blank, placeholder
    and copy families (an entity, markup, a comment, an empty-label link, `n/a` as the
    JOINED form, a placeholder whose twin WAS listed, and a copy in either case), taking
    the population to 57 — the review of that pass then found the `all(...)` placeholder
    half unpinned, so multi-word placeholders got their own arm, and the verdict and
    evidence columns turned out to read the RAW cell (a commented-out verdict and a
    commented-out citation both passed), which added the last two: 49 + 8 = 57, + 3 = 60,
    and the CYCLE-3 review added one more for the tally reader (a verdict hidden behind a
    commented-out one), taking it to 61 — and the CYCLE-10 review added the raw-text arm
    (`<SCRIPT>lorem ipsum</SCRIPT>`: a body a browser never renders, in a mixed CASE, which
    pins both the raw-text switch and its case-insensitive matching), taking it to 62.
    """
    # ⛔ EQUALITY, not `>=` (#11902 review P2). A floor cannot see a one-arm shrink:
    # at this head two merges of two 25-arm populations produced 26 arms while the
    # floor stayed 25, so deleting an arm left `--self-test` at PASS (rc 0) and every
    # population test green — the anti-shrink guard permitting the exact shrink it
    # exists to refuse. Equality makes the pair drift-proof in both directions.
    assert len(flc.MUTATIONS) == flc.MIN_SELF_TEST_MUTATIONS, (
        "the registered population must EQUAL the floor it declares — a `>=` floor "
        "cannot see a one-arm shrink"
    )
    assert flc.MIN_SELF_TEST_MUTATIONS == 62, (
        "this line RECORDS the population (62 arms); removing one means editing the "
        "floor here too, which is the second place a shrink has to touch"
    )


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


def test_section1_vocabulary_must_match_the_enforced_set(tmp_path, monkeypatch):
    """FAILS when §1 defines a different vocabulary than the guard enforces.

    Reachable by deleting one of §1's five rows: §3 could still use the verdict
    and §8 would still call §1 the authority, while the guard — which compared §3
    against its own constant only — reported clean (measured: removing the
    `ADOPTED` row left `check_document()` empty). The page would then define a
    vocabulary narrower than its own table uses, which is the "claim class with no
    handling rule" §1 exists to prevent.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: "\n".join(
            ln for ln in t.splitlines() if not ln.startswith("| **ADOPTED** |")
        ),
    )
    problems = _doc_problems()
    assert any("§1 vocabulary drift" in p for p in problems), problems


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
            "| **STILL-TRUE** | `tortoise/sdk.py:17109`",
            "| **PROBABLY-TRUE** | `tortoise/sdk.py:17109`",
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
            "| **STILL-TRUE** | `tortoise/sdk.py:17109`",
            "| looks right | `tortoise/sdk.py:17109`",
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
            "| **STILL-TRUE** | `tortoise/sdk.py:17109` —",
            "| **STILL-TRUE** | checked, trust me —",
            1,
        ).replace("`tortoise/sdk.py:17530`", "the call", 1),
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


@pytest.mark.parametrize(
    ("cell", "column", "absence"),
    [(2, "claim", "names no retracted claim"), (3, "correction", "states no correction")],
)
def test_a_blank_prose_cell_is_rejected(tmp_path, monkeypatch, cell, column, absence):
    """FAILS if §3's claim or correction cell can be blanked into greenness.

    The evidence floor alone left a row reducible to
    `| **e** |  |  | **ADOPTED** | <citation> |` — a verdict with neither the claim
    that was retracted nor the correction that replaced it — and the page stayed
    CLEAN (measured on the pre-fix tool: blanking either cell, `check_document()`
    `[]`). The citation was still openable, which is exactly why the evidence floor
    could not see it: the row kept its evidence and lost its SUBJECT.

    The shape is preserved on purpose (`|`.join over the split cells round-trips the
    line), so a reshaped row would be caught by the row-shape arm instead and this
    assert would be satisfied by a neighbour's red.
    """
    original = flc.read_doc(REPO)
    assert flc.check_evidence_cells(original) == []
    blanked = flc._blank_cell_of_first_correction_row(original, cell)
    assert blanked != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_evidence_cells(blanked)
    assert any(absence in p and column in p for p in problems), problems
    # Evidence is untouched, so the evidence-floor assert cannot be what reds here:
    assert not any("names no `path:line`" in p for p in problems), problems


@pytest.mark.parametrize(
    "cell",
    [
        "&nbsp;",
        "<br>",
        "<span>&nbsp;</span>",
        "<!-- removed -->",
        "[](https://x)",
        "![](https://x/y.png)",
        "<!DOCTYPE html>",  # a DECLARATION renders as nothing
        "<![CDATA[x]]>",  # …and so does a CDATA section
        '<span title="`n/a`">',  # a QUOTED attribute may hold a backtick
        '<div title="a > b"></div>',  # …or a `>`, which a loose pattern never matches
        "<!-->",  # HTML5's abrupt-closing EMPTY comment is complete, not unterminated
        "<!--->",
        "<!----x-->",  # a TERMINATED comment whose text begins with `--`
        "<script>lorem ipsum</script>",  # a RAW-TEXT element's body is not rendered at all
        "<style>lorem ipsum</style>",
        "<template>lorem ipsum</template>",
        "<noscript>lorem ipsum</noscript>",
        "<title>lorem ipsum</title>",
        "<iframe>lorem ipsum</iframe>",
        '<script>x</script foo=">**STILL-TRUE**">',  # an ATTRIBUTED close tag is not one
        "<script>x</script/>AFTER",  # …nor is a self-closed one
        "<script>a `</script>` b</script>",  # a close tag inside a CODE SPAN is escaped
        "<SCRIPT>lorem ipsum</SCRIPT>",  # the element name is CASE-INSENSITIVE
        "&#32;",  # a NUMERIC character reference the whitespace family decodes
        "&#9;",
        "&#x20;",
        '<script>x</script foo=">**STILL-TRUE**">',  # an ATTRIBUTED close tag closes nothing
        "<script><!--<script></script>-->lorem</script>",  # DOUBLE-escaped: the inner close is not one
        "<template><template>x</template>VISIBLE",  # `<template>` NESTS: the inner close is not ours
        "<template><template/>VISIBLE</template>VISIBLE</template>",  # HTML ignores a self-closing `/`
        "<template><script></template></script>VISIBLE",  # a nested RAW-TEXT body swallows the close
        "<template><style></template></style>VISIBLE",
        "<template><textarea></template></textarea>VISIBLE",  # RCDATA body, likewise
        "<script>[b](</script>)VISIBLE</script>",  # a RECOGNISED link re-emits its destination
        "<template><!-- </template>VISIBLE -->",  # PARSED but hidden: a comment's close is not one
        "[<br>](https://x)",  # a LINK LABEL renders; the destination does not
        "[<span></span>](https://x)",
        "[![](x)](https://x)",  # an image inside a label
        "[](<https://x>)",  # an autolink destination
    ],
)
@pytest.mark.parametrize("column", [2, 3])
def test_a_blank_rendering_cell_is_rejected(tmp_path, monkeypatch, column, cell):
    """FAILS if a §3 prose cell that RENDERS blank can still pass the floor.

    The whitespace floor behind `check_evidence_cells` asked `value.strip()`, which is a
    different question from "does this cell show a reader anything". Measured at
    `c5990249c0`: `&nbsp;`, `<br>`, `<!-- x -->` and `[](https://x)` in a prose cell each
    left `--check` at rc 0 while the row sat beside them asserting a verdict — the same
    "a verdict with nothing behind it" hole one dialect out from the blank cell the floor
    already refused.
    """
    original = flc.read_doc(REPO)
    assert flc.check_evidence_cells(original) == []
    mutated = flc._rewrite_first_correction_cell(original, column, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_evidence_cells(mutated)
    assert any("carries nothing a reader can read" in p for p in problems), problems
    # Evidence is untouched, so the evidence floor cannot be what reds here.
    assert not any("names no `path:line`" in p for p in problems), problems


@pytest.mark.parametrize(
    "cell", ["n/a", "N/A", "tbd", "TBC", "none", "to do", "x nil", "nil nil", "tbd none"]
)
def test_a_placeholder_cell_is_rejected(tmp_path, monkeypatch, cell):
    """FAILS if a §3 prose cell recording a placeholder counts as content.

    A placeholder is what a lane reaches for when the guard reds a blank cell: it is not
    blank, so `.strip()` passes it, and it records nothing. `n/a` tokenises to `['n', 'a']`
    because `/` separates in the word class, so the JOINED form has to be tested too —
    the most likely placeholder spelling of all is otherwise the one the set cannot see.
    `TBC` is here because `tba` was in the set while its twin was not (measured: `TBC` in
    every §3 correction cell left `--check` at rc 0).
    """
    original = flc.read_doc(REPO)
    assert flc.check_evidence_cells(original) == []
    mutated = flc._rewrite_first_correction_cell(original, 3, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_evidence_cells(mutated)
    assert any("is a placeholder" in p for p in problems), problems


@pytest.mark.parametrize(
    ("claim", "correction"),
    [
        ("`CommitEvent` has 8 fields; `Point` has 15", "`CommitEvent` has 10 fields; `Point` has 18"),
        ("The register holds 50 open decisions", "The register holds 41 open decisions"),
        ("`tortoise/projection/entities.py:2679`", "`tortoise/projection/entities.py:3162`"),
    ],
)
def test_the_copy_test_reads_digits(claim, correction):
    """FAILS if the copy test compares two cells with their NUMBERS DELETED.

    `CONTENT_WORD_RE` is letters-only, so using it for the copy comparison made a correction
    that only fixes a count, a line or a section number reduce to its claim's own word list
    and get refused as "a COPY of its claim" (measured by the review of this change) — the
    dominant correction shape on this page, where rows (b), (k) and (l) all correct a number.
    The copy test therefore reads digits (`_copy_tokens`); the placeholder test, which is
    about the ABSENCE of content, keeps the letters-only reader.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 2, f" {claim} ")
    mutated = flc._rewrite_first_correction_cell(mutated, 3, f" {correction} ")
    assert flc._copy_tokens(claim) != flc._copy_tokens(correction)
    assert flc.check_evidence_cells(mutated) == []


@pytest.mark.parametrize(
    "cell",
    [
        r"\`<br>\`",
        r"\`&nbsp;\`",
        r"\`[](https://x)\`",
        r"\`<!-- removed -->\`",
        r"\`<span>&nbsp;</span>\`",
    ],
)
def test_an_escaped_marker_does_not_hide_markup(cell):
    r"""FAILS if the row splitter DELETES the backslash it uses to suppress a split.

    `_split_row_cells`'s job is not to split on `\|`. Dropping the backslash as well
    rewrote the cell's markdown before every reader saw it, so `\``<br>`` ` reached the guard
    as a clean two-backtick span — accepted, CLEAN — while the renderer showed an escaped
    backtick and a real `<br>` element: ONE character defeated all four render-blank families
    (measured by the fifth review of this change, end-to-end through `check_document()`). The
    splitter keeps the backslash now and `_rendered_cell_text` collapses the escape the way
    the renderer does, which is also why the literal spelling `\<br>` is accepted.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 3, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_evidence_cells(mutated)
    assert any("carries nothing a reader can read" in p for p in problems), problems


@pytest.mark.parametrize("cell", ["`<br>", "`<span>", "`&nbsp;", "``<br>`"])
def test_an_unmatched_backtick_run_does_not_hide_markup(cell):
    """FAILS if an UNMATCHED backtick run is treated as a code span.

    CommonMark does not open a span without a closing run, so `` ` <br> `` — a plausible typo
    of the accepted `` `<br>` `` — renders the tag, and the tag renders nothing. A reader that
    splits on single backticks classified everything after the run as "inside code", stripped
    nothing, and let the cell pass as content (measured by the fourth review of this change).
    A run of N closes only on a run of exactly N, which is why `` ``<br>` `` is refused too.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 3, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_evidence_cells(mutated)
    assert any("carries nothing a reader can read" in p for p in problems), problems


@pytest.mark.parametrize(
    ("column", "cell"),
    [
        (2, "[<br>](https://x)"),
        (3, "[<!-- see section 4 -->](https://x)"),
        (5, "[<br>](`tortoise/sdk.py:1`)"),
    ],
)
def test_a_link_is_reduced_to_its_label_as_a_unit(column, cell):
    """FAILS if a link is reduced only inside a scanned chunk.

    A `[` starts no construct of its own, so once tags were moved out of the chunks a link
    whose label carried one was fed to `LINK_RE` as a fragment that ended at the `<`: the link
    was never reduced to its label and the DESTINATION's letters counted as content — 109 of
    156 measured fail-opens (seventh review of this change), inside the family
    `[](https://x)` had already closed. Links are now scanned as units, with bracket- and
    paren-depth matching, and the LABEL is rendered recursively.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, column, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    assert flc.check_document(mutated, REPO), "a blank-rendering link label passed as content"


@pytest.mark.parametrize(
    ("column", "cell"),
    [
        (2, '<span title="`n/a`">'),
        (3, '<span title="`x`">'),
        (4, '<span title="**STILL-TRUE** `x`">'),
        (5, '<span title="`tortoise/sdk.py:17109`">'),
    ],
)
def test_a_tag_is_stripped_whole_even_across_a_backtick(column, cell):
    """FAILS if a tag is stripped per SEGMENT rather than scanned as a unit.

    A quoted attribute may contain a backtick, a backslash or `>`, so a tag is a single
    construct the renderer consumes whole. Stripping tags inside each scanned segment instead
    meant a construct boundary inside the tag ended the segment before its `>`, so the tag was
    never matched and its own text was counted as CONTENT while the cell rendered blank —
    measured by the sixth review of this change as 41 of 84 tag shapes, and the verdict column
    is the sharpest: a blank verdict cell carried a phantom `**STILL-TRUE**` that satisfied
    `check_verdicts` and agreed with the tally.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, column, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    assert flc.check_document(mutated, REPO), "a blank-rendering tag passed as content"


def test_a_comparison_keeps_its_operand_words():
    """FAILS if `TAG_RE` widens back to "any angle-bracket span".

    A comparison in a §3 cell is prose, not markup: `x < y > z` must read as three words, not
    two. Nothing else pins the narrowing — the wide pattern refuses no cell the narrow one
    accepts, because a comparison carries letters or digits either way — so without this the
    word-list fidelity the placeholder and copy tests depend on is unguarded.
    """
    assert flc._content_cell_words("x < y > z") == ["x", "y", "z"]
    # …and a REAL tag is still stripped, else the pin would buy fidelity by losing the floor.
    assert flc._content_cell_words("<span>x</span>") == ["x"]


def test_an_unlisted_placeholder_is_the_declared_limit():
    """FAILS silently if someone believes the denylist is a closed class.

    §8 declares the denylist a LIST, so an unlisted placeholder passes. That is the arm's
    honest limit, and pinning the acceptance is what keeps the declaration and the code
    from drifting apart in the safe-looking direction (a guard nobody documents as partial
    is read as total). This is also why the self-test arm named for `TBC` is about the LIST
    as it stands, not about "unlisted" behaviour — the review of this change measured that
    `tbc` was in the set, so an arm claiming to prove unlistedness proved listing.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 3, " not applicable ")
    assert flc.check_evidence_cells(mutated) == []


@pytest.mark.parametrize(
    "cell",
    [
        "<span hidden>lorem ipsum</span>",  # an element the BROWSER hides with CSS
        "[][ref]",  # a REFERENCE-style link with no definition renders its literal text
    ],
)
def test_a_declared_limit_is_pinned_as_accepted(cell):
    """FAILS if a declared limit becomes a silent hole — in either direction.

    §8 names three limits; the placeholder one has its own pin above, and these are the other
    two. A declared limit that is not pinned is a declaration nobody has tested: the guard could
    begin refusing them (a false accusation on a required job) or the declaration could go stale
    against a code change, and neither would red anything. Both are measured with a real renderer
    in §8's own words: a CSS-hidden element renders blank, and `[][ref]` renders its literal text.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 3, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    assert flc.check_evidence_cells(mutated) == []


def test_a_near_copy_is_the_declared_limit():
    """FAILS if the copy test starts closing the NEAR-copy class it declares open.

    §8's third limit: the copy test is EQUALITY, so a claim with one word added, dropped or
    swapped — including the word that inverts it — passes. That is deliberate (a heuristic that
    guessed at near-copies would refuse the derivations rows (b), (k) and (l) rely on), and it is
    the limit a reader has to know about, which is why it is pinned rather than only declared.
    """
    original = flc.read_doc(REPO)
    claim = flc._first_correction_cell(original, 2).strip()
    near = f" {claim} — and this is false. "
    mutated = flc._rewrite_first_correction_cell(original, 3, near)
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    assert flc.check_evidence_cells(mutated) == []


@pytest.mark.parametrize(
    ("column", "cell", "expect"),
    [
        (4, "<!-- **STILL-TRUE** -->", "no bolded verdict"),
        (5, "<!-- `tortoise/sdk.py:17109` -->", "names no `path:line`, issue, command or §N"),
    ],
)
def test_a_commented_out_column_is_rejected(column, cell, expect):
    """FAILS if the verdict or evidence column can be hidden inside a comment.

    The prose floor taught the READER to strip tags and comments; the two columns beside it
    still read the RAW cell, so `<!-- **STILL-TRUE** -->` in the verdict cell and
    `<!-- `tortoise/sdk.py:17109` -->` in the evidence cell each left `check_document()` at
    `[]` (measured by the review of this change): a row that loses its verdict AND its
    openable citation while §1 and §8 promise both. Each column reads
    `_rendered_cell_text` now, one function shared with the prose floor.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, column, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    problems = flc.check_document(mutated, REPO)
    assert any(expect in p for p in problems), problems


@pytest.mark.parametrize("shout", [False, True])
def test_a_correction_copying_its_claim_is_rejected(shout):
    """FAILS if a row can restate its claim where its correction belongs.

    A corrections log whose correction is the claim it exists to correct records no
    correction at all — and every referential arm still passes, because the words are
    real. Raw equality would pass the case-changed copy, so the comparison is on the
    casefolded words (measured: `exists` vs `EXISTS` left `check_document()` at `[]`).
    """
    original = flc.read_doc(REPO)
    assert flc.check_evidence_cells(original) == []
    claim = flc._first_correction_cell(original, 2)
    assert claim.strip(), "the first §3 row has no claim — re-derive this mutation"
    mutated = flc._rewrite_first_correction_cell(
        original, 3, f" {claim.strip().upper() if shout else claim.strip()} "
    )
    problems = flc.check_evidence_cells(mutated)
    assert any("is a COPY of its claim" in p for p in problems), problems


def test_a_correction_that_quotes_its_claim_and_adds_is_accepted():
    """FAILS if the copy test over-refuses — the direction a lane routes around.

    A correction that quotes the claim and then corrects it (`**False.** <claim>`) is NOT
    a copy: it carries more words than the claim, so equality passes it. Containment
    would refuse it, and a guard that reds correct prose is a guard a lane learns to
    bypass — which is how a real drift later merges green.
    """
    original = flc.read_doc(REPO)
    claim = flc._first_correction_cell(original, 2)
    mutated = flc._rewrite_first_correction_cell(
        original, 3, f" **False.** {claim.strip()} — it is superseded by the fold "
    )
    assert flc.check_evidence_cells(mutated) == []


@pytest.mark.parametrize(
    "cell",
    [
        "`closed`",  # one content word names exactly what closed
        "#2748, #1695, #1418 closed",  # refs plus one word
        "[the props doc](https://x)",  # a link whose label is the content
        "`&lt;br&gt;`",  # an ESCAPED tag: the reader sees literal text
        "**Ложно** — проверено отсутствует",  # a non-Latin correction is content
        "the R&amp;D lane is checked",  # an entity beside real words
        "2026-09-10",  # a bare date: no LETTER, but a reader reads it
        "#2748, #1695, #1418",  # an issue list records which issues closed
        "`10 < 20 > 5`",  # a comparison is prose, not markup
        "`has 10 fields` where the claim says 8",  # a derived NUMBER is content
        "x = 2",  # a placeholder beside a digit records the digit
        "`<br>`",  # a code span renders LITERALLY: the tag is the text a reader sees
        "`&nbsp;`",  # …and so is the entity
        "``<br>``",  # a run of TWO backticks closes only on a run of two
        r"\<br>",  # an ESCAPED tag renders the literal text `<br>`
        "<!-- unterminated comment",  # an unclosed comment is literal text, not a comment
        "<!-- `<br>`",  # …and the construct AFTER it still has its own rules
        "&nbsp",  # a character reference needs its semicolon; this renders five characters
        "<!-->the lane is checked-->",  # an ABRUPT close ends the comment; the rest is text
        "<!--a--->",  # a comment's text cannot end with `-`, so this is literal text
        "</a b>",  # a CLOSE tag carries no attributes, so this is literal text
        "<textarea>lorem ipsum</textarea>",  # a browser SHOWS this body: only RAW-TEXT hides
        "<xmp>lorem ipsum</xmp>",
        "<plaintext>lorem ipsum",  # the tokenizer switch shows everything after it as TEXT
        "<script>x</SCRIPT>AFTER",  # the close tag is found CASE-INSENSITIVELY, so `AFTER` reads
        '<script><span title="</script>">VISIBLE',  # a close tag inside a TAG's attribute is real
        "<script>[a](</script>VISIBLE)",  # NOT a recognised link, so the close tag is literal
        "<script><!-- </script>AFTER -->",  # the escaped state ends at the close tag
        "<template></template>VISIBLE",  # the body ends at its OWN close tag
        "<script><!--<script>--></script>VISIBLE",  # `-->` leaves the double-escaped state
        "<script><!--<script></script>VISIBLE",  # an UNTERMINATED `<!--` is escaped, not a comment
        "<template>[a</template>b](u)VISIBLE",  # a link's LABEL is markup: the close tag is real
        "<textarea>VISIBLE</textarea>",  # RCDATA text is SHOWN
        "<textarea><!-- note </textarea>",  # …and so is a comment's text inside it
        "x Ложно",  # a placeholder beside a non-Latin word is not placeholders alone
    ],
)
def test_brief_rendering_and_non_latin_cells_are_accepted(cell):
    """FAILS if the content floor over-refuses correct prose.

    The rule is emptiness / declared placeholder / copy — deliberately NOT length. A
    three-word floor was written first and measured false: the page's own shortest cells
    ARE three words, so it sat at the page minimum and refused a legitimate compression
    (`#2748, #1695, #1418 **closed**`). An ASCII-only word class counted a non-Latin
    correction as zero for the same reason. Each case here is one a reader can read.
    """
    original = flc.read_doc(REPO)
    mutated = flc._rewrite_first_correction_cell(original, 3, f" {cell} ")
    assert mutated != original, "the §3 row shape moved — re-derive this mutation"
    assert flc.check_evidence_cells(mutated) == []


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
        lambda t: t.replace("`tortoise/sdk.py:17109`", "`tortoise/sdk.py: 16962`", 1),
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
        ("`tortoise/sdk.py:36215-36218`", "`tortoise/sdk.py:36215-36218 `"),
        ("`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:16 `"),
    ):
        _mutated(tmp_path, monkeypatch, lambda t, o=old, n=new: t.replace(o, n, 1))
        problems = _doc_problems()
        assert any("not a recognised citation" in p for p in problems), (old, problems)


def test_a_shorthand_with_a_trailing_character_is_refused(tmp_path, monkeypatch):
    """FAILS if a trailing space or period after a `:LINE` shorthand is skipped."""
    for old, new in (("`:792`", "`:792 `"), ("`:3162`", "`:999999.`")):
        _mutated(tmp_path, monkeypatch, lambda t, o=old, n=new: t.replace(o, n, 1))
        problems = _doc_problems()
        assert any("not a recognised citation" in p for p in problems), (old, problems)


def test_a_citation_embedded_in_a_longer_span_is_refused(tmp_path, monkeypatch):
    """FAILS if appending a word inside the backticks defuses an out-of-range red.

    The shape arms only see a span whose WHOLE body is `path:line`, so a lane that
    gets a legitimate red could go green by writing the citation inside a sentence.
    Measured: `…:999999 (dead)` and `see …:999999 here` both exited 0.
    """
    for new in (
        "`tortoise/pipeline.py:999999 (dead)`",
        "`see tortoise/pipeline.py:999999 here`",
    ):
        _mutated(
            tmp_path,
            monkeypatch,
            lambda t, n=new: t.replace("`tortoise/pipeline.py:16`", n, 1),
        )
        problems = _doc_problems()
        assert any("not a recognised citation" in p for p in problems), (new, problems)


def test_an_absolute_path_outside_the_tree_is_refused(tmp_path, monkeypatch):
    """FAILS if `/`-rooted citations are exempt wholesale.

    The exemption used to be `startswith(("~", "/"))`, so rewriting every citation
    to an absolute path left the page CLEAN with nothing resolved. An absolute path
    is only evidence if it points inside this tree.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`/nope/tortoise/pipeline.py:16`", 1
        ),
    )
    problems = _doc_problems()
    assert any("absolute path outside" in p for p in problems), problems


def test_a_page_of_nothing_but_tilde_citations_is_drift(tmp_path, monkeypatch):
    """FAILS if re-pointing every citation at `~` buys silence instead of a verdict.

    Two defects met here. The floor counted MATCHES rather than RESOLUTIONS, so
    every citation exempted to `~` still counted as measured and a page of nothing
    but exempt citations could reach the "measured nothing" refusal by luck rather
    than by rule. And the `~` exemption itself was the fail-open #11902's review
    measured: a `~`-rooted `path:line` was skipped entirely, so an out-of-range
    `~/tortoise/pipeline.py:999999` read CLEAN.

    The verdict is now DRIFT (exit 1), not UNVERIFIED: every one of these citations
    is a dangling reference, which §5's own first consequence states — a citation
    into `~/.swarm` is a BROKEN citation. Either way it is never clean, which is the
    property this arm exists to protect, and asserting `1` is strictly stronger
    than the old `2` because it names the false claim instead of refusing to look.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: re.sub(
            r"`((?:tortoise|tests|docs|\.github|config|tools)/[^`]*:\d[^`]*)`",
            r"`~/.swarm/\1`",
            t,
        ),
    )
    problems = _doc_problems()
    assert problems, "a page of nothing but `~`-rooted citations reported CLEAN"
    assert any("does not exist in the tree" in p for p in problems), problems


def test_a_tilde_rooted_citation_is_refused_in_both_spellings(tmp_path, monkeypatch):
    """FAILS if a `~`-rooted citation is exempt again, in EITHER spelling.

    Measured before this arm: an out-of-range `~/tortoise/pipeline.py:999999` read
    CLEAN (`[]`), and so did `~/.swarm/x.md#L999999` — the exemption covered
    `path:line` and the GitHub permalink form alike. No revision of the page ever
    carried such a citation (measured over all 37 commits that touch it: zero in
    either spelling), so the carve-out protected nothing and only disabled a red.
    §8 now says so, and §5.1 already called such a citation BROKEN.
    """
    for needle, expected in (
        ("`~/tortoise/pipeline.py:999999`", "does not exist in the tree"),
        ("`~/.swarm/x.md#L999999`", "not a recognised citation"),
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _n=needle: t.replace("`tortoise/pipeline.py:16`", _n, 1),
            )
            problems = _doc_problems()
            assert any(expected in p for p in problems), (needle, problems)


def test_a_page_of_nothing_but_absolute_citations_is_refused(tmp_path, monkeypatch):
    """FAILS if re-pointing every citation at `/` buys silence instead of a verdict.

    The same hole as the `~` arm above, for the other half of the old
    `startswith(("~", "/"))` exemption: an absolute path is only evidence if it
    points INSIDE this tree, and otherwise each citation must be reported.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: re.sub(
            r"`((?:tortoise|tests|docs|\.github|config|tools)/[^`]*:\d[^`]*)`", r"`/nope/\1`", t
        ),
    )
    problems = _doc_problems()
    assert problems, "a page of nothing but absolute citations reported CLEAN"
    assert any("absolute path outside" in p for p in problems), problems


def test_an_evidence_reference_to_a_missing_section_is_rejected(tmp_path, monkeypatch):
    """FAILS if `§N` is accepted without checking section N exists.

    Measured: replacing all twelve §3 evidence cells with `§9` (there is no §9) left
    `--check` at rc 0 — the emptied column, wearing a section number.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("| §6 below, measured per issue |", "| §9 below, measured per issue |", 1),
    )
    problems = _doc_problems()
    assert any("names no `path:line`, issue, command or §N" in p for p in problems), problems


def test_a_decoy_headline_paragraph_is_rejected(tmp_path, monkeypatch):
    """FAILS if the tally arm takes the FIRST such paragraph anywhere in the file.

    Measured: a decoy carrying a correct-looking tally above §3 plus a falsified real
    sentence left rc 0 — the arm compared a sentence nobody quotes. More than one is a
    page defect, mirroring `check_survivor_arithmetic`'s `len(matches) > 1` rule.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "**Read §3 first if you are about to fix something.**",
            "**The one thing to take away:** whatever the table says.\n\n"
            "**Read §3 first if you are about to fix something.**",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("paragraphs starting" in p for p in problems), problems


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


def test_a_class_first_tally_is_accepted(tmp_path, monkeypatch):
    """FAILS if the SAME assignment written class-first reds as a swap.

    The arm read only "letters preceding a class token", so
    `**SUPERSEDED-BY-FIX**: (c), (d) and (g)` — the identical assignment, reworded —
    returned *"assigns a different verdict … (c) stated ADOPTED, table says
    SUPERSEDED-BY-FIX"*: a false accusation printed at the REQUIRED `docs` job over
    prose that is exactly right, which is the failure mode the shallow arm one
    function over exists to prevent on the other side.

    Accepting it must not go blind — the second half keeps a real swap RED, because
    the class-first reading is adopted only when it covers EVERY non-STILL-TRUE row
    and agrees with the table.
    """
    original = (
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    class_first = (
        "**SUPERSEDED-BY-FIX**: **(c)**, **(d)** and **(g)**; **ADOPTED**: **(e)**; "
        "**DRIFTED**: **(b)**, **(k)** and **(l)**."
    )
    swapped = (
        "**ADOPTED**: **(c)**, **(d)** and **(g)**; **SUPERSEDED-BY-FIX**: **(e)**; "
        "**DRIFTED**: **(b)**, **(k)** and **(l)**."
    )
    assert original in DOC.read_text(encoding="utf-8"), (
        "the tally sentence moved — re-point this pair at its new wording"
    )

    # NOT `== []`: the copy lives at a tmp path, so the registration arm legitimately
    # reports that no `docs/00_index.md` row names it. What must not appear is the
    # false accusation about the sentence.
    _mutated(tmp_path, monkeypatch, lambda t: t.replace(original, class_first, 1))
    assert not any(
        "assigns a different verdict" in p for p in _doc_problems()
    ), "a legitimate class-first reword red the guard"

    _mutated(tmp_path, monkeypatch, lambda t: t.replace(original, swapped, 1))
    assert any(
        "assigns a different verdict" in p for p in _doc_problems()
    ), "accepting class-first must not accept an actual swap"


def test_a_second_table_is_rejected(tmp_path, monkeypatch):
    """FAILS if a row added to a SECOND table in §3 or §6 stays invisible.

    `_table_rows()` reads the section's first table and stops at the first
    non-`|` line, so a thirteenth correction row — or a fifth §6 bucket — appended in
    a fresh table was never enumerated: no verdict check, no evidence floor, no
    population pin. Measured before this arm existed: `--check` rc=0, `--self-test`
    PASS and the whole suite green over a page carrying thirteen rows while §8
    advertises the population pinned to twelve.
    """
    extra = (
        "| # | claim | correction | verdict | evidence |\n"
        "|---|---|---|---|---|\n"
        "| **m** | an undeclared claim | **False.** | **STILL-TRUE** | nothing |\n"
        "\n"
    )
    for anchor in ("## 4. The artefact index", "## 7. What this changes"):
        _mutated(tmp_path, monkeypatch, lambda t, a=anchor: t.replace(a, extra + a, 1))
        assert any(
            "carries 2 tables" in p for p in _doc_problems()
        ), (anchor, _doc_problems())


def test_s8_states_the_arm_counts_the_code_actually_has():
    """FAILS when §8's "other N … all M arms" drifts from the code.

    Nothing pinned this pair, so it drifted twice: at `454c40583c` nine tree-only
    arms made "the other nine" / "all ten arms" exactly right, then two arms were
    added to `check_document()` in later cycles and the sentence kept reading nine
    and ten (measured: eleven tree-only arms, twelve in `check_all()`). A page that
    misstates how much of it is machine-checked is the "declared ≠ stored" defect
    this document exists to catch, so the count is derived here, not trusted.
    """
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
        "thirteen": 13, "fourteen": 14,
    }
    doc = flc.read_doc(REPO)
    tree_only = inspect.getsource(flc.check_document).count("problems.extend(")
    total = tree_only + inspect.getsource(flc.check_all).count("problems.extend(")

    other = re.search(r"[Tt]he other (\w+) read this checkout", doc)
    all_arms = re.search(r"`--check` \(all (\w+) arms\)", doc)
    assert other, "§8 no longer states the tree-only arm count — re-derive this test"
    assert all_arms, "§8 no longer states the `--check` arm count — re-derive this test"
    assert words[other.group(1).lower()] == tree_only, (
        f"§8 says 'the other {other.group(1)}' but check_document() wires {tree_only} arms"
    )
    assert words[all_arms.group(1).lower()] == total, (
        f"§8 says 'all {all_arms.group(1)} arms' but check_all() runs {total}"
    )


def test_a_stray_table_row_with_no_separator_is_rejected(tmp_path, monkeypatch):
    """FAILS if a lone `|` row under §3/§6 stays invisible.

    `check_table_population()` counts SEPARATORS, so the test above covers a
    second TABLE — but a bare row appended after a blank line brings no separator
    and was counted by nothing: measured, it carried the invented verdict
    `FIXED-IT` under §3 and `check_document()` returned `[]`, i.e. a claim class
    §1's closed vocabulary has no handling rule for.
    """
    stray = (
        "| **Z** | a stray row | `tortoise/sdk.py:17109` | **FIXED-IT** |"
        " `tortoise/sdk.py:17109` |\n\n"
    )
    for anchor_heading in ("## 4. The artefact index", "## 7. What this changes"):
        _mutated(
            tmp_path, monkeypatch, lambda t, a=anchor_heading: t.replace(a, stray + a, 1)
        )
        assert any("outside its single table" in p for p in _doc_problems()), (
            anchor_heading,
            _doc_problems(),
        )


# ── citations resolve ──────────────────────────────────────────────────────


def test_a_letter_stated_two_different_ways_is_rejected(tmp_path, monkeypatch):
    """FAILS if a contradicting duplicate mention is out-voted by the other one.

    The attribution maps hold ONE verdict per letter (`before` overwrites,
    `following` setdefaults), so the last (or first) mention silently won and
    `**(e)** is **DRIFTED**, and **(e)** is **ADOPTED**` returned `[]` (clean) while
    the sentence contradicted itself and the table (measured). The CONNECTORS here
    are the ones a copula-only rule missed — `is also`, `was`, `remains` — each of
    which is the same claim written in ordinary English, so each is asserted rather
    than left to whatever the regex happens to accept.
    """
    original = "**(e)** is\n**ADOPTED**"
    for connector in ("is", "is also", "was", "remains"):
        _mutated(
            tmp_path,
            monkeypatch,
            lambda t, c=connector: t.replace(
                original,
                f"**(e)** is **DRIFTED**, and **(e)** {c}\n**ADOPTED**",
                1,
            ),
        )
        problems = _doc_problems()
        assert any("assigns a different verdict" in p for p in problems), (connector, problems)


def test_a_duplicate_mention_after_the_group_is_rejected(tmp_path, monkeypatch):
    """FAILS if a contradicting repeat AFTER a class group is dropped.

    … are **SUPERSEDED-BY-FIX**, and **(d)** is also **ADOPTED**, **(e)** is
    **ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**.

    Two mechanisms catch this shape, and either one suffices: the attribution maps hold
    one verdict per letter with the LAST assignment winning, so the trailing repeat
    overwrites the group's correct verdict (measured: a `before.setdefault(...)` map
    reds here too), and the adjacency layer sees `(d)` + `is also` + `**ADOPTED**`.
    It therefore fails only when BOTH are removed — the test that ISOLATES the
    adjacency layer is `test_a_contradicting_mention_before_the_group_is_rejected`,
    where the maps land on the correct verdict and the repeat sits before it.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is",
            "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, and **(d)** is "
            "also **ADOPTED**, **(e)** is",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("assigns a different verdict" in p for p in problems), problems


def test_a_duplicate_in_a_later_sentence_is_rejected(tmp_path, monkeypatch):
    """FAILS if the mention scan stops at the sentence carrying the letter list.

    Reachable, and measured: the takeaway paragraph's closing sentence is what a lane
    quoting the summary may include —

        … and **(b)**, **(k)** and **(l)** are **DRIFTED**. Note **(d)** is **ADOPTED**.

    — and reading only the sentence that carries "no longer read as written" returned
    `[]` (clean) over it, while the table says `(d)` is SUPERSEDED-BY-FIX. The scan
    covers the whole paragraph for exactly this reason; only rows the table calls
    non-STILL-TRUE can be misattributed, so the complement sentence adds no claims.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "**(b)**, **(k)** and **(l)** are **DRIFTED**.",
            "**(b)**, **(k)** and **(l)** are **DRIFTED**. Note **(d)** is "
            "**ADOPTED**.",
            1,
        ),
    )
    problems = _doc_problems()
    assert any("assigns a different verdict" in p for p in problems), problems


def test_a_citation_separated_by_whitespace_is_refused(tmp_path, monkeypatch):
    """FAILS if `path <LINE>` or the GitHub permalink spelling is skipped, not refused.

    \u00a78 promises both are refused. `path <LINE>` is the whitespace delimiter; the
    PERMALINK form (path, `#`, `L`, line number) is the one `#` case that survives the
    objection which made every other bare `#` a carve-out: an `owner/repo#N` issue ref
    carries no `L`, so `#L` + digits cannot be mistaken for one — and it is what a human
    writes when copying a GitHub link (#11952 P1, measured: both an out-of-range and an
    in-range permalink left the page CLEAN before the arm existed, so the likeliest
    real-world spelling was also the one that looked verified and was not).

    The rest are NAMED carve-outs in \u00a78 rather than silent gaps, because every wider
    delimiter set refused correct prose when tried against this repository's own
    `docs/` (measured \u2014 `_` parses `tests/test_findings_ledger_2852.py`, `-` parses a
    dated slug, `/` parses plain paths and dates, bare `#` parses `owner/repo#N` issue
    refs, `@` parses commit refs, `,` parses calls).

    The exclusions are asserted here too, because each was a measured false accusation:
    a backticked COMMAND with a numeric argument is a legal \u00a71 evidence form, and an
    `owner/repo#N` issue ref is not a citation at all. The BARE-NUMBER `#` form is
    asserted exempt as well — it is the boundary this change is drawn against, and a
    future widening that swallows it would red this repo's own issue refs.
    """
    for spelling in (
        "tortoise/pipeline.py 999999",
        "tortoise/nope.py 16",
        "tortoise/pipeline.py#L999999",
        "tortoise/pipeline.py#L16",
        "see tortoise/pipeline.py#L16 here",
        "tortoise/pipeline.py#L 999999",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=spelling: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            problems = _doc_problems()
            assert any("not a recognised citation" in p for p in problems), (
                f"a citation rewritten as {spelling!r} was accepted \u2014 one character "
                f"of reformatting defused the red: {problems}"
            )
    for not_a_citation in (
        "uv run python tools/collision_preflight.py 2852 --repo .",
        "daniel-ospina/tortoise#9363",
        "tortoise/pipeline.py#999999",
        "localhost 6379",
        "fetch-depth 0",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=not_a_citation: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            assert not any(
                "not a recognised citation" in p for p in _doc_problems()
            ), f"{not_a_citation!r} must not be refused as a citation"

def test_a_class_first_group_with_a_later_mention_is_not_a_swap(tmp_path, monkeypatch):
    """FAILS if the agreement rule turns a legitimate class-first tally red.

    The first attempt at "every mention must agree" held SETS inside the two
    attribution maps, and that reds correct prose: a class-first group
    (`**SUPERSEDED-BY-FIX**: (c), (d) and (g)`) makes the letters-first map's region
    span the preceding group, so the same letters pick up a second verdict the
    sentence never asserts (measured: class-first prose that was clean on the parent
    commit went RED). The rule is adjacency-based for exactly this reason.
    """
    original = (
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    legit = (
        "**SUPERSEDED-BY-FIX**: **(c)**, **(d)** and **(g)**; **ADOPTED**: **(e)**; "
        "**DRIFTED**: **(b)**, **(k)** and **(l)**, while **(e)** was the one the "
        "rewrite adopted."
    )
    _mutated(tmp_path, monkeypatch, lambda t: t.replace(original, legit, 1))
    assert not any(
        "assigns a different verdict" in p for p in _doc_problems()
    ), "a legitimate class-first tally with a later mention red the guard"


def test_a_class_first_tally_joined_by_commas_is_not_a_swap(tmp_path, monkeypatch):
    """FAILS if a comma-joined class-first tally reds as a swap it never made.

    Reachable, and measured: the connector between a letter and the next class token
    is `, ` when class-first groups are comma-joined — and that is also the shape a
    hard wrap produces, because the checker collapses whitespace before matching. A
    rule whose copula and adverb were BOTH optional accepted a bare `, `, so
    `**SUPERSEDED-BY-FIX**: **(c)**, **(d)** and **(g)**, **ADOPTED**: **(e)**, …`
    red `(e) stated ADOPTED+SUPERSEDED-BY-FIX` on the one ordering the rule was added
    to support (parent commit: clean). The connector must therefore CARRY a copula or
    an adverb; a bare separator claims nothing.
    """
    original = (
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    for separator in (", ", ",\n", ", and ", "; "):
        legit = (
            f"**SUPERSEDED-BY-FIX**: **(c)**, **(d)** and **(g)**{separator}"
            f"**ADOPTED**: **(e)**{separator}"
            f"**DRIFTED**: **(b)**, **(k)** and **(l)**."
        )
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, _l=legit: t.replace(original, _l, 1))
            assert not any(
                "assigns a different verdict" in p for p in _doc_problems()
            ), f"a comma-joined class-first tally red the guard at separator {separator!r}"


def test_a_contradicting_mention_before_the_group_is_rejected(tmp_path, monkeypatch):
    """FAILS if a contradicting mention outside the adopted map is dropped.

    The adopted map is chosen whole, so a mention read only by the OTHER ordering was
    discarded: `**(e)** is **DRIFTED**, ` before a class-first group that says
    `**ADOPTED**: **(e)**` returned clean while the sentence asserted both (measured).
    """
    original = (
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    contradictory = (
        "**(e)** is **DRIFTED**, **SUPERSEDED-BY-FIX**: **(c)**, **(d)** and **(g)**; "
        "**ADOPTED**: **(e)**; **DRIFTED**: **(b)**, **(k)** and **(l)**."
    )
    _mutated(tmp_path, monkeypatch, lambda t: t.replace(original, contradictory, 1))
    problems = _doc_problems()
    assert any("assigns a different verdict" in p for p in problems), problems


def test_a_dated_reading_is_not_the_rows_current_verdict(tmp_path, monkeypatch):
    """FAILS if a dated mention of a row reds the tally as a contradiction.

    This page's idiom is a dated reading — "(d) was **STILL-TRUE** as written on
    2026-09-10" — because §3's subject is what a 2026-09-10 ledger claimed versus what
    the tree does now. Measured: appending that sentence to the takeaway paragraph red
    the arm with "(d) stated STILL-TRUE+SUPERSEDED-BY-FIX" over a page whose table is
    untouched, i.e. a false accusation on correct prose at the REQUIRED `docs` job. The
    date marker is what separates a historical statement from a current one; removing
    the `dated` guard in the checker reds this test.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "**(b)**, **(k)** and **(l)** are **DRIFTED**.",
            "**(b)**, **(k)** and **(l)** are **DRIFTED**. **(d)** was "
            "**STILL-TRUE** as written on 2026-09-10.",
            1,
        ),
    )
    assert not any("assigns a different verdict" in p for p in _doc_problems()), (
        "a dated reading of a row was read as the row's current verdict"
    )


def test_the_span_readers_and_the_extractor_share_one_spelling_list():
    """FAILS if the letter spellings become TWO literals that must be kept in step.

    The round-4 fix widened three arms from `\\**(x)**`-only to the extractor's four
    spellings, and claimed `_LETTER_ALT_MARKED` was the single source. It was not: the
    extractor still carried its own copy of the same six alternatives, so the two could
    drift — and a review measured both directions of the drift:

        add a spelling to `_NAMED_LETTER_RE` only
            -> `— _c_, _d_ do not.` REDS as a false accusation
        add a spelling to `_LETTER_ALT_MARKED` only
            -> `Note _d_ is **ADOPTED**.` reads CLEAN (the fail-open returns)

    Both with the whole suite green. A hand-kept pair of literals is exactly the
    "docstring that says VERIFIED" class the repo's `MEMORY.md` forbids, so the
    extractor is now BUILT from the constant and this test pins the identity.
    """
    assert flc._NAMED_LETTER_RE.pattern == flc._LETTER_ALT_MARKED, (
        "the extractor must be compiled FROM `_LETTER_ALT_MARKED`, not from a second "
        "literal that has to be kept in step by hand"
    )
    assert flc._LETTER_ALT_MARKED in flc._LETTER_ITEM_ANY, (
        "the governed-complement pattern must embed the same spelling list"
    )
    #: And the list really is the four documented spellings — assert the SPELLINGS,
    #: not a description of them, so one dropped from the shared constant is caught too.
    for spelling in ("**(c)**", "**c**", "`c`", "`(c)`", "*(c)*", "*c*", "(c)"):
        assert flc._NAMED_LETTER_RE.findall(spelling), (
            f"{spelling!r} is a documented letter spelling and must be accepted"
        )


def test_a_contradicting_mention_is_read_in_every_spelling(tmp_path, monkeypatch):
    """FAILS if a contradicting verdict repeat is only seen when written `**(x)**`.

    §8 blesses four spellings — bold, code, italic and bare — and both the extractor
    and the candidate gate read all of them. The MENTION scan did not: it carried its
    own `\\*\\*\\(([a-z])\\)\\*\\*`, so a second, contradicting statement of a row's
    verdict was invisible in three of the four. Measured at this head against the real
    page, where `(d)` is SUPERSEDED-BY-FIX in §3:

        Note **(d)** is **ADOPTED**.   -> red (caught)
        Note (d) is **ADOPTED**.       -> `[]`  (the fail-open this test closes)
        Note `d` is **ADOPTED**.       -> `[]`
        Note *d* is **ADOPTED**.       -> `[]`

    `**` must not be what decides whether a contradiction is seen, so every MARKED
    spelling is asserted. ⛔ A bare `d` is deliberately NOT in this list — see
    `test_a_bare_letter_outside_a_list_run_assigns_no_verdict` for that boundary.
    """
    anchor = "**(b)**, **(k)** and **(l)** are **DRIFTED**."
    for spelling in ("**(d)**", "(d)", "`d`", "*d*"):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, s=spelling: t.replace(
                    anchor, f"{anchor} Note {s} is **ADOPTED**.", 1
                ),
            )
            problems = _doc_problems()
            assert any("assigns a different verdict" in p for p in problems), (
                f"a contradicting mention spelled {spelling!r} must be read: {problems}"
            )


def test_a_bare_letter_outside_a_list_run_assigns_no_verdict(tmp_path, monkeypatch):
    """PINS a deliberate boundary: a bare letter in prose is prose, not a verdict.

    This is the OTHER direction of the test above, and it is a decision rather than an
    accident. `a` and `i` are English words, so reading every bare `[a-l]` token as a
    verdict claim would let ordinary prose assign one — the false-accusation class this
    arm must not create either (`… as a rule …` claims nothing about row `a`). A bare
    letter is therefore a member only inside a list RUN, which is the same rule
    `_named_letters` applies to §3's letter lists.

    ⚠️ STATED RESIDUAL: a page that wrote its contradicting repeat as `Note d is
    **ADOPTED**.` — bare, outside any run — would still read CLEAN. The four MARKED
    spellings are the ones §8 names as letter spellings and the ones this arm now reads.
    """
    anchor = "**(b)**, **(k)** and **(l)** are **DRIFTED**."
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(anchor, f"{anchor} Note d is **ADOPTED**.", 1),
    )
    assert not any("assigns a different verdict" in p for p in _doc_problems()), (
        "a bare letter outside a list run must not be read as a verdict claim"
    )


def test_the_class_first_spelling_is_read_in_every_spelling(tmp_path, monkeypatch):
    """FAILS if the class-first assignment is only understood when written `**(x)**`.

    The checker's own comment blesses the class-first form —
    `**SUPERSEDED-BY-FIX**: (c), (d) and (g)` — and quotes it in PLAIN PARENTHESES,
    which is exactly the spelling the attribution scan could not read. Measured at
    this head: rewriting the shipped tally as class-first stayed CLEAN with `**(x)**`
    and RED with `(x)`, `` `x` `` and `*x*`, all seven rows accused of stating no
    verdict. That is a false accusation on a documented spelling, the class one arm
    over says this guard must not create.
    """
    original = (
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    #: (spelling template, letters in that spelling) — same assignment, reworded.
    for template in ("**({})**", "({})", "`{}`", "*{}*"):
        rewritten = (
            f"**SUPERSEDED-BY-FIX**: {template.format('c')}, {template.format('d')} "
            f"and {template.format('g')}, **ADOPTED**: {template.format('e')}, and "
            f"**DRIFTED**: {template.format('b')}, {template.format('k')} and "
            f"{template.format('l')}."
        )
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, r=rewritten: t.replace(original, r, 1),
            )
            problems = _doc_problems()
            assert not any("assigns a different verdict" in p for p in problems), (
                f"the class-first spelling {template!r} is the same assignment and "
                f"must not be accused of a swap: {problems}"
            )


def test_the_complement_clause_is_read_in_every_spelling(tmp_path, monkeypatch):
    """FAILS if the negated complement is only stripped when written `**(x)**`.

    `_still_list_may_spell_its_own_complement` pins the CONSTRUCTION with the bold
    spelling. The stripping rule carried its own `\\*\\*\\([a-z]\\)\\*\\*`, so the other
    three documented spellings were not stripped and were then compared as part of the
    STILL-TRUE list. Measured at this head, on the shipped sentence plus
    `— <complement> do not.`:

        **(c)**, **(d)**   -> CLEAN
        `c`, `d`           -> "STILL-TRUE list names ['a','c','d','f','h','i','j']"
        *c*, *d*           -> same
        (c), (d)           -> same
        c, d               -> same

    A group ANCHORED to the clause end is the one place a bare letter is safe, so the
    bare spelling is asserted here and only here.
    """
    anchor = "**(i)** and **(j)** still read as written."
    for complement in (
        "**(c)**, **(d)**",
        "`c`, `d`",
        "*c*, *d*",
        "(c), (d)",
        "c, d",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, c=complement: t.replace(
                    anchor, f"**(i)** and **(j)** still read as written — {c} do not.", 1
                ),
            )
            problems = _doc_problems()
            assert not any("STILL-TRUE list names" in p for p in problems), (
                f"the complement spelled {complement!r} must be stripped, not counted: "
                f"{problems}"
            )


def test_a_marked_letter_outside_a_list_run_is_not_a_complement_member(tmp_path, monkeypatch):
    """FAILS open if a marked letter ANYWHERE in the clause certifies list membership.

    `_named_letters` reads a MARKED letter unconditionally — correct for verdict
    ATTRIBUTION, where the markup IS the claim, and wrong for the complement COMPARISON,
    which compares a LIST. Ordinary prose marks letters: `(i)` is the standard
    roman-numeral enumerator and `(a)`/`(c)` are this page's own cross-reference idiom.
    Measured on the shipped page before `_listed_letters` existed — both of these read
    CLEAN with `i` (resp. `a`) missing from the list they were compared against:

        **(a)**, **(f)**, **(h)** and **(j)** still read as written (see (i)).  -> CLEAN
        **(f)**, **(h)**, **(i)** and **(j)** still read as written, unlike (a). -> CLEAN

    WHY IT IS REACHABLE: the parenthetical cross-reference is this page's normal prose, so
    a lane that drops a row from the list inherits a green check from the sentence that
    comments on it. The non-still list was protected by a second arm (verdict
    attribution); the complement had none.

    ⛔ THE FIX IS RUN MEMBERSHIP FOR EVERY SPELLING, NOT `_bare_list_members` — that
    helper SKIPS masked items and would drop every member of the page's own
    `**(a)**, **(f)**, **(h)** and **(j)**` spelling. The one-member case is a DELIBERATE
    over-refusal, pinned here so a change of mind must move it: a lone PARENTHESISED
    `(a)` is prose (the page's own candidacy rule: `PAREN` is "a LIST ITEM only, never a
    standalone match"), so a one-member complement must be spelled marked.
    """
    anchor = "**(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
    # The stale list each mutation writes OMITS a STILL-TRUE row; the only thing that could
    # certify it is the marked letter carried by the prose tail.
    for tail in (
        " (see (i)).",
        ", unlike (a).",
        " — cf. (j).",
    ):
        stale = "**(a)**, **(f)**, **(h)** and **(j)** still read as written" + tail
        if "(a)" in tail:
            stale = "**(f)**, **(h)**, **(i)** and **(j)** still read as written" + tail
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, s=stale: t.replace(anchor, s, 1))
            problems = _doc_problems()
            assert any("STILL-TRUE list names" in p for p in problems), (
                "a marked letter in the prose tail must not certify the complement list: "
                f"{tail!r} left {problems}"
            )
    # The documented spellings still read every member — the run, not the prose, decides.
    assert flc._listed_letters("Only **(a)**, **(f)**, **(h)**, **(i)** and **(j)**.") == {
        "a", "f", "h", "i", "j"
    }
    assert flc._listed_letters("Only a, f, h, i, j.") == {"a", "f", "h", "i", "j"}
    # THE OVER-REFUSAL, pinned: a lone parenthesised letter is prose; a lone MARKED one is a
    # one-member list and is read.
    assert flc._listed_letters("Only (a) still read as written.") == set()
    for spelling in ("`a`", "**a**", "*a*"):
        assert flc._listed_letters(f"Only {spelling} still read as written.") == {"a"}, (
            f"a one-member complement written {spelling!r} must still be read"
        )


def test_the_negated_complement_clause_is_validated_against_the_table(tmp_path, monkeypatch):
    """FAILS open if the governed group is STRIPPED and never validated.

    `still_clause = text[: negation.start()]` discards everything after `do not` /
    `no longer`, so the group it governs was never compared with anything. Measured on the
    shipped page before this arm existed — BOTH of these read CLEAN:

        … still read as written — **(a)** and **(f)** do not.             (names STILL-TRUE rows)
        … still read as written — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** do not.
                                                                   (contradicts itself, twice)

    WHY IT IS REACHABLE: it is the second half of the page's own documented spelling, so a
    lane editing the complement by hand can name the wrong rows while the tally above it
    stays correct and the arm stays green. The governed group is the COMPLEMENT of the
    still-list, so every letter it names must be a row the table does NOT call STILL-TRUE —
    a check with no new ambiguity, because the table is beside it.
    """
    anchor = "**(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
    # The CORRECT complement stays clean: the arm must not accuse the documented spelling.
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                anchor,
                anchor[:-1] + " — **(c)**, **(d)**, **(e)** and **(g)** do not.",
                1,
            ),
        )
        assert not any("negated complement" in p for p in _doc_problems())
    # …while naming a STILL-TRUE row as "does not [still] read" is a self-contradiction.
    for governed in (
        "**(a)** and **(f)**",
        "**(a)**, **(f)**, **(h)**, **(i)** and **(j)**",
        "(a), (f)",
        "a, f",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, g=governed: t.replace(
                    anchor, anchor[:-1] + f" — {g} do not.", 1
                ),
            )
            problems = _doc_problems()
            assert any("negated complement" in p for p in problems), (
                f"the governed group {governed!r} names STILL-TRUE rows and must red: {problems}"
            )


def _tally_problems() -> list[str]:
    """`_doc_problems()` restricted to the §3 tally arms.

    A mutated copy lives in `tmp_path`, so `check_registration` always reports it as
    unregistered (correctly — the temp file is not in `docs/00_index.md`). The new arms
    below assert about the TALLY, so they filter to it rather than pretending the whole
    document is clean.
    """
    return [
        p
        for p in _doc_problems()
        if "STILL-TRUE list names" in p or "negated complement" in p
    ]


def test_a_marked_cross_reference_is_not_a_complement_member(tmp_path, monkeypatch):
    """FAILS open if a lone MARKED letter in prose certifies (or accuses) a list member.

    Round 7's fix refused only the PARENTHESISED spelling of a prose cross-reference. The
    page's own cross-reference idiom is MARKED — `See correction **(e)**.` at `:82`,
    `see correction **(i)**` at `:199` — so all three marked spellings still supplied a
    member, and the mirror case reded a CORRECT page. Both were measured against the
    shipped sentence, before the group split was anchored on the phrase:

        **(a)**, **(f)**, **(h)** and **(j)** still read as written (see correction **(i)**).
                                   -> CLEAN over a list that omits `i`   (fail-open)
        **(a)**, **(f)**, **(h)**, **(i)** and **(j)** still read as written, unlike **(b)**.
                                   -> RED with `(b)` as a still-true member  (false accusation)

    WHY IT IS REACHABLE: `**(i)**` is how this page points at a row, so the sentence that
    comments on a list is exactly where the phantom comes from. The fix is structural —
    membership is read from the RUN between the clause start and the phrase — so no
    spelling of a later reference can contribute, in either direction.
    """
    anchor = "**(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
    stale = "**(a)**, **(f)**, **(h)** and **(j)** still read as written"
    for tail in (" (see correction **(i)**).", " (see `i`).", " (see *i*)."):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, s=stale + tail: t.replace(anchor, s, 1))
            problems = _doc_problems()
            assert any("STILL-TRUE list names" in p for p in problems), (
                f"a marked cross-reference {tail!r} must not certify the list: {problems}"
            )
    # …and the mirror: a CORRECT list followed by a marked reference to a non-still row must
    # not be accused of naming it.
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(anchor, anchor[:-1] + ", unlike **(b)**.", 1),
        )
        assert not _tally_problems()


def test_the_governed_complement_is_read_by_its_run_not_by_its_tail(tmp_path, monkeypatch):
    """FAILS if the governed group is peeled from the clause END instead of read as a run.

    The peel matched `<separator>?<letter>$` repeatedly, so it was narrower than English in
    BOTH directions, each measured against the shipped sentence:

    FAIL-OPEN — any word between the group and its verb stopped the peel, and the group was
    then neither collected nor validated:

        … — **(a)** and **(f)** (both re-measured) do not.   -> CLEAN (names STILL-TRUE rows)
        … — **(a)** and **(f)** certainly do not.             -> CLEAN

    FALSE ACCUSATION — the peel's separator set was `,`/`and`/`;`/dashes, while the page
    documents `&`, `or`, `also`, `plus`, `as well as`, `followed by`, `namely` too, so a
    CORRECT complement reded as still-true: `… — **(b)** & **(k)** & **(l)** do not.`

    WHY IT IS REACHABLE: an author writing a correct complement naturally adds a qualifier
    or uses `&`/`or`; both are ordinary prose. The fix anchors the split on the sentence's
    own phrase, after which the separator set is irrelevant — a run is a run.
    """
    anchor = "**(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
    for governed in ("**(a)** and **(f)**", "**(a)**, **(f)**", "(a), (f)", "a and f"):
        for middle in (" (both re-measured)", " certainly", " usually", ""):
            mutation = anchor[:-1] + f" — {governed}{middle} do not."
            with monkeypatch.context() as patch:
                _mutated(tmp_path, patch, lambda t, m=mutation: t.replace(anchor, m, 1))
                problems = _doc_problems()
                assert any("negated complement" in p for p in problems), (
                    f"a governed group {governed!r} with {middle!r} must still be validated: "
                    f"{problems}"
                )
    # Every documented separator joins a CORRECT complement without an accusation.
    for join in (" & ", " or ", " ; ", " also ", " plus ", ", and ", ", "):
        mutation = anchor[:-1] + f" — **(b)**{join}**(k)**{join}**(l)** do not."
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, m=mutation: t.replace(anchor, m, 1))
            assert not _tally_problems(), f"a documented separator {join!r} must not red a correct page"


def test_no_longer_governs_a_complement_and_does_not_hide_a_second_tally(tmp_path, monkeypatch):
    """FAILS both ways if the word `no longer` — rather than the PHRASE — picks the group.

    The still-sentence candidacy filter dropped any sentence CONTAINING `no longer`, which
    made the `no longer` negation governor unreachable. Measured against the shipped
    sentence, both directions:

        … while **(b)**, **(k)** and **(l)** no longer do.        (CORRECT)  -> RED, (none)
        <stale still-sentence> …, while the rest no longer do.   (STALE)    -> CLEAN,
            i.e. the same-paragraph second sentence the "exactly one" rule exists to refuse

    WHY IT IS REACHABLE: `no longer do` is the natural way to write the complement, so the
    correct spelling red and the stale one hid. Only the non-still PHRASE (`no longer read
    as written`) belongs to the other arm, and only it is excluded now.
    """
    anchor = "**(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                anchor, anchor[:-1] + ", while **(b)**, **(k)** and **(l)** no longer do.", 1
            ),
        )
        assert not _tally_problems()
    stale = (
        "Only **(a)**, **(f)**, **(h)** and **(j)** still read as written, while the rest "
        "no longer do. "
    )
    with monkeypatch.context() as patch:
        _mutated(tmp_path, patch, lambda t: t.replace(anchor, stale + anchor, 1))
        problems = _doc_problems()
        assert any("2 sentences saying 'still read as written'" in p for p in problems), (
            f"a stale second still-sentence spelled with `no longer do` must be counted: {problems}"
        )


def test_a_still_list_may_spell_its_own_complement(tmp_path, monkeypatch):
    """FAILS if the STILL-TRUE list swallows the letters its negation governs.

    Correct prose names both halves: "**(h)**, **(i)** and **(j)** still read as
    written — **(c)**, **(d)**, **(e)** and **(g)** do not". Measured: the list was read
    from the whole sentence, so the arm accused the page of naming
    `['a','c','d','e','f','g','h','i','j']` when the table's STILL-TRUE rows are
    `['a','f','h','i','j']` — a false accusation whose own message was false. The
    guarded clause stops at the negation and the letters it governs.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "**(h)**, **(i)** and **(j)** still read as written.",
            "**(h)**, **(i)** and **(j)** still read as written — **(c)**, **(d)**, "
            "**(e)** and **(g)** do not.",
            1,
        ),
    )
    assert not any("STILL-TRUE list names" in p for p in _doc_problems()), (
        "a complement clause was read as part of the STILL-TRUE list"
    )


def test_the_s8_citation_carve_outs_stay_skippable(tmp_path, monkeypatch):
    """FAILS if a delimiter §8 names as a carve-out starts being refused, or vice versa.

    §8 lists the shapes the refusal does NOT measure — `path#LINE`, `path@LINE`,
    `path-LINE`, `path_LINE`, `path/LINE`, `path,LINE`, `path:L LINE`, `path N --flag`,
    and an HTTP-status line — because every one of them refused correct prose from this
    repository's own `docs/` when it was measured (the whole tree then — it moves, so re-derive
    it with `git ls-files docs | wc -l`, which counts `.md` and non-`.md` alike; the `.md`-only
    subset is 803); a hand-typed population rots
    the same way the span counts did). Nothing pinned that list,
    so it could drift from the code with every test green; that is how the whitespace
    RANGE form (`path 16-18`) and `path N --flag` went unnoticed. This test is the
    guard on the boundary in BOTH directions: the carve-outs stay skippable, and the two
    spellings the arm DOES measure (`path N` and `path START-END`) are refused.
    """
    refused = ("tortoise/nope_module.py 16-18", "tortoise/nope_module.py 16",
               "tortoise/nope_module.py|16",
               # A `~`-rooted path was a §8 carve-out until #11902's review measured it
               # fail-open in both spellings: the exemption skipped `~/…`, so this
               # permalink (and an out-of-range `~/` `path:line`) read CLEAN while no
               # revision of the page ever carried one (measured over all 37 commits
               # that touch it: zero). §5.1 already called such a citation BROKEN.
               "~/.swarm/x.md#L999999",
               # An HTTP status after a PATH is refused wherever it appears — §8 names
               # ONLY the `.html`/`.htm` form as an HTTP carve-out, because that is the
               # only shape the exemption tests. This spelling is here so the doc and
               # the code cannot drift apart again in the direction §8 used to claim
               # ("a status inside error text"): measured, it reds, and a lane told
               # otherwise would red the required `docs` job on prose the page exempts.
               "upstream /v1/context 503")
    for spelling in refused:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=spelling: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            assert any(
                "not a recognised citation" in p for p in _doc_problems()
            ), f"{spelling!r} is the spelling the arm measures, and it passed"
    for carve_out in (
        "path.py#16",
        "path.py@16",
        "path.py-16",
        "path.py_16",
        "path.py/16",
        "path.py,16",
        "path.py:L16",
        "Makefile 12",
        "daniel-ospina/tortoise#9363",
        "index.html 200",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=carve_out: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            assert not any(
                "not a recognised citation" in p for p in _doc_problems()
            ), f"{carve_out!r} is a §8 carve-out and must stay skippable"


def test_an_evidence_cell_of_prose_is_rejected(tmp_path, monkeypatch):
    """FAILS if `word:digits` counts as evidence.

    `CITATION_RE`'s path group is OPTIONAL, so a `fullmatch` alone accepted any
    marker-less token ending in `:digits`. Measured: every §3 evidence cell replaced
    with `username:1234` (or one cell with `see row (c):3144`) left `--check` at rc 0
    — the whole evidence column unopenable, while §1 and §8 promise each cell names
    something a reader can open. The marker test is the one `check_citations` already
    applies, so removing it from the evidence floor reds this test.
    """
    def prose_only_evidence(text: str) -> str:
        """Row (a)'s whole evidence cell becomes `word:digits` English."""
        out = []
        for line in text.splitlines(keepends=True):
            if line.startswith("| **a** |"):
                head, sep, _ = line.rpartition("| **STILL-TRUE** |")
                line = f"{head}{sep} `username:1234` |\n"
            out.append(line)
        return "".join(out)

    _mutated(tmp_path, monkeypatch, prose_only_evidence)
    assert any("evidence cell" in p for p in _doc_problems()), (
        "an evidence cell carrying only `word:digits` prose was accepted as evidence"
    )


def test_an_owner_repo_issue_ref_counts_as_evidence(tmp_path, monkeypatch):
    r"""FAILS if `owner/repo#N` is not accepted as an issue reference.

    §8 calls that spelling "the form this repo writes everywhere", and §1 accepts "an
    issue" without prescribing a spelling — but the bare-`#N` pattern refused it as
    "names no `path:line`, issue, command or §N", i.e. a false accusation on correct
    evidence at the REQUIRED `docs` job. Widening it back to the bare issue-ref pattern reds this test.
    """
    def issue_ref_only_evidence(text: str) -> str:
        """Row (a)'s whole evidence cell becomes one `owner/repo#N` reference."""
        out = []
        for line in text.splitlines(keepends=True):
            if line.startswith("| **a** |"):
                head, sep, _ = line.rpartition("| **STILL-TRUE** |")
                line = f"{head}{sep} `daniel-ospina/tortoise#9363` |\n"
            out.append(line)
        return "".join(out)

    _mutated(tmp_path, monkeypatch, issue_ref_only_evidence)
    assert not any("evidence cell" in p for p in _doc_problems()), (
        "an `owner/repo#N` issue ref was refused as an evidence cell"
    )


def test_a_host_port_is_not_a_citation(tmp_path, monkeypatch):
    """FAILS if a dotted `host:port` is resolved as a `path:line`.

    §8 promises a host:port in prose does not red this context. `_has_path_marker`
    sees the dot in `127.0.0.1` or `api.example.com` and called it a path, so the
    resolver reported "127.0.0.1 does not exist in the tree" — measured for
    `127.0.0.1:6379`, `0.0.0.0:8000`, `api.example.com:443` and `db.internal:5432`
    (bare `localhost:6379` was already green because it has no dot). The carve-out is
    a dotted HOST plus a port, which is why it cannot swallow
    `tortoise/pipeline.py:16`; removing `_is_host_port` from the resolver reds this.
    """
    for host_port in (
        "127.0.0.1:6379",
        "0.0.0.0:8000",
        "api.example.com:443",
        "db.internal:5432",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=host_port: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            assert not any(
                p.startswith("citation ") or "not a recognised citation" in p
                for p in _doc_problems()
            ), f"{host_port!r} is a host:port, not a citation: {_doc_problems()}"


def test_a_leading_date_makes_a_reading_historical(tmp_path, monkeypatch):
    """FAILS if a dated reading reds when the date comes BEFORE the claim.

    "As of 2026-09-10 **(d)** was **STILL-TRUE**" and "**(d)** was **STILL-TRUE** as
    written on 2026-09-10" are the same historical statement. The guard first read only
    the trailing form and red the leading one with a false accusation (measured); the
    date-side test now looks on both sides, and `Note **(d)** is **ADOPTED**.` — no
    date at all — still reds.
    """
    takeaway = "**The one thing to take away:**"

    def append(sentence: str) -> str:
        text = DOC.read_text(encoding="utf-8")
        start = text.index(takeaway)
        end = text.index("\n\n", start)
        return text[:end] + " " + sentence + text[end:]

    for dated in (
        "As of 2026-09-10 **(d)** was **STILL-TRUE**.",
        "**(d)** was **STILL-TRUE** as written on 2026-09-10.",
        "**(d)** was **STILL-TRUE** on the 2026-09-10 ledger.",
    ):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, _s=dated: append(_s))
            assert not any(
                "assigns a different verdict" in p for p in _doc_problems()
            ), f"{dated!r} is a dated reading, not a contradiction"
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: append("Note **(d)** is **ADOPTED**."),
        )
        assert any(
            "assigns a different verdict" in p for p in _doc_problems()
        ), "an undated contradicting mention must still red"


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


def test_out_of_tree_citation_is_reported(tmp_path, monkeypatch):
    """FAILS if a RELATIVE citation that escapes the repository is called clean.

    Reachable, and measured clean before this arm: a `../..`-escaping citation
    resolves to a real file (`../../../../..` + `/etc/hostname:1` verified) that
    `is_file()` accepts, so the existence-only check certified "evidence" the repo
    does not contain — the class this page is about.

    ⚠️ The arm is about RELATIVE escapes only: a `/`-rooted path is resolved against
    the repository root and refused if it points outside it (its own arm), and a
    `~`-rooted path is refused by the citation arm outright — that exemption was
    measured fail-open and removed (#11902), so do not read this test as protecting
    it. Do not "fix" a red here by re-exempting either: that would restore a
    fail-open.
    """
    def add_escaping_citation(text: str) -> str:
        return text.replace(
            "It resolves on none of the machines measured (§5) — a",
            "`../../../../../../etc/hostname:1`",
            1,
        )

    _mutated(tmp_path, monkeypatch, add_escaping_citation)
    problems = _doc_problems()
    assert any("resolves outside the repository" in p for p in problems), problems


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
    `:3162` and row (e)'s `:66` were skipped entirely while §8 told the reader
    every `path:line` in §3/§5 is checked. Skipped evidence presents as checked
    evidence, which is the defect this page is about.
    """
    matched = {m.group(0) for m in flc.CITATION_RE.finditer(flc.read_doc(REPO))}
    for shorthand in ("`:792`", "`:1002`", "`:429-438`", "`:3162`", "`:66`"):
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


def test_a_duplicated_ref_cannot_stand_in_for_a_dropped_issue(tmp_path, monkeypatch):
    """FAILS if §6 counts OCCURRENCES in a row's list instead of DISTINCT refs.

    Reachable by a substitution, and a substitution is the shape a size/equality
    pin cannot see: swapping one issue for a duplicate of another keeps the row's
    total, so `count` still reconciles with its own list while the census has
    silently lost an issue. Measured on the pre-fix tool: the shipped
    `CLOSED COMPLETED` row with `#2725` replaced by a second `#2714` read
    `count=32 listed=32` over **31 distinct** issues and `check_state_arithmetic`
    returned `[]` — a fail-open, and the exact defect `MEMORY.md`'s
    `[pin-defensive-claim]` names ("PIN THE LIST BY SHAPE, NOT BY SIZE").
    """
    text = flc.read_doc(REPO)
    assert flc.check_state_arithmetic(text) == []
    duplicated = text.replace("incl. `#2714`, `#2725`", "incl. `#2714`, `#2714`", 1)
    assert duplicated != text, "the §6 anchor moved — re-derive this mutation"
    problems = flc.check_state_arithmetic(duplicated)
    assert any("more than once" in p and "#2714" in p for p in problems), problems
    # The count still reconciles, so the duplicate arm is the ONLY thing that can red:
    # an arm that also tripped the `listed != count` check would prove nothing new.
    assert not any("is not derived from the list" in p for p in problems), problems


def test_a_substitution_across_state_rows_is_rejected():
    """FAILS if §6 judges each state row IN ISOLATION instead of as one census.

    Reachable, and not covered by the same-row arm beside it: dropping `#2787` from
    `CLOSED other` while duplicating a `#2714` already listed in `CLOSED COMPLETED`
    leaves EVERY row reconciling with its own count (4/4, 32/32, 4/4, 2/2) and loses
    an issue from the four-row census — measured on the round-1 tool:
    `check_document()` returned `[]`. The four rows are one population, so the check
    is over their UNION.
    """
    text = flc.read_doc(REPO)
    assert flc.check_state_arithmetic(text) == []
    moved = text.replace(
        "`#1509` (`NOT_PLANNED`) · `#2787` (`NOT_PLANNED`)",
        "`#1509` (`NOT_PLANNED`) · `#2714` (`NOT_PLANNED`)",
        1,
    )
    assert moved != text, "the §6 anchor moved — re-derive this mutation"
    problems = flc.check_state_arithmetic(moved)
    assert any("more than once" in p and "CLOSED other" in p for p in problems), problems
    # No row's own count is disturbed, so only the union arm can be what reds here.
    assert not any("is not derived from the list" in p for p in problems), problems


def test_a_re_spelled_ref_cannot_hide_a_duplicate():
    """FAILS if §6 compares refs as raw STRINGS rather than as issue numbers.

    Reachable by a plain re-spelling: `#2714` and `#02714` are distinct strings, so a
    duplicate test over the raw match saw nothing while the census was one issue short
    (measured on the round-1 tool: `check_document()` `[]`). Normalising to the issue
    NUMBER — as a leading-zero-stripped digit STRING, never `int()` — makes the two
    spellings the same issue, which is what the row means.
    """
    text = flc.read_doc(REPO)
    assert flc.check_state_arithmetic(text) == []
    respelled = text.replace("incl. `#2714`, `#2725`", "incl. `#2714`, `#02714`", 1)
    assert respelled != text, "the §6 anchor moved — re-derive this mutation"
    problems = flc.check_state_arithmetic(respelled)
    assert any("more than once" in p and "#2714" in p for p in problems), problems
    assert not any("is not derived from the list" in p for p in problems), problems


def test_a_ref_the_index_never_carried_cannot_stand_in_for_one_it_dropped():
    """FAILS if the census is pinned by SIZE and UNIQUENESS instead of by IDENTITY.

    Reachable by a plain mistyping or mis-transcription (`#2734` -> `#2735`), which
    is how a number the ledger never indexed enters the table. Pinning the count
    could not see it: replacing `#2726` with `#9999` leaves every row reconciling
    with its own count, the sum at 42, the census at 42 distinct and no duplicate
    anywhere — measured on the round-2 tool, `check_document()` returned `[]` and
    `--check` exited 0 with an indexed issue gone from the population. The set is
    pinned by identity now, and the two directions are reported separately so the
    message names which number left and which number stood in for it.
    """
    text = flc.read_doc(REPO)
    assert flc.check_state_arithmetic(text) == []
    smuggled = text.replace("`#2726`", "`#9999`", 1)
    assert smuggled != text, "the §6 anchor moved — re-derive this mutation"
    problems = flc.check_state_arithmetic(smuggled)
    assert any("#2726" in p and "left the counted population" in p for p in problems), problems
    assert any("#9999" in p and "never indexed" in p for p in problems), problems
    # Every row still reconciles, so only the membership arms can be what reds here.
    assert not any("is not derived from the list" in p for p in problems), problems


def test_a_long_ref_cannot_crash_the_guard():
    """FAILS (traceback, exit 1) if a ref is converted through `int()`.

    Reachable: `re.findall(r"`#\\d+`")` matches any digit run, and Python refuses to
    convert an integer literal above 4,300 digits — so a `#` followed by 4,301 digits
    inside a state row raised `ValueError` out of `check_state_arithmetic`, past
    `main()`'s `except Unverifiable`, and the CLI exited **1**: the code §8 documents as
    "drift, a named claim is false", over a page that states no falsified claim and with
    a traceback rather than a named claim. Measured on the round-2 tool. Refs are
    compared as normalised digit STRINGS now, so no REF here is converted from text —
    the same class as the round-1 `UnicodeDecodeError` hole, and the reason the fix is
    "stop converting" rather than "catch it".

    The offending number is also an unindexed one, so it must be REPORTED — and the
    message must stay bounded, because a 4,301-digit number in a log is its own defect.
    """
    text = flc.read_doc(REPO)
    huge = "9" * 4301
    probe = text.replace("incl. `#2714`, `#2725`", f"incl. `#2714`, `#{huge}`", 1)
    assert probe != text, "the §6 anchor moved — re-derive this mutation"
    problems = flc.check_state_arithmetic(probe)  # must not raise
    assert any("never indexed" in p for p in problems), problems
    assert all(len(p) < 1000 for p in problems), [len(p) for p in problems]
    assert any("digits)" in p for p in problems), problems


#: Every position where an unbounded `\d+` run reaches an `int()` conversion, and what the
#: guard must say about it. ⛔ Parametrized rather than one case on purpose: the version
#: above mutates only a REF position, so it stayed GREEN while four other conversion sites
#: were live — a test asserting an invariant its own function violated (#11902 review round
#: 4). Anyone adding an `int()` on page-controlled text must add a row here.
_LONG_NUMBER_SITES = (
    (
        "a §6 state row's count cell",
        lambda t, h: t.replace("| **OPEN** | **4** |", f"| **OPEN** | **{h}** |", 1),
        "too long to convert",
    ),
    (
        "a citation's line span",
        lambda t, h: t.replace("`tortoise/pipeline.py:16`", f"`tortoise/pipeline.py:{h}`", 1),
        "is out of range",
    ),
    (
        # Document level only NO-CRASH here: the cell this replaces carries other evidence,
        # so the floor legitimately stays quiet. The REFUSAL is pinned directly by
        # `test_a_huge_section_reference_is_not_evidence` below.
        "a `§N` section reference in an evidence cell",
        lambda t, h: t.replace("`tortoise/pipeline.py:16`", f"`§{h}`", 1),
        None,
    ),
    (
        # No problem is expected here — an absurd heading number is simply not a section
        # anything resolves. What is forbidden is the CRASH, which is what this asserts.
        "a numbered heading",
        lambda t, h: t.replace(
            "## 7. What this changes", f"## {h}. A new section\n\n## 7. What this changes", 1
        ),
        None,
    ),
)


@pytest.mark.parametrize(
    ("site", "mutate", "expect"),
    _LONG_NUMBER_SITES,
    ids=[case[0] for case in _LONG_NUMBER_SITES],
)
def test_no_position_converts_an_unbounded_digit_run(site, mutate, expect):
    r"""FAILS (traceback, exit 1) if ANY `\d+` position is passed to `int()`.

    Python refuses to convert an integer literal above 4,300 digits, so any `int()` on a
    digit run a PAGE controls can raise `ValueError`. That escapes `main()`'s
    `except Unverifiable` and the CLI exits **1** — the code §8 documents as "drift, a
    named claim is false" — with a traceback, over a page that may state nothing at all.
    Measured live at four sites after the ref path was already fixed: the §6 count cell, a
    citation's line span, a `§N` reference, and a heading number.

    Each is refused BEFORE the conversion now, and every message is bounded so a
    4,301-digit number cannot flood a log either.
    """
    text = flc.read_doc(REPO)
    probe = mutate(text, "9" * 4301)
    assert probe != text, f"the {site} anchor moved — re-derive this mutation"
    problems = flc.check_document(probe)  # must not raise
    if expect is not None:
        assert any(expect in p for p in problems), (site, problems)
    assert all(len(p) < 1000 for p in problems), [(site, len(p)) for p in problems]


def test_a_huge_section_reference_is_not_evidence():
    """FAILS (traceback) if a `§`-reference is converted without a length bound.

    `SECTION_REF_TOKEN_RE` is `§\\s?(\\d+)` — an unbounded digit run — so `int(n)` on a
    4,301-digit section number raised `ValueError` out of the evidence floor, past
    `main()`'s `except Unverifiable`, and the CLI exited 1 (measured, round 4). A number
    that long cannot name a heading, so it is not evidence; the caller must REFUSE it
    rather than convert it, and the refusal is what makes the evidence floor red.
    """
    assert flc._has_evidence("`§" + "9" * 4301 + "`", {1, 2, 3}) is False
    # The negative control: a real section reference IS evidence, so the arm above is not
    # passing because `_has_evidence` refuses everything.
    assert flc._has_evidence("`§8`", {8}) is True


def _append_correction_rows(text: str, extra: int) -> str:
    """Append `extra` adopter rows directly to the END of §3's table.

    Directly — not after the closing prose. A row appended past a blank line forms a
    separate table and is invisible to `_table_rows`, which is how a probe can "prove"
    an unreachable crash (measured while verifying this finding: the first attempt
    appended after §3's takeaway paragraph and read 12 rows, not 18).
    """
    start = text.index("## 3. The corrections log")
    end = text.index("## 4. The artefact index")
    lines = text.split("\n")
    last = max(
        i for i, line in enumerate(lines)
        if start <= sum(len(x) + 1 for x in lines[:i]) < end
        and flc.CORRECTION_ROW_RE.match(line)
    )
    letters = [chr(ord("m") + k) for k in range(extra)]
    rows = [
        f"| **{letter}** | a claim | **ADOPTED** | **ADOPTED** | `tortoise/pipeline.py:16` | x |"
        for letter in letters
    ]
    return "\n".join(lines[: last + 1] + rows + lines[last + 1 :])


def test_a_thirteenth_correction_row_cannot_crash_the_tally(tmp_path, monkeypatch):
    """FAILS (IndexError, exit 1) if `COUNT_WORDS` is indexed bare.

    `CORRECTION_ROW_RE` accepts labels `a`-`z`, but `COUNT_WORDS` stops at `twelve`, so a
    thirteenth non-STILL-TRUE row made `COUNT_WORDS[13]` raise `IndexError` out of
    `check_document()`; `main()` catches only `Unverifiable`, so the CLI exited **1** —
    the code §8 reserves for "a named claim is false". Measured, review round 5. One of
    the four sites was already guarded inline and three were not, which is why 47/47
    self-test arms and every collected case stayed green over it.
    """
    text = flc.read_doc(REPO)
    grown = _append_correction_rows(text, 6)
    assert grown != text, "the §3 table anchor moved — re-derive this mutation"
    assert len(flc._verdicts_by_label(grown)) == 18, (
        "the six rows must land INSIDE §3's table, or this arm tests nothing"
    )
    problems = flc.check_document(grown)  # must not raise
    assert any("no longer read as written" in p for p in problems), problems


def test_a_path_the_filesystem_refuses_is_a_dangling_citation(tmp_path, monkeypatch):
    """FAILS (OSError, exit 1) if `Path.is_file()` is called bare on a citation path.

    `pathlib` swallows only `(ENOENT, ENOTDIR, EBADF, ELOOP)`, so `ENAMETOOLONG` — which a
    page-controlled path longer than `PATH_MAX` produces — is re-raised, and `main()`
    catches only `Unverifiable`: the CLI exited 1 with a traceback (measured, round 5).
    The two neighbouring arms already handled this, so these were the outliers. A path
    the filesystem refuses is a path the tree does not contain.
    """
    text = flc.read_doc(REPO)
    long_path = "a" * 5000
    probe = text.replace("`tortoise/pipeline.py:16`", f"`{long_path}.py:16`", 1)
    assert probe != text, "the citation anchor moved — re-derive this mutation"
    problems = flc.check_document(probe)  # must not raise
    assert any("does not exist in the tree" in p for p in problems), problems
    # And the helper itself, so the assertion is not passing on an unrelated red.
    assert flc._is_file(flc.REPO / (long_path + ".py")) is False


def test_the_pinned_population_is_a_membership_set_not_a_count():
    """FAILS if `PINNED_INDEXED` and `PINNED_INDEXED_ROWS` can drift apart.

    The count must be DERIVED from the set, the same discipline
    `PINNED_CORRECTION_LABELS` follows: two independent declarations are two places a
    shrink has to touch, and the point of pinning identity is that there is only one.
    """
    assert len(flc.PINNED_INDEXED) == flc.PINNED_INDEXED_ROWS, (
        "PINNED_INDEXED_ROWS must be len(PINNED_INDEXED) so a count and a set cannot disagree"
    )
    assert len(flc.PINNED_INDEXED) == 42, (
        "this line RECORDS the indexed population (40 issues + 2 PRs); re-derive it with "
        "the set literals in §6's four state rows, never by carrying this number forward"
    )


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
        # ⛔ Target the sentence's OPENING clause (`of the four survivors,`) — the clause
        # `SURVIVOR_RE` keys on — not the qualifier that follows it: a target quoting
        # `the two survivor bullets below` becomes a no-op as soon as that phrase is
        # reworded, and the arm then dies behind `_mutated`'s own "mutation did not change
        # the document" assert (#11902 review; the tool's own arm had this shape).
        return t.replace(
            "of the four survivors,",
            "of the four survivors, three are already re-measured, and",
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
        if "--is-shallow-repository" in cmd:
            # A FULL clone — the only state in which status 1 certifies drift.
            # `check_measured_commit` asks this AFTER a status-1 answer, so the
            # stub has to answer it or the arm under test never completes.
            return _Result(0, "false\n")
        if "rev-parse" in cmd:
            return _Result(0, "f70d884b7f51\n")
        return _Result(1)  # merge-base --is-ancestor: exit 1 == NOT an ancestor

    monkeypatch.setattr(flc, "subprocess", SimpleNamespace(run=fake_run))
    problems = flc.check_measured_commit(flc.read_doc(REPO), REPO)
    assert any("NOT an ancestor of HEAD" in p for p in problems), problems
    assert any("--is-ancestor" in cmd for cmd in calls), calls
    assert any("--is-shallow-repository" in cmd for cmd in calls), calls


def test_measured_commit_not_ancestor_is_unverifiable_in_a_shallow_clone(monkeypatch):
    """FAILS when a shallow/grafted checkout's status-1 answer is read as DRIFT.

    Reachable by running in a `--depth 1` clone, or in a DEEPENED shallow clone:
    the pinned commit's object is present, so `rev-parse --verify` succeeds, but
    the graft boundary cuts the walk and `merge-base --is-ancestor` exits 1 for a
    REAL ancestor. That is the same status an unrelated tree produces, so the
    answer cannot be told apart from a truncated walk — reporting it accuses a
    correct page of "a cut it did not make", which is the false red
    `_history_available()` already refuses to accredit the TEST with (#10356).

    The sibling `tools/substrate_map_check.py` states the rule: ⛔ ONLY `False`
    certifies drift. `git` is stubbed because a shallow pytest checkout cannot
    manufacture a genuine graft (`--depth 1` produces an ABSENT object instead,
    which dies earlier at `rev-parse --verify` and takes the same exit-2 path for
    a different reason), so the stub is the only way to isolate THIS branch.
    """
    class _Result:
        def __init__(self, returncode: int, stdout: str = "") -> None:
            self.returncode, self.stdout, self.stderr = returncode, stdout, ""

    # SECOND: this checkout is a FULL clone, so the real arm still certifies the
    # shipped page rather than degrading to UNVERIFIED. Otherwise this fix would
    # have turned a false red into a permanent `exit 2` on every box, which is its
    # own kind of gate failure. Gated on history availability because the `test`
    # jobs checkout at depth 1 — where this arm is inert by design (the same reason
    # the two other ancestry assertions skip), while the stubbed half above runs
    # everywhere.
    if _history_available():
        assert flc.check_measured_commit(flc.read_doc(REPO), REPO) == []

    for state in ("true\n", ""):  # shallow, and an unanswerable probe
        # `_state` binds the loop variable at definition time (ruff B023): the
        # closure is installed on the tool's `subprocess` and called later, so a
        # bare `state` would read whatever the loop left behind.
        def fake_run(cmd, *args, _state=state, **kwargs):
            if "--is-shallow-repository" in cmd:
                return _Result(0, _state)
            if "rev-parse" in cmd:
                return _Result(0, "f70d884b7f51\n")
            return _Result(1)  # a real ancestor, cut off by the graft boundary

        monkeypatch.setattr(flc, "subprocess", SimpleNamespace(run=fake_run))
        with pytest.raises(flc.Unverifiable):
            flc.check_measured_commit(flc.read_doc(REPO), REPO)


def test_unreadable_document_is_unverifiable(monkeypatch):
    """FAILS (exit 2) when the document cannot be read.

    Reachable by deleting the file. A guard that cannot run must not report ok.
    """
    monkeypatch.setattr(flc, "DOC", "/nonexistent/ledger.md")
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


def test_undecodable_document_is_unverifiable_not_drift(tmp_path, monkeypatch):
    """FAILS (exits 1, "drift") when the document holds bytes that are not UTF-8.

    Reachable: any commit that lands a stray non-UTF-8 byte in the page. This is
    NOT the same class as the ENOENT arm above and the arm above does not cover it —
    `read_text(encoding="utf-8")` raises `UnicodeDecodeError`, a `ValueError` and
    **not** an `OSError`, so an `except OSError` misses it entirely and the traceback
    reaches `main()`'s top-level handler, which exits **1** — the code §8 reserves for
    "a named claim is false". Measured on the pre-fix tool: exit 1, a traceback, and no
    claim named, over a page nobody faulted. `MEMORY.md`'s
    `[unicode-decode-is-not-oserror]` row records exactly this shape and the remedy.

    The fixture writes RAW BYTES on purpose: a `"{not json"`-style fixture is valid
    UTF-8 and stays green over the whole class (the same reason that row records).
    """
    target = tmp_path / "ledger.md"
    target.write_bytes(
        DOC.read_text(encoding="utf-8").encode("utf-8") + b"\xff\xfe"
    )
    monkeypatch.setattr(flc, "DOC", str(target))
    with pytest.raises(flc.Unverifiable) as excinfo:
        _doc_problems()
    # The reason must be the READ failure, not some later arm: a bare `raises` over a
    # page that also lost its citations would pass for the wrong reason.
    assert "cannot read" in str(excinfo.value), excinfo.value
    assert "decode" in str(excinfo.value).lower(), excinfo.value
    # And end to end: the CLI must refuse with 2, never the drift code 1.
    assert flc.main(["--check", "--root", str(REPO)]) == 2


def test_undecodable_index_is_unverifiable_not_drift(tmp_path, monkeypatch):
    """FAILS (exits 1) when `docs/00_index.md` holds non-UTF-8 bytes.

    The same `UnicodeDecodeError`-is-not-`OSError` hole as the document arm, one
    `read_text` site over in `check_registration()`: measured on the pre-fix tool,
    an undecodable index exited **1** with a traceback instead of 2 UNVERIFIED.
    """
    target = tmp_path / "00_index.md"
    target.write_bytes(INDEX.read_text(encoding="utf-8").encode("utf-8") + b"\xff\xfe")
    monkeypatch.setattr(flc, "INDEX", str(target))
    with pytest.raises(flc.Unverifiable) as excinfo:
        flc.check_registration(REPO)
    assert "cannot read" in str(excinfo.value), excinfo.value


def test_missing_section_is_unverifiable(tmp_path, monkeypatch):
    """FAILS (exit 2) when a checked section heading is gone.

    Reachable by renumbering §3. An absent section is the strongest drift, and
    silently checking nothing would report a clean tree over an empty check.
    """
    _mutated(tmp_path, monkeypatch, lambda t: t.replace("## 3. The corrections log", "## 3. Corrections"))
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


@pytest.mark.parametrize("heading", flc.SECTION_HEADINGS)
def test_every_section_is_required_not_just_the_bounded_ones(tmp_path, monkeypatch, heading):
    """FAILS (exit 2) if ANY of the eight sections can be deleted into greenness.

    Reachable, and measured before this arm existed: §1–§4 and §6–§7 were each
    bounded by a `_region()` pair and refused when renumbered away, but §5 and §8
    were bounded by nothing — deleting either WHOLE returned `check_document() ==
    []`, i.e. `--check` at rc 0 over a page missing one of its two headline halves.
    §5 is the at-risk inventory the review's own conclusion rests on, and prose
    outside it cites it; §8 is the page's statement of what IS checked, so its
    absence silently widens every other claim on the page.

    The section is removed by cutting its heading and body up to the next heading,
    which is what a lane "simplifying" the page would actually commit — not just a
    renumbered heading, so this covers the loss the `_region` arms never saw.
    """
    def drop_section(text: str) -> str:
        start = text.find(heading)
        assert start >= 0, heading
        rest = text[start + len(heading) :]
        nxt = rest.find("\n## ")
        assert nxt >= 0 or start + len(heading) + len(rest) == len(text)
        return text[:start] + (rest[nxt + 1 :] if nxt >= 0 else "")

    _mutated(tmp_path, monkeypatch, drop_section)
    with pytest.raises(flc.Unverifiable):
        _doc_problems()


def test_an_unanswerable_git_is_unverifiable_not_drift(monkeypatch, capsys):
    """FAILS if a `git` that cannot be RUN reports DRIFT (1) instead of UNVERIFIED (2).

    Measured before this arm: `env PATH=/tmp/nogit python3
    tools/findings_ledger_check.py --check` escaped as a `FileNotFoundError`
    traceback and Python's interpreter exit — **1** — which this module defines as
    "drift: a named claim is false, and the message names the claim". There is no
    claim: nothing opened the document's git question at all, so a cell wired to
    that exit code would red a correct page. The sibling map's `_git` exists for
    exactly this (`tools/problem_map_check.py`), so the refusal shape is the
    recorded one. `PermissionError` and `TimeoutExpired` take the same path.
    """
    def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(flc.subprocess, "run", boom)
    with pytest.raises(flc.Unverifiable) as excinfo:
        flc.check_measured_commit(flc.read_doc(REPO), REPO)
    assert "could not be run" in str(excinfo.value)
    assert "FileNotFoundError" in str(excinfo.value)


def test_main_exit_two_when_git_cannot_be_run(monkeypatch, capsys):
    """FAILS if the CLI turns an unaskable `git` into exit 1 (or a traceback).

    Same defect as the test above, asserted at the CONTRACT boundary the `docs`
    job reads: `2` says the checker could not look, `1` accuses the document.
    """
    def boom(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(flc.subprocess, "run", boom)
    assert flc.main(["--check", "--root", str(REPO)]) == 2
    assert "UNVERIFIED" in capsys.readouterr().err


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


def _third_party_imports(source: str) -> tuple[list[str], list[str]]:
    """Third-party modules a checker source imports, and dynamic imports it cannot verify.

    Returns `(third_party, unverifiable)`. `ast.Import`/`ast.ImportFrom` cover the
    static spellings; a CALL to `__import__`/`import_module` covers the dynamic ones
    (`importlib.import_module("yaml")` was invisible to a walk that only visited
    import nodes — measured, the test stayed green while the module required
    pyyaml). A dynamic import whose target is NOT a literal cannot be resolved
    statically, so it is reported as unverifiable rather than assumed harmless: a
    guard that cannot see the target has not measured it.

    ⚠️ KNOWN ESCAPES — measured, and named rather than left looking covered, because
    that is the "comment claims coverage the walk does not have" class this scan
    exists to close. These resolve to NOTHING here and are SILENT, not unverifiable:
    an alias (`from importlib import import_module as im; im("yaml")`), a bound method
    (`im = importlib.import_module; im("yaml")`), `getattr(importlib,
    "import_module")("yaml")`, `importlib.__dict__["import_module"]("yaml")`, and
    `__builtins__["__import__"]("yaml")`, and a target handed to `eval`/`exec`
    (`eval("__import__('yaml')")`). Each is a plain Name/Attribute/Subscript/Call
    node the walk already visits — it simply does not classify them, and closing
    them needs a call graph (or, for `eval`, a parser of program text carried in a
    string) rather than an AST walk. They are listed so the next reader can tell a
    measured limit from an unseen one; the list is the boundary of the scan, not a
    claim that everything outside it is covered.
    """
    imported: set[str] = set()
    unverifiable: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if name not in ("__import__", "import_module"):
                continue
            target = node.args[0] if node.args else None
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                imported.add(target.value.split(".")[0])
            else:
                unverifiable.append(
                    f"line {node.lineno}: `{name}(…)` with a non-literal target "
                    "— the module cannot be resolved without running the code"
                )
    third_party = sorted(imported - set(sys.stdlib_module_names) - {"__future__"})
    return third_party, unverifiable


def test_the_stdlib_scan_sees_every_import_spelling():
    """FAILS if any import spelling can carry a third-party package past `core`.

    The substring version this replaced could not see `from yaml import safe_load`;
    the AST version could not see `importlib.import_module("yaml")` or
    `__import__("yaml")` — both measured green over a checker that genuinely
    required pyyaml, while the comment above the walk named that exact spelling as
    covered. Each spelling is asserted here so a future rewrite of the scan has to
    keep covering all of them, and a NON-literal call must report unverifiable
    rather than pass: an unresolved target is not a clean one.
    """
    for spelling in (
        "import yaml\n",
        "import os, yaml\n",
        "from yaml import safe_load\n",
        "import importlib\nimportlib.import_module('yaml')\n",
        "__import__('yaml')\n",
    ):
        third_party, unverifiable = _third_party_imports(spelling + "\n")
        assert third_party == ["yaml"], (spelling, third_party)
        assert unverifiable == [], (spelling, unverifiable)
    third_party, unverifiable = _third_party_imports(
        "import importlib\nimportlib.import_module(name)\n"
    )
    assert third_party == [], third_party
    assert len(unverifiable) == 1, unverifiable
    assert "non-literal" in unverifiable[0], unverifiable
    # The real checker must be clean on BOTH legs, not just the literal one.
    third_party, unverifiable = _third_party_imports(
        (REPO / "tools" / "findings_ledger_check.py").read_text(encoding="utf-8")
    )
    assert third_party == [], third_party
    assert unverifiable == [], unverifiable


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
    # ⛔ AST, not a substring scan. The four-literal version this replaced could not
    # see `from yaml import safe_load` or `import os, yaml`, and the AST version that
    # replaced it could not see `importlib.import_module("yaml")` — measured: the test
    # stayed green in every one of those cases while the docstring claimed it "FAILS
    # if the checker grows a third-party import". `_third_party_imports` covers the
    # import statements AND a `__import__`/`import_module` call with a LITERAL target,
    # and reports a non-literal target as unverifiable instead of clearing it.
    # ⚠️ The reaches it still misses are enumerated in that helper's docstring: an
    # aliased module, a bound method, a subscripted `__dict__`, `getattr`,
    # `__builtins__[…]`, and a target handed to `eval`/`exec`. They ARE reachable —
    # they are simply not classified — so they are named rather than implied away, and the `--self-test` leg below is a smoke
    # probe, not a second opinion.
    third_party, unverifiable = _third_party_imports(source)
    assert third_party == [], (
        f"the checker must be stdlib-only (the `docs` job runs it beside a `pip "
        f"install -e .`, whose absence must never break the guard); found {third_party}"
    )
    assert unverifiable == [], (
        "every dynamic import in the checker must name its target literally, or this "
        f"test cannot tell whether it is stdlib-only: {unverifiable}"
    )
    assert subprocess.run(
        [sys.executable, str(REPO / "tools" / "findings_ledger_check.py"), "--self-test"],
        cwd=REPO, capture_output=True, check=False,
    ).returncode == 0


def test_a_tilde_citation_outside_the_swarm_tree_is_refused(tmp_path, monkeypatch):
    """FAILS if a `~`-rooted citation anywhere is exempted from the resolver.

    The arm exempted every `~` path, so prefixing any citation with one character
    defused every red this resolver can raise: measured, `~tortoise/nope.py:16` (a
    path that does not exist) and `~tortoise/pipeline.py:999999` (a line past the
    end of its file) both returned `[]`. A citation the guard cannot open must not
    be counted as checked.

    ⛔ The carve-out is now EMPTY, not narrowed to `~/.swarm/`. Narrowing was tried
    (and measured to still exempt a `~/.swarm/…:3` citation of a file §5 records as
    **gone** — the one class this page exists to stop a lane re-citing), so §8 no
    longer declares any `~` carve-out and every `~`-rooted citation is refused.
    """
    for spelling in ("~tortoise/nope_module.py:16", "~tortoise/pipeline.py:999999"):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _s=spelling: t.replace(
                    "`tortoise/pipeline.py:16`", f"`{_s}`", 1
                ),
            )
            problems = _doc_problems()
            assert any("does not exist in the tree" in p for p in problems), (
                f"{spelling!r} is `~`-rooted and must be refused, "
                f"not exempted: {problems}"
            )


def test_a_tilde_citation_into_the_lost_swarm_tree_is_refused(tmp_path, monkeypatch):
    """FAILS if the `~/.swarm/`-only exemption ever comes back.

    This is the NARROWED version of the fail-open, and it is the tempting one: §5's
    inventory names `~/.swarm/issue-triage-2026-09-09.md`, so exempting that tree
    looks like protecting a legitimate §5 reading. It is not — §5 records that tree
    as **gone from every machine measured**, which makes a `~/.swarm/…:LINE`
    citation an assertion of evidence nobody can open: exactly the "declares what it
    cannot store" class this page exists to catch, and the reason its own first
    consequence says such a citation is a BROKEN citation. The name with no line
    number is a table cell, not a citation, and stays untouched.
    """
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                "`tortoise/pipeline.py:16`",
                "`~/.swarm/issue-triage-2026-09-09.md:3`",
                1,
            ),
        )
        problems = _doc_problems()
        assert any("does not exist in the tree" in p for p in problems), problems

def test_a_two_digit_section_reference_must_name_a_real_heading(tmp_path, monkeypatch):
    """FAILS if `§N` is read as a single digit.

    `SECTION_REF_TOKEN_RE` matched one digit and DISCARDED the rest, so `§10`
    parsed as `§1` — a section the page has — and every two-digit reference to a
    section that does not exist was accepted as evidence (measured: an evidence
    cell rewritten to `§10` left `check_document()` at `[]`). Existence is only a
    check if the whole number is read.
    """
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                "| §6 below, measured per issue |",
                "| §10 below, measured per issue |",
                1,
            ),
        )
        problems = _doc_problems()
        assert any("names no `path:line`, issue, command or §N" in p for p in problems), (
            f"a `§10` evidence cell names no heading this page has: {problems}"
        )


def test_a_non_verdict_row_in_s1_is_refused(tmp_path, monkeypatch):
    """FAILS if §1's table can carry a row that is not a `**VERDICT**` row.

    `check_vocabulary()` compared only the rows that MATCH its pattern, so a row
    shaped any other way was invisible: measured, inserting
    `| SUPERSEDED-BY-DRIFT | … |` left the arm empty and `--check` at rc 0 — a
    sixth claim class with no handling rule, in the one table §8 names as the
    authority for the vocabulary §3 is checked against.
    """
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                "| **ADOPTED** |",
                "| SUPERSEDED-BY-DRIFT | whatever |\n| **ADOPTED** |",
                1,
            ),
        )
        problems = _doc_problems()
        assert any("is not a `**VERDICT**` row" in p for p in problems), (
            f"an unhandled claim class in §1 must red: {problems}"
        )


def test_a_s1_row_starting_with_the_word_verdict_is_still_refused(tmp_path, monkeypatch):
    """FAILS if §1's header skip is a prefix test on the word `Verdict`.

    The skip exists for the ONE header line, but `row.startswith("| Verdict")`
    exempted every row whose first cell began with that word — so the arm beside it
    was defeated by a plural: measured, `| Verdicts | a | b |`,
    `| Verdict-ish | a | b |` and `| Verdict-and-more | … |` each left
    `check_document()` at `[]` while the plain non-verdict row reded. The header is now
    the FIRST `|` row whose first cell reads `Verdict` — position AND shape, because
    position alone was itself a fail-open (a substituted first row went CLEAN).
    """
    for row in ("| Verdicts | a | b |", "| Verdict-ish | a | b |"):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _r=row: t.replace(
                    "| **ADOPTED** |", f"{_r}\n| **ADOPTED** |", 1
                ),
            )
            problems = _doc_problems()
            assert any("is not a `**VERDICT**` row" in p for p in problems), (
                f"{row!r} is not the header and must be refused: {problems}"
            )
    # The header itself cannot be dropped: the first `|` row is then a verdict row.
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                "| Verdict | Means | What a lane should do |\n|---", "|---", 1
            ),
        )
        problems = _doc_problems()
        assert any(
            "has no header row" in p or "is not the table header" in p for p in problems
        ), (
            f"a header-less §1 table must red: {problems}"
        )


def test_a_s1_first_row_that_is_not_the_header_is_refused(tmp_path, monkeypatch):
    """FAILS if §1's header slot is exempted by POSITION rather than by shape.

    Skipping the first `|` row unconditionally exempted whatever stood there, so a
    claim class substituted for the shipped header — `| SUPERSEDED-BY-DRIFT | Means |
    What a lane should do |` — left `check_document()` at `[]` while the same row one
    line lower reded (measured, #11902 review P1). The exemption is the header's
    shape (`| Verdict | … |`), so a substituted first row reds too, and the shipped
    page still passes because its first row IS that header.
    """
    for row in (
        "| SUPERSEDED-BY-DRIFT | Means | What a lane should do |",
        "| not-a-header | Means | What a lane should do |",
        "| FIXED-IT | a | b |",
        # The PLURAL is the case a substring test would wave through: the shape
        # test refuses it because `Verdict` must be followed by the cell edge.
        "| Verdicts | Means | What a lane should do |",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _r=row: t.replace(
                    "| Verdict | Means | What a lane should do |", _r, 1
                ),
            )
            problems = _doc_problems()
            assert any("is not the table header" in p for p in problems), (
                f"{row!r} in the header slot must be refused: {problems}"
            )
    # The shipped header is the exemption, so the real page stays clean.
    assert not [p for p in _doc_problems() if "table header" in p], (
        "the shipped header must not be refused"
    )


def test_a_sentence_quoting_the_tally_phrases_is_not_counted(tmp_path, monkeypatch):
    """FAILS if a sentence that merely QUOTES a tally phrase is counted as a second one.

    The exactly-one arms locate their subject by phrase, so counting every sentence
    that contains one red an edit that only talked ABOUT the phrase: measured,
    appending `(The phrase "still read as written" names the complement.)` produced
    `§3 carries 2 sentences saying 'still read as written'` while a single sentence
    asserted anything. A candidate must also carry a LETTER LIST — two or more single letters, comma-joined
    or in a pair, each written bare, bold, code or italic, every form bounded to the
    page's alphabet `a`–`l`; requiring MARKUP rather than a list was itself fail-open
    (measured), and markup around the PARENTHESES does not decide candidacy either
    (`**(c)**` and `(c)` are one item). A single parenthetical letter in prose is not
    a list. A sentence with
    no letter list in it cannot disagree with the table.
    """
    for quote in (
        ' stated both ways.) (The phrase "still read as written" names the complement.) ',
        ' stated both ways.) The words "no longer read as written" are the arm\'s subject. ',
        # ⛔ A SINGLE-QUOTED span must be stripped too. Dropping the `'`-delimited half of
        # `quoted` leaves every gate green and makes this legitimate quotation a RED
        # (measured at e5a7ba2f) — the false-accusation direction, while the possessive
        # test pins the opposite one.
        " stated both ways.) They wrote 'seven of the twelve rows no longer read as written — **(c)**, **(d)**' verbatim. ",
    ):
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _q=quote: t.replace(" stated both ways.) ", _q, 1),
            )
            problems = _doc_problems()
            assert not any(
                "sentences saying 'still read as written'" in p
                or "sentences saying 'no longer read as written'" in p
                for p in problems
            ), f"a sentence quoting the phrase must not be counted: {problems}"


def test_a_quoted_letter_list_is_still_a_candidate(tmp_path, monkeypatch):
    """FAILS if the quote strip is applied to the LETTERS as well as the phrase.

    Removing quoted spans before the phrase test is what stops a sentence that merely
    QUOTES the phrase from being counted. Feeding the same stripped text to the letter
    test went one step too far: a quoted letter LIST then became invisible, so a stale
    takeaway spelled `… no longer read as written: "**(b)**, **(k)** and **(l)**".`
    read CLEAN beside one compliant decoy while the parent refused it (measured). A
    quote around the PHRASE names the words; a quote around a LETTER still names that
    letter.
    """
    anchor = "is the exact defect #2795 D2 fixed. "
    decoy = 'Six of the twelve rows no longer read as written: "**(b)**, **(k)** and **(l)**". '
    with monkeypatch.context() as patch:
        _mutated(tmp_path, patch, lambda t: t.replace(anchor, f"{anchor}{decoy}", 1))
        problems = _doc_problems()
        assert any(
            "sentences saying 'no longer read as written'" in p for p in problems
        ), f"a QUOTED letter list is still a letter list: {problems}"


#: §3's shipped complement sentence VERBATIM, so a test can replace it whole instead of
#: patching a fragment and passing for the wrong reason.
_STILL_SENTENCE = (
    "Only **(a)**, **(f)**,\n**(h)**, **(i)** and **(j)** still read as written."
)


def test_english_prose_cannot_supply_a_missing_member(tmp_path, monkeypatch):
    """FAILS if ordinary English supplies a row letter to either tally list.

    A STALE complement list — one that omits a row the table calls STILL-TRUE — is the
    one thing the complement arm exists to catch, and short prose was enough to close
    the gap: `, i.e. today.` supplied `i`, `, and I think so.` supplied `i`, `, or i
    think.` supplied `i`, `, and a claim §1 supports.` supplied `a` (`i` and `a` are both
    STILL-TRUE in the shipped table), and `, e.g. today.` supplied `e`. Every one of them
    read CLEAN over a page whose complement list contradicts its own table, and the
    no-tail control REDs — so the tail was the cause, not a coincidence.

    ⛔ EACH SHAPE IS ITS OWN SUB-CASE, WITH ITS OWN REMOVAL. The previous version asserted
    three tails against ONE removal, so only the first could discriminate: the removal
    was `(a)` and every tail supplied `i`/`e`, which never closes a set that is already
    short by `a` (#11902 review P2, measured). A case that is RED in both worlds pins
    nothing.
    """
    #: (label, replacement sentence) — the REMOVAL matches the phantom each tail supplies,
    #: so a fix that stops reading that phantom REDs and a regression CLEANs.
    cases = (
        (
            ", i.e. today.",
            "Only **(a)**, **(f)**,\n**(h)** and **(j)** still read as written, i.e. today.",
        ),
        (
            ", and I think so.",
            "Only **(a)**, **(f)**,\n**(h)** and **(j)** still read as written, and I think so.",
        ),
        (
            ", or i think.",
            "Only **(a)**, **(f)**,\n**(h)** and **(j)** still read as written, or i think.",
        ),
        (
            ", and a claim §1 supports.",
            "Only **(f)**,\n**(h)**, **(i)** and **(j)** still read as written"
            ", and a claim §1 supports.",
        ),
        # ⛔ AND THE PHANTOM THAT *IS* CONNECTED TO THE RUN. The four shapes above supply
        # their letter across a sentence's worth of words, so the run rule alone refuses
        # them, and the ENDING rule was left with no case at all — measured: disabling it
        # outright kept the suite green. These two insert the prose INSIDE the list, so the
        # phantom is a run member and only the ending rule can refuse it (`i.e.` is refused
        # because a full stop followed by a letter does not end a member; `and I` because
        # `think` is not a predicate).
        # ⛔ WHAT THE ENDING RULE ACCEPTS IS THE SENTENCE'S OWN ENDING, NOT LIST PUNCTUATION:
        # a full stop that does not begin an abbreviation, the end of the sentence, a
        # closing parenthesis, a colon, an em/en dash, or one of the arm's own predicates as
        # a whole word. A comma is deliberately NOT accepted (a comma after the last item
        # means the run had not ended), and neither is a semicolon — both are run separators,
        # so if no item follows them, the list is over and prose continues.
        (
            ", and I think so, inside the run",
            "Only **(a)**, **(f)**,\n**(h)** and **(j)**, and I think so, "
            "still read as written.",
        ),
        (
            ", i.e. today, inside the run",
            "Only **(a)**, **(f)**,\n**(h)** and **(j)**, i.e. today, "
            "still read as written.",
        ),
        # ⛔ AND THE PHANTOM THE REVIEWER FOUND ONE LEVEL FURTHER IN (#11902 review): an
        # `a`/`i` in an INTERIOR run position is not the run's tail, so an ending test
        # applied only there left it with NO evidence test at all — `, and I,` and
        # `, and a,` closed a stale list while reading CLEAN. A connector word beside the
        # letter is what makes those prose; the documented bare spelling carries only
        # punctuation (`a, f, h, i, j`).
        (
            ", and I, in an INTERIOR position",
            "Only **(a)**, **(f)**,\n**(h)**, and I, **(j)** still read as written.",
        ),
        (
            ", and a, in an INTERIOR position",
            "Only **(f)**,\n**(h)**, **(i)**, and a, **(j)** still read as written.",
        ),
    )
    for shape, replacement in cases:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, r=replacement: t.replace(_STILL_SENTENCE, r, 1),
            )
            problems = _doc_problems()
            assert any(
                "STILL-TRUE list names" in p for p in problems
            ), f"English prose must not supply a member: {shape!r} -> {problems}"

    #: ⛔ AND THE SHAPES THE SET COMPARISON CANNOT DISCRIMINATE. `e.g.` supplies `e`, and
    #: `e` is ADOPTED rather than STILL-TRUE, so the still arm REDs either way and a
    #: document-level assertion pins nothing. `e` becomes a live phantom the moment a row
    #: moves to STILL-TRUE — this page's normal fate — so the extractor is asserted
    #: directly: prose contributes NO letter at all.
    for prose in (
        "i.e. today",
        "e.g. today",
        "and I think so",
        "and a claim §1 supports",
        "or i think",
        "and as a rule the row moved",
    ):
        assert flc._named_letters(
            f"Only **(a)** still read as written, {prose}."
        ) == {"a"}, f"prose must contribute no row letter: {prose!r}"

    #: ⛔ AND THE SAME QUESTION ABOUT THE OTHER SIDE OF A LIST ITEM. Which side is evidence
    #: depends on the position: a run's TAIL is decided by what FOLLOWS it, so a bare
    #: `a`/`i` whose LEFT neighbour is a comma must not be read just because a comma sits
    #: there — measured, an ordering that tested the left side first read a phantom `i`
    #: out of `Only a, f, h, i foo bar.` and `Only a, f, h, i, and then some.` (found by
    #: the verification gate on the first attempt at this rule).
    for sentence in (
        "Only a, f, h, i foo bar.",
        "Only a, f, h, i think so.",
        "Only a, f, h, i, and then some.",
    ):
        assert flc._named_letters(sentence) == {"a", "f", "h"}, f"prose must not supply the trailing `i`: {sentence!r}"

    #: …while every documented spelling keeps reading EVERY member, including the ones
    #: that only work because a position rule is applied the right way round.
    for sentence, expected in (
        ("Only a, f, h, i, j still read as written.", {"a", "f", "h", "i", "j"}),
        ("Only a, f, h, i and j still read as written.", {"a", "f", "h", "i", "j"}),
        # ⛔ A CONNECTOR-JOINED RUN IS A LIST TOO (§8). An INTERIOR rule that accepted only
        # punctuation-only separators dropped the `i` here and REDed a correct page —
        # measured by #11902's review round 3 on the previous head, which read all five.
        # Both homogeneous forms are evidence; a MIXTURE (`, and I,`) is prose.
        ("Only a and f and h and i and j still read as written.", {"a", "f", "h", "i", "j"}),
        ("Only a and i and j still read as written.", {"a", "i", "j"}),
        ("Only a as well as i as well as j still read as written.", {"a", "i", "j"}),
        ("Only a followed by i followed by j still read as written.", {"a", "i", "j"}),
        ("Only a, f, h, i remain STILL-TRUE.", {"a", "f", "h", "i"}),
        ("Only a, f, h, i) still read as written.", {"a", "f", "h", "i"}),
        ("Note: j or a no longer read as written.", {"a", "j"}),
    ):
        assert flc._named_letters(sentence) == expected, f"a documented spelling must read every member: {sentence!r}"


def test_a_written_bare_member_is_still_read(tmp_path, monkeypatch):
    """FAILS if the guard refuses a spelling §8 declares accepted.

    The ending rule that stopped prose supplying a phantom member was also too tight in
    the other direction at `e5a7ba2f4` (#12028):

    * `Only a, f, h, i, j still read as written.` — the comma-joined BARE spelling —
      yielded `['a','f','h','i']` and RED, because the trailing `j` is followed by ` still`
      and not by the OLD rule's list punctuation. It was CLEAN one commit earlier: the
      fix introduced the
      false accusation.
    * `Only **(a)**, **(f)**, **(h)**, **(i)**, c and **(j)** still read as written.` read
      CLEAN while claiming a SUPERSEDED-BY-FIX row as still: the bare `c` before ` and`
      was dropped, so the compared set silently EQUALLED the table's.

    So one rule was too loose and too tight at once. Membership is now decided by the list
    RUN (items joined by separators only), which reads the trailing member and the bare
    member inside a mixed run, and refuses a single item that follows prose.
    """
    accepted = (
        "Only a, f, h, i, j still read as written.",
        "Only a, f, h, i and j still read as written.",
        "Only **(a)**, **(f)**, **(h)**, **(i)**, also j still read as written.",
        "Only **(a)**, **(f)**, **(h)**, **(i)** and **(j)** still read as written.",
    )
    for replacement in accepted:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, r=replacement: t.replace(_STILL_SENTENCE, r, 1),
            )
            problems = _doc_problems()
            assert not any(
                "STILL-TRUE list names" in p for p in problems
            ), f"a documented spelling must read: {replacement!r} -> {problems}"

    refused = (
        # the member the run MUST read: `c` is SUPERSEDED-BY-FIX, so naming it as still
        # is a contradiction, and dropping it was the fail-open
        "Only **(a)**, **(f)**, **(h)**, **(i)**, c and **(j)** still read as written.",
        "Only **(a)**, **(f)**, **(h)**, **(i)**, c, **(j)** still read as written.",
    )
    for replacement in refused:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, r=replacement: t.replace(_STILL_SENTENCE, r, 1),
            )
            problems = _doc_problems()
            assert any(
                "STILL-TRUE list names" in p for p in problems
            ), f"a bare member inside a run must be read: {replacement!r} -> {problems}"


def test_a_second_run_in_the_same_sentence_supplies_members_too() -> None:
    """Membership is sentence-global — stated as an OVER-READING, pinned from the reading side.

    This is the FAIL-OPEN side of the same mechanism whose contract side is
    `test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling`: a TWO-ITEM
    letter run in prose later in the sentence contributes its members. Measured on the real
    page: a complement list that OMITS `i`, closed with `; see h, i.`, reads CLEAN, because
    `h, i` is a second run and its tail (`i`, followed by a full stop) satisfies the ending
    rule — a maintenance edit that appends a cross-reference un-stales the list.

    ⛔ SCOPING THE ARM TO THE FIRST RUN IS REFUSED, and this test says so: it would close this
    hole and break two pins further down this file, which require a letter added in a LATER clause
    (`… **(l)** are **DRIFTED**, and a, f too.`) to be compared. Identical bytes, opposite
    verdicts, so the choice is a TRADE and the residual census states it. If a future change
    scopes the arm, that change must invert this assertion AND move the census in
    `tools/findings_ledger_check.py` and the page — drifting silently is what this pins against.
    """
    # The reading side: a later run contributes.
    assert flc._named_letters("Only a, f, h still read as written; see h, i.") == {
        "a",
        "f",
        "h",
        "i",
    }, "a second run's members must be read (and the census must say so)"
    # The control: ONE-item runs still contribute nothing on either side.
    assert flc._named_letters("Only a, f, h still read as written, and I think so.") == {
        "a",
        "f",
        "h",
    }, "a run of one item is not a list"
    # …and the contract side, which is what forbids the scope fix. ⛔ THE LATER CLAUSE MUST BE THE
    # ONLY SOURCE of the letter asserted, or the assertion passes for the wrong reason: with
    # `**(a)**` in the first clause it is satisfied by markup alone and does NOT discriminate
    # (measured — it stays True under `runs[:1]`, the very change it claims to forbid). So the
    # first clause carries no `a` at all.
    assert "a" in flc._named_letters(
        "Only **(f)** and **(j)** still read as written, and **(l)** are **DRIFTED**, and a, f too."
    ), "a letter added in a LATER clause must still be compared, which is why scoping is refused"


def test_a_marked_token_wider_than_one_letter_is_not_a_row_letter() -> None:
    """§8 bounds every spelling to the twelve letters — so `{1,2}` was a false accusation.

    `_LETTER_ALT_MARKED` used `([a-l]{1,2})`, which reads ANY one- or two-letter all-a..l
    token in bold/code/italic as a row label: measured on a CORRECT page,
    `… still read as written (`if` it holds).` REDed with `['a','f','h','i','if','j']`, and
    `**he**` / `*be*` behaved the same. A two-letter English word in markup is prose, and §8
    says the forms are "bounded to the twelve letters §3's table can name" — one character.
    """
    for span, stray in (("`if`", "if"), ("**he**", "he"), ("*be*", "be"), ("**(ii)**", "ii")):
        letters = flc._named_letters(f"Only (a), (f), (h) and (j) still read as written, {span} it holds.")
        assert stray not in letters, f"a {len(stray)}-letter markup token is not a row letter: {span}"
    # The twelve real ones are still read in every marked spelling.
    for spelling in ("**(c)**", "**c**", "`c`", "`(c)`", "*(c)*", "*c*", "(c)"):
        assert flc._named_letters(f"Only (a), (f) and (j) still read as written, {spelling} too.") >= {"c"}


def test_a_mixed_separator_interior_join_is_the_declared_over_refusal() -> None:
    """An interior item joined by BOTH kinds of separator loses that member — on purpose.

    `a, f, h, and i, j` is a genuine list (§8 counts `,` and `and` as separators), and this
    reads it WITHOUT the `i`. That is the delta's one OVER-REFUSAL, and it is a deliberate
    trade rather than an oversight: the same bytes (`, and i,`) are prose, and a prose `I`/`a`
    there closes a stale complement list (`, and I, **(j)**` read CLEAN over one — measured,
    the reason #12028 exists). With identical bytes we can either read a phantom or drop a
    member, and a false PASS outranks the friction of an over-block (AGENTS.md A/B).

    ⛔ THIS TEST PINS THE DISCLOSURE, NOT A DESIRABLE OUTCOME. The run's TAIL is unaffected —
    `a, f, h, and i.` still reads its `i`, because a tail is decided by what follows it — so
    the Oxford-comma spelling every real page uses is safe. If a future change makes mixed
    interior separators evidence, this assertion MUST be inverted together with the residual
    lists in `findings_ledger_check.py` and the page; drifting from them silently is the
    failure this pins against.
    """
    for sentence in (
        "Only a, f, h, and i, j still read as written.",
        "Only a, f, h, and i and j still read as written.",
        "Only a and f and h, and i, j still read as written.",
    ):
        letters = flc._named_letters(sentence)
        assert "i" not in letters, f"over-refusal closed without updating the disclosure: {sentence!r}"
        assert {"a", "f", "h"} <= letters, f"the rest of the run must still read: {sentence!r}"
    # …and the TAIL of a mixed run IS read, which is the half that must never regress.
    assert flc._named_letters("Only a, f, h, and i. still read as written.") == {
        "a",
        "f",
        "h",
        "i",
    }


def test_a_quoted_figure_is_not_a_second_assertion(tmp_path, monkeypatch):
    """FAILS if either FIGURE arm counts a sentence that only QUOTES the phrase.

    The letter arms have always stripped quoted spans before the phrase test — a sentence
    that names the words is not asserting them. Both FIGURE arms did not: they tested
    `FIGURE_RE.search(s)` / `OVERTAKEN_FIGURE_RE.search(s)` on the RAW sentence, so
    `They wrote "seven of the twelve rows no longer read as written" verbatim.` counted as
    a second statement of the figure and red the page with "2 sentence(s) state a figure"
    over prose that asserts nothing (measured, both arms). That is a false accusation
    about the page's own subject matter, which is the class these arms must not create.

    ⛔ Both alternatives are pinned separately: a single decoy exercising only one arm
    leaves the other deletable with the suite green.
    """
    anchor = "is the exact defect #2795 D2 fixed. "
    for decoy, marker in (
        (
            'They wrote "seven of the twelve rows no longer read as written" verbatim. ',
            "rows no longer read as written",
        ),
        (
            'They wrote "four of the seven were overtaken" verbatim. ',
            "overtaken figure",
        ),
        # ⛔ AND A QUOTED SENTENCE WITH ITS OWN FULL STOP. The split ran before the strip,
        # so this decoy was cut inside the quotation, the left fragment had no closing
        # quote for `quoted` to see, and it counted as an assertion (measured).
        (
            'They wrote "seven of the twelve rows no longer read as written. '
            'It is wrong." verbatim. ',
            "rows no longer read as written",
        ),
        # ⛔ AND THE OVERTAKEN ARM'S OWN COPY OF BOTH DECOYS (#12029). The quote mask was
        # applied to one of two structural twins: the `overtaken` arm re-split the RAW
        # paragraph, so `"four of the seven were overtaken. It is wrong."` counted as a
        # second statement and red the page with *"2 sentence(s) state a 'N of the M
        # were' overtaken figure"* while the no-full-stop decoy stayed CLEAN — the exact
        # failing value this case exists for.
        (
            'They wrote "four of the seven were overtaken. It is wrong." verbatim. ',
            "overtaken figure",
        ),
    ):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, d=decoy: t.replace(anchor, f"{anchor}{d}", 1))
            problems = _doc_problems()
            assert not any(marker in p and "sentence(s)" in p for p in problems), (
                f"a QUOTED figure asserts nothing and must not count: {problems}"
            )

    # ⛔ AND THE ARM MUST STAY LIVE. Stripping quotes from both legs would satisfy every
    # case above while making each arm blind to a genuine second statement, so a decoy that
    # is NOT quoted still has to red with the "2 sentence(s)" message.
    for decoy in (
        "Note that four of the seven were overtaken. ",
        "Note that seven of the twelve rows no longer read as written. ",
    ):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, d=decoy: t.replace(anchor, f"{anchor}{d}", 1))
            problems = _doc_problems()
            assert any("2 sentence(s)" in p for p in problems), (
                f"an UNQUOTED second figure must still count: {decoy!r} -> {problems}"
            )


def test_every_spelling_of_a_second_tally_sentence_is_refused(tmp_path, monkeypatch):
    """FAILS if any ONE spelling of a letter list is not a tally candidate.

    The candidate filter once required a BOLDED `**(x)**` token, so a second genuine
    tally written plainly was no candidate at all and the "keep exactly one" invariant
    could be beaten by removing emphasis: measured, a de-bolded stale list beside one
    compliant bolded decoy read CLEAN while the parent refused it. The first fix that
    widened it was itself half-pinned — each further spelling was added and left with
    no case, so any one alternative could be deleted with the suite still green
    (measured: four separate neuterings, four green runs).

    So every spelling the page or this repo can plausibly use gets its own case, and
    each decoy is anchored at a REAL sentence boundary: appending after
    `stated both ways.)` merges the decoy into the preceding sentence, which already
    carries a list, and then the case passes for the wrong reason (measured).
    """
    decoys = (
        # a comma run, in each markup. ⛔ The BOLD case must be a run with no `and`,
        # or the pair form can carry it and the bold branch is deletable GREEN
        # (measured: dropping `\*\*\([a-l]\)\*\*` from MARKED left this test
        # passing until the decoy below became bold-only).
        "Six of the twelve rows no longer read as written: b, c, d, e, g and k.",
        "Six of the twelve rows no longer read as written: **(b)**, **(c)**, **(g)**.",
        "Six of the twelve rows no longer read as written: **(b)**, **(c)** and **(g)**.",
        "Six of the twelve rows no longer read as written: `b`, `c` and `g`.",
        "Six of the twelve rows no longer read as written: *(b)*, *(c)* and *(g)*.",
        # a parenthesised run
        "Six of the twelve rows no longer read as written (b), (c), (d), (e), (g) and (k).",
        # a two-item list
        "Two of the twelve rows no longer read as written: c and d.",
        # a comma run with NO "and" — the only case that exercises the run
        # alternative on its own (the decoy above also matches the pair form, so
        # without this one the run could be deleted green)
        "Three of the twelve rows no longer read as written: b, c, d.",
        # ⛔ BOTH members come from ONE match (the plain head rule), so taking only the
        # first regex group reads a two-member list as one member and the candidacy gate
        # (`>= 2`) never fires — `Note: j or a no longer read as written.` then reads
        # CLEAN (measured at e5a7ba2f). The `… and a …` spelling does NOT discriminate:
        # another alternative carries it.
        "Note: j or a no longer read as written.",
        # ⛔ THE GATE WAS LOWERCASE-ONLY WHILE THE EXTRACTOR IS IGNORECASE, so an UPPERCASE
        # marked tally was read by one and invisible to the other: measured,
        # `Note **C** still read as written.` appended to §3's takeaway paragraph returned
        # ZERO problems while the lowercase form red, and the page documents uppercase as a
        # read spelling. `letter_sentence` is now compiled IGNORECASE; each case below is a
        # single member, because a pair would be carried by the extractor disjunct instead.
        "Note **C** no longer read as written.",
        "Note `C` no longer read as written.",
    )
    anchor = "is the exact defect #2795 D2 fixed. "
    for decoy in decoys:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _d=decoy: t.replace(anchor, f"{anchor}{_d} ", 1),
            )
            problems = _doc_problems()
            assert any(
                "sentences saying 'no longer read as written'" in p for p in problems
            ), f"a second genuine tally must red in every spelling: {decoy!r} -> {problems}"

def test_a_possessive_apostrophe_cannot_hide_a_second_tally(tmp_path, monkeypatch):
    """FAILS if an apostrophe is treated as a quote delimiter, hiding a second tally.

    The quote-stripping regex was `"[^"]*"|'[^']*'`, so in English prose it blanked
    everything between two POSSESSIVES. Measured before the fix, appending this one
    sentence left `check_verdict_tally` CLEAN and `--check` at rc 0 — a second stale
    tally *and* a falsified verdict behind two apostrophes — while the parent refused
    the identical page. The false-accusation cases the stripping exists for all use
    double quotes, so a single quote now delimits only at a non-letter boundary.
    """
    decoy = (
        " The review's summary — seven of the twelve rows no longer read as written,"
        " **(c)** is STILL-TRUE — is what the ledger's author repeats."
    )
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace("stated both ways.)", f"stated both ways.) {decoy}", 1),
    )
    problems = _doc_problems()
    assert any("sentences saying 'no longer read as written'" in p for p in problems), (
        "a possessive apostrophe must not hide a second stale tally",
        problems,
    )


def test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling(
    tmp_path, monkeypatch
):
    """FAILS if the letter COMPARISON reads only one of the spellings the gate accepts.

    Two halves, both measured as fail-opens at `3cf42a2511`:

    ⛔ The tally side preferred a parenthesised extraction and fell back to the plain
    one with `or`. `set(findall(r"\\(([a-z]{1,2})\\)"))` matches the `(c)` INSIDE
    `**(c)**`, so on the shipped sentence the fallback was DEAD: appending
    `, and a, f too` (or `, and **a** too`) named two STILL-TRUE rows as overtaken and
    left `--check` at rc 0. Only the parenthesised spelling was ever compared.

    ⛔ The complement side read `\\*\\*\\(([a-z])\\)\\*\\*` alone, though the candidate gate
    accepts four spellings, so `… **(j)**, also c still read as written` asserted that
    a SUPERSEDED-BY-FIX row still read as written, at rc 0.

    One extractor now serves both, over `[a-l]` (the page's own twelve rows), which is
    what keeps ordinary prose out.
    """
    tally_edits = {
        # a plain member appended to the real list
        "and **(l)** are **DRIFTED**, and a, f too.": "were **DRIFTED**",
        # a bolded member with no parentheses
        "and **(l)** are **DRIFTED**, and **a** too.": "were **DRIFTED**",
        # the parenthesised control — this one was already caught
        "and **(l)** are **DRIFTED**, and (a) too.": "were **DRIFTED**",
    }
    for new, _marker in tally_edits.items():
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _n=new: t.replace("and **(l)** are **DRIFTED**.", _n, 1),
            )
            problems = _doc_problems()
            assert any(
                "as no longer reading as written" in p or "STILL-TRUE list names" in p
                for p in problems
            ), f"a letter added in any spelling must be compared: {new!r} -> {problems}"

    complement_edits = {
        "**(j)**, also c still read as written.": None,
        "**(j)**, and **c** still read as written.": None,
        "**(j)**, plus c still read as written.": None,
        "**(j)**, also (c) still read as written.": None,
    }
    for new, _ in complement_edits.items():
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _n=new: t.replace("**(j)** still read as written.", _n, 1),
            )
            problems = _doc_problems()
            assert any(
                "STILL-TRUE list names" in p for p in problems
            ), f"the complement list must be compared too: {new!r} -> {problems}"


def test_a_code_or_italic_letter_is_read_like_every_other_markup(
    tmp_path, monkeypatch
):
    """FAILS if a sentence can QUALIFY on its markup and then contribute no letters.

    The candidate gate has always accepted a letter written bold, code, italic or bare,
    but the extractor read only `**(x)**`, `**x**`, `(x)` and a bare member. So
    `, and `a` too` (code) and `, and *a* too` (italic) made the sentence a candidate and
    then contributed NOTHING, and the comparison silently under-counted.

    Measured identical at two heads, on four spellings — so this was never introduced by
    one lane: appending `, and ` + backtick-a-backtick + ` too` or `, and *a* too` to the
    tally sentence returned `--check` rc 0 over a page naming a STILL-TRUE row as
    overtaken, and `**(j)**, and ` + backtick-c-backtick + ` still read as written` did
    the same on the complement arm. §8 of the page promises a letter may be written
    "bold, code, italic or bare", so the doc described a comparison the code did not make.

    The four spellings that already worked are asserted beside them: this pin must not be
    satisfiable by a fix that trades one hole for another.
    """
    tally_edits = [
        "and **(l)** are **DRIFTED**, and `a` too.",
        "and **(l)** are **DRIFTED**, and *a* too.",
        "and **(l)** are **DRIFTED**, and **(a)** too.",
        "and **(l)** are **DRIFTED**, and a, f too.",
    ]
    for new in tally_edits:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _n=new: t.replace("and **(l)** are **DRIFTED**.", _n, 1),
            )
            problems = _doc_problems()
            assert any(
                "as no longer reading as written" in p or "STILL-TRUE list names" in p
                for p in problems
            ), f"a letter in any markup must be counted: {new!r} -> {problems}"

    complement_edits = [
        "**(j)**, and `c` still read as written.",
        "**(j)**, and *c* still read as written.",
        "**(j)**, and **(c)** still read as written.",
        "**(j)**, also c still read as written.",
    ]
    for new in complement_edits:
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, _n=new: t.replace("**(j)** still read as written.", _n, 1),
            )
            problems = _doc_problems()
            assert any(
                "STILL-TRUE list names" in p for p in problems
            ), f"the complement must count every markup too: {new!r} -> {problems}"


def test_a_second_still_read_as_written_sentence_is_refused(tmp_path, monkeypatch):
    """FAILS if the tally's COMPLEMENT list can be joined across sentences.

    The `still read as written` list was joined from every matching sentence and
    compared as a set-union, so the page could assert `seven of the twelve … no longer
    read as written` beside `Only **(a)** still read as written` — 7 + 1 against a
    table of twelve — at `--check` rc 0 (measured), and falsifying the real still-list
    next to a compliant decoy did the same. One sentence, or a red.
    """
    with monkeypatch.context() as patch:
        _mutated(
            tmp_path,
            patch,
            lambda t: t.replace(
                "stated both ways.)",
                "stated both ways.) Only **(a)** still read as written.",
                1,
            ),
        )
        problems = _doc_problems()
        assert any("sentences saying 'still read as written'" in p for p in problems), (
            f"a second complement sentence must red: {problems}"
        )


def test_a_second_tally_sentence_is_refused(tmp_path, monkeypatch):
    """FAILS if a decoy sentence can supply the §3 tally's letter list.

    The arm joined EVERY sentence containing "no longer read as written", so a
    second one anywhere in the paragraph could carry the correct letters, the
    per-letter verdict attributions and the overtaken count while the sentence a
    lane actually quotes went stale: measured on the pre-fix tool, falsifying the
    real sentence's letter list while appending a compliant decoy in the same
    paragraph returned `[]`. The §6 survivor arm already refuses a second match for
    the same reason — the subject is located BY PATTERN, so more than one candidate
    means the arm can report OK over a sentence nobody reads.
    """
    real = (
        "written** — **(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is\n"
        "**ADOPTED**, and **(b)**, **(k)** and **(l)** are **DRIFTED**."
    )
    decoy = (
        " stated both ways.) Note: seven of the twelve rows no longer read as written — "
        "**(c)**, **(d)** and **(g)** are **SUPERSEDED-BY-FIX**, **(e)** is **ADOPTED**, "
        "and **(b)**, **(k)** and **(l)** are **DRIFTED**. "
    )

    def _falsify_and_deceive(text: str) -> str:
        # The falsified sentence KEEPS a letter token on purpose: a candidate must name
        # a letter to be compared at all, so falsifying it down to bare prose would
        # drop it out of the population and leave this arm testing nothing (measured).
        falsified = text.replace(
            real, "written** — only **(a)** moved this round.", 1
        )
        assert falsified != text, "the tally sentence did not change"
        deceived = falsified.replace(" stated both ways.) ", decoy, 1)
        assert deceived != falsified, "the decoy anchor did not change"
        return deceived

    with monkeypatch.context() as patch:
        _mutated(tmp_path, patch, _falsify_and_deceive)
        problems = _doc_problems()
        assert any("sentences saying 'no longer read as written'" in p for p in problems), (
            f"a decoy must not be able to take the tally match: {problems}"
        )

def test_a_bare_shorthand_does_not_satisfy_the_evidence_floor(tmp_path, monkeypatch):
    """FAILS if an evidence cell can name only `:LINE`, leaving its path in a neighbour.

    Reachable by a lane trimming a row: the shorthand looks like a citation to a
    reader and to the marker test, but the cell itself names nothing openable.
    Measured before the fix, replacing §3 row (e)'s evidence cell with `` `:5` ``
    returned `[]` from every arm — `_has_path_marker(":5")` is True and
    `check_citations` resolved it against row (d)'s path, which is a different
    row's evidence. §1 says the tool refuses a page whose evidence cell names none
    of `path:line`; an anchor in a NEIGHBOUR is not this cell naming one.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "the code names the separation | `tortoise/pipeline.py:16`", "the code names the separation | `:5`", 1
        ),
    )
    problems = _doc_problems()
    assert any("names no `path:line`, issue, command or §N" in p for p in problems), problems


def test_a_shorthand_cannot_resolve_across_a_section_boundary(tmp_path, monkeypatch):
    """FAILS if a `:LINE` shorthand resolves against an anchor in ANOTHER section.

    Measured: replacing §5's in-prose `docs/plans/…:558-559` with `` `:9` `` returned
    `[]`, because the anchor was still §3 row (h)'s test file — a path §5 never
    names. Scoping to the section is what makes "nearest PRECEDING" mean what §8
    says it means. Per-ROW scoping was rejected: §4's second row says "Cited by the
    same WS-C plan, `:558-559`" and deliberately anchors on the row above.
    """
    _mutated(
        tmp_path,
        monkeypatch,
        lambda t: t.replace(
            "`docs/plans/2026-09-10-ws-c-numeric-normalization.md:558-559`\n", "`:9`\n", 1
        ),
    )
    problems = _doc_problems()
    assert any("shorthand with no preceding" in p for p in problems), problems


def test_a_governed_group_in_its_own_sentence_is_validated(tmp_path, monkeypatch):
    """FAILS open if the page's ELLIPSIS hides the governed complement.

    The documented shape writes the governed group as its own sentence
    (`… still read as written. — **(c)** and **(d)** do not.`). Such a fragment carries
    NEITHER tally phrase, so every phrase-driven arm — the still list, the non-still
    list, the governed span and the stray-tally arm — looked past it: measured at
    `c5990249c0`, a fragment naming STILL-TRUE rows read CLEAN in both positions, while
    the identical text inside the sentence reds.

    The rule is the one the governed-span arm already uses, applied to every sentence in
    the paragraph: everything before the negation verb must be NOTHING BUT letter items
    and separators. Anything carrying a real word is prose and is never accused — the
    control here is `Rows (i) and (j) do not apply here.`
    """
    tail = "are **DRIFTED**. Only"
    complement = "still read as written. (Four"
    contradicting = (
        "are **DRIFTED**. — **(i)** and **(j)** do not. Only",
        "still read as written. — **(i)** and **(j)** do not. (Four",
        "are **DRIFTED**. — **(i)** and **(j)** no longer do. Only",
        # ⛔ AND THE PAGE'S OWN LABEL MAY OPEN THE FRAGMENT (`Only **(a)** … still read as
        # written` is the documented list label): without it this spelling read CLEAN.
        "are **DRIFTED**. Only **(i)** and **(j)** do not. Only",
        # ⛔ A LONE MARKED ITEM IS THE SPAN (#11902 review, round 15 P3): with
        # `lone_marked=False` a one-member fragment contributed no letters and read CLEAN,
        # while the identical claim inside the sentence reds. All three marked spellings.
        "are **DRIFTED**. — **(i)** do not. Only",
        "are **DRIFTED**. — `i` do not. Only",
        "are **DRIFTED**. — *(i)* do not. Only",
        # The idiomatic trailing adverb, which the tail check must accept.
        "are **DRIFTED**. — **(i)** and **(j)** do not read as written any more. Only",
    )
    for spelled in contradicting:
        src = tail if spelled.startswith("are ") else complement
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, a=src, b=spelled: t.replace(a, b, 1))
            problems = _doc_problems()
            assert any("negated complement" in p for p in problems), (
                f"a standalone governed fragment must be validated: {spelled!r} "
                f"left {problems}"
            )
    for spelled in (
        "are **DRIFTED**. — **(c)** and **(d)** do not. Only",
        # A one-member fragment naming a NON-still row is correct and must stay CLEAN.
        "are **DRIFTED**. — **(c)** do not. Only",
        "are **DRIFTED**. Rows (i) and (j) do not apply here. Only",
        # ⛔ A NEGATION MUST GOVERN *READING*, or the arm accuses a TRUE sentence
        # (#11902 review, round 12): these all name STILL-TRUE rows and all state what the
        # table says, and all three RED before the tail check existed.
        "are **DRIFTED**. — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** "
        "do not need re-reading. Only",
        "are **DRIFTED**. — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** "
        "did not move. Only",
        "are **DRIFTED**. — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** "
        "no longer dispute the fix. Only",
        # `Only` is this page's own list label, so it may open the governed span — and a
        # real word after it still means prose.
        "are **DRIFTED**. Only **(i)** and **(j)** do not apply here. Only",
        # ⚠️ A STATED RESIDUAL, pinned so it cannot drift silently: only the verbs this page
        # pairs with the tally are read. `no longer hold.` negates a row too, but it is
        # shape-identical to the TRUE `did not move.`, so reading arbitrary verbs
        # re-creates the false accusation. CLEAN here is the declared trade, not a pass.
        "are **DRIFTED**. — **(i)** and **(j)** no longer hold. Only",
        # …and a PUNCTUATED continuation is no better: it does not separate the true from the
        # false either. Allowing one made the verb requirement moot and re-red the very
        # sentence round 12 removed (#11902 review, round 14).
        "are **DRIFTED**. — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** "
        "do not, in fact, need re-reading. Only",
        "are **DRIFTED**. — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** "
        "did not, however, move. Only",
        "are **DRIFTED**. — **(i)** and **(j)** do not, unlike the rest. Only",
    ):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, b=spelled: t.replace(tail, b, 1))
            problems = _doc_problems()
            assert not any("negated complement" in p for p in problems), (
                f"correct prose and a legitimate fragment must stay CLEAN: {spelled!r} "
                f"left {problems}"
            )


def test_a_second_tally_cannot_hide_one_blank_line_away(tmp_path, monkeypatch):
    """FAILS open if a tally outside the takeaway PARAGRAPH goes unread.

    Every §3 arm takes its subject from `TAKEAWAY_MARKER`'s paragraph, so a second
    complement list one blank line away was CLEAN while the same-paragraph form reds —
    measured, appending a `\\n\\n`-separated `Only **(a)** and **(f)** still read as written.`
    left `--check` at exit 0. §3's PROSE carries exactly one sentence naming either phrase;
    the table rows are excluded (they carry verdicts, not the tally), and anything past
    that is an assertion the paragraph-scoped arms never see. A paragraph that only
    QUOTES either phrase stays exempt — the quote mask runs first, which is the control.
    """
    anchor = "is the exact defect #2795 D2 fixed. "
    decoy = "\n\nOnly **(a)** and **(f)** still read as written."
    with monkeypatch.context() as patch:
        _mutated(tmp_path, patch, lambda t: t.replace(anchor, f"{anchor}{decoy}", 1))
        problems = _doc_problems()
        assert any("OUTSIDE the takeaway paragraph" in p for p in problems), (
            f"a second tally one blank line away must red: {problems}"
        )
    quoted = '\n\nThey wrote "only **(a)** still read as written" verbatim.'
    with monkeypatch.context() as patch:
        _mutated(tmp_path, patch, lambda t: t.replace(anchor, f"{anchor}{quoted}", 1))
        problems = _doc_problems()
        assert not any("OUTSIDE the takeaway paragraph" in p for p in problems), (
            f"a QUOTED phrase asserts nothing and must stay CLEAN: {problems}"
        )
    # ⛔ AND A QUOTATION WITH A SENTENCE BREAK INSIDE IT (#11902 review, round 10). The
    # control above has no internal full stop, so it could not see that `remainder` was
    # split RAW: a quoted `… no longer read as written. It is wrong.` was cut at its own
    # full stop and the fragment — which asserts nothing — was reported as a second tally.
    for q in (
        '\n\nThey wrote "seven of the twelve rows no longer read as written. '
        'It is wrong." verbatim.',
        "\n\nThey wrote 'seven of the twelve rows no longer read as written. "
        "It is wrong.' verbatim.",
    ):
        with monkeypatch.context() as patch:
            _mutated(tmp_path, patch, lambda t, s=q: t.replace(anchor, f"{anchor}{s}", 1))
            problems = _doc_problems()
            assert not any("OUTSIDE the takeaway paragraph" in p for p in problems), (
                f"a quotation with an internal full stop asserts nothing: {problems}"
            )
    # ⛔ AND THE LEGITIMATE-PAGE DIRECTION (#11902 review, round 9). The first version of
    # this arm compared sentence-CHUNKS, and the paragraph subject starts at the MARKER
    # TEXT while the section stream inherits whatever precedes it — so a terminator other
    # than `. ` before the paragraph joined its first sentence to the previous text and the
    # page's own single tally was reported as a stray (measured: rc 1 on a correct page).
    for terminator in (":", ";", "\u2014"):
        intro = (
            "this re-cut keeps them *and* records which ones the tree has since overtaken."
        )
        with monkeypatch.context() as patch:
            _mutated(
                tmp_path,
                patch,
                lambda t, c=terminator, i=intro: t.replace(i, i[:-1] + c, 1),
            )
            problems = _doc_problems()
            assert not any("OUTSIDE the takeaway paragraph" in p for p in problems), (
                f"a preceding {terminator!r} must not turn the page's own tally into a "
                f"stray: {problems}"
            )
