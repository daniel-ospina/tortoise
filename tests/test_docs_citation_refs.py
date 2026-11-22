"""#1165 — a pointer INTO a scoping/research record must land on a line of text.

WHY THIS FILE EXISTS

The #5063 defect class is *a pointer that does not resolve*: a document names the
place its reader must go, and the reference is dangling. PR #11559 fixed exactly
that for the `E1`/`E2` label pointers in the #265 encryption scope — and then
introduced a fresh instance of the same defect in the same commit, in the same
file. Its status/carve-out block inserts 8 lines at the top of
`docs/scoping/2026-08-13-317-cross-encoder-scoping.md`, which shifts every
downstream line by 8. Two documents cite that file's `:98` for the reranker's
`max_length 512` content contract:

  * `docs/scoping/2026-08-13-265-content-encryption-scoping.md:128`
  * `docs/research/2026-08-13-265-encryption/axis-research.md:99`

At that head, line 98 of the target is BLANK. A `#265` revival lane following the
carve-out pointer lands on whitespace — the precise failure the PR's own body
describes as "the #5063 defect class", re-created by the diff that fixes it.

Nothing caught it: markdownlint does not resolve citations, and the `docs` job's
linters see prose, not targets. So the rot is silent, which is what this ends.

THE POLICY IT ENFORCES, AND WHERE THAT POLICY COMES FROM

Citing by NAME is the recorded convention, not this file's invention:

  * `docs/architecture/S2-DECISION-SEAM.md:503` — "assertions by NAME — never by
    line number, and never by row **ordinal**. Both go stale the same way".
  * `docs/evidence/7949-capstone/E1-E5-PROOFS.md:137` — "cited **by title, never
    by line**: a line number ROTS".
  * `tortoise/hosted_api.py`, the `POST /v1/context` transport-bound comment —
    "Cited by SYMBOL, never by line number (line numbers re-stale — …)". Cited
    without a line number because that is the rule it states, and this list's
    own `:NNN` entries rot the same way when the target grows above them.

Sphinx's cross-reference guidance converges on the same rule from outside this
repo: explicit labels (`:ref:`) are preferred over fragile direct links because
labels survive edits and warn when they break.

This test does not ban line numbers — it refuses one that does not land on text.
Any surviving `:NN` pointer **written in one of the two forms it resolves** is
therefore kept HONEST **against the one shift it can see**: insert a section above
it and this reds when the shift lands on whitespace — but a shift onto a
DIFFERENT non-blank line stays green, and the pointer then silently names whatever
row now sits there. (A pointer already past the end of the file is red for its own
reason; no insertion can drive one there.) This is line PRESENCE, not semantic
correctness (see STATED LIMITS). A pointer outside those two forms is not kept
honest at all.

WHY THIS IS NOT A DUPLICATE OF THE EXISTING CITATION GUARDS

`tests/test_extractor_v4_citation_rot.py` (#8506) and
`tests/test_extractor_v4_doc_citations.py` (#8498) already hold this instrument,
but neither reads this relation. `citation_rot` resolves a quoted `sym` against a
`file.py:NNNN` whose symbol must still exist in that source file, and it states
the same claim across several design documents rather than one — the asserted set
is `FACT_STATEMENTS_IN_SIBLINGS`, whose entries are `STORAGE`, `ONTOLOGY`,
`SEARCH`, `EVAL_SPEC`, `INGEST` and `ONTOLOGY_CHANGELOG` (`DOC` is a declared
module constant that carries no entry. ⛔ `SEARCH` was absent when this file was
written and arrived with the `origin/main` merge — re-derive the set at YOUR head
(`grep -nE '^\\s+(STORAGE|ONTOLOGY|SEARCH|INGEST|EVAL_SPEC|ONTOLOGY_CHANGELOG),'
tests/test_extractor_v4_citation_rot.py`) instead of trusting this list, for the
same reason the paragraph below declines to state the constants' line numbers).
`doc_citations` does not use `file.py:NNNN` at all: it pins
that §7's value-gate row names the SHIPPED symbols (`value_gate._REFERENCE` /
`identifier_only()`) and that those symbols behave as the row says. Neither reads
a markdown-to-markdown line pointer, and neither covers `docs/scoping/` or
`docs/research/`, so the regression above was invisible to both. This file is the
same INSTRUMENT over the missing subject, not a second implementation of theirs.
⛔ `citation_rot`'s constants are cited here by NAME, never by line number:
`origin/main` added `SEARCH` below `ONTOLOGY` and shifted **the three constants
declared after it** (`ONTOLOGY_CHANGELOG`, `INGEST`, `EVAL_SPEC` each moved down
4 lines; `DOC`, `STORAGE` and `ONTOLOGY` did not move at all) — the same rot this
file exists to catch, and one it is blind to inside a `.py` docstring (see STATED
LIMITS). An earlier cut of this paragraph cited the ranges `:76-81` and `:248-249`.
Both are dead at the merge result, and `:76-81` was **already off by one at the
head that wrote it**: the range ended at `INGEST` while the sentence listed
`EVAL_SPEC`, which sat at `:82`.

`tests/test_s2_decision_record_5087.py` (#10072) is the closest precedent for the
policy side: its §8 pin reads "§8's rows must cite a sibling mutant or test by
NAME, never by ordinal".
Like both citation guards, this file is DUAL-REGISTERED (`core` + `tier1`) because
a docs-only diff runs `tier1` only — and a docs-only diff is exactly the change
that can move the line numbers a citation depends on.

WHAT IT CHECKS

A citation is reported when its RESOLVED TARGET lies under `docs/scoping/` or
`docs/research/` — the decision-scope and research records whose pointer graph
#1165 exists to keep followable (a disposition that names an issue a reader
cannot find is the #5063 defect the PR body leads with). Two forms are resolved:

  1. a full path — ``docs/....md:NN`` (also ``:NN-MM``; a range's START must be
     non-blank, because a range's span is the author's claim about content and no
     mechanical check can judge it);
  2. the shorthand `` `317` scoping:NN `` / ``317 research:NN``, resolved to the
     single record under `docs/scoping/` / `docs/research/` whose PATH carries
     `<number>` as a `-<number>-` token — hyphen-WRAPPED on both sides. The match
     is on the path's tokens,
     not on a filename glob: `docs/research/` is nested, and the `#265` brief this
     PR edits keeps its number in the DIRECTORY
     (`docs/research/2026-08-13-265-encryption/axis-research.md`).

STATED LIMITS — deliberate, and measured rather than guessed:

  * A bare ``:NN`` continuation (``(`:203`)``) carries no document name, so it
    cannot be resolved in isolation and is NOT checked. A citation meant to
    survive should carry its own name instead of leaning on a preceding one —
    which is why this change introduces NO such pointer: the two sites it touches
    (`docs/scoping/2026-08-13-317-cross-encoder-scoping.md` and its research
    brief) cite the `265` scope doc's wiring table by its `#317 reranker` ROW
    NAME instead of by `:203`.
  * Line PRESENCE is the whole check. A pointer is dead only when its line is
    BLANK or past the end of the file (`_dead_reason`): one that a later insert
    SHIFTS onto a different non-blank line is reported healthy, and it silently
    names whatever row now sits there. Measured, not reasoned about: inserting ONE
    line above the `#160 hosted embeddings` bullet in
    ``docs/scoping/2026-08-13-265-content-encryption-scoping.md``'s
    `### Runtime prerequisites` list moves the `#317 cross-encoder reranker` row
    down one line, and a `:NN` pointer at it still reports 0 problems while it
    names the `#160` row. (The site matters: an insert above that document's
    WIRING TABLE, ~56 lines below, moves nothing such a pointer reads.) That is
    why this change cites that row by NAME, and why the measurement stays here as
    the instrument's limit rather than as an excuse to write the number. Past-EOF
    is a
    SEPARATE arm and no insertion reaches it — only a pointer already past the end
    of the file, like the ``ONTOLOGY.md:1515`` cite from
    `docs/plans/2026-09-10-ws-c-numeric-normalization.md:501` (enumerated below, with the rest
    of the out-of-scope backlog). Cite a row
    by NAME when it must survive an edit (the policy quoted above); this instrument
    catches the whitespace half of the problem, which is the half that is
    mechanically decidable without inventing a notion of what a row MEANS.
  * The two resolved FORMS are also the instrument's scope, and they are
    narrower than "a pointer into these two directories". A citation written as
    a bare filename with no ``docs/`` prefix (``platform-seams.md:192``), a
    ``docs/``-less relative path (``scoping/....md:98``), or a ``#L98`` fragment
    matches NEITHER regex (``_PATH_REF`` requires the literal ``docs/``,
    ``_SHORT_REF`` requires the ``scoping|research:NN`` shorthand) and is not
    checked even when the target it names is in scope. Measured at the head this
    landed on, one such live citation exists —
    ``docs/research/2026-09-01-gbrain-learnings/platform-seams.md`` cites
    ``platform-landscape.md:192-193``, an in-scope target, and the guard never
    looks at it. What this instrument covers is "every pointer written in the
    two forms above", not "every `:NN` pointer into ``docs/scoping/`` and
    ``docs/research/``".
  * The CITING side is `docs/**/*.md` only (`docs.rglob`). This instrument never
    reads a pointer written inside a `.py` docstring or comment — including one
    of the three policy quotes above, which lives in `tortoise/hosted_api.py` — so
    "the citation graph" here means the graph BETWEEN documents, not every
    citation in the tree.
  * The guard is scoped to those two directories ON PURPOSE. With `_in_scope`
    widened to accept EVERY target under `docs/`, this tree carried **10** further
    dead citations when the disposition was written (measured at `e66450bb`,
    the base WHEN IT WAS WRITTEN — the paragraph below re-measures the same
    widening against `origin/main` and gets **8**, so do not "correct" this 10 to
    that 8: they are two different heads and neither is the live one; paths below
    are relative to `docs/`: `ONTOLOGY.md` ×4 — three blank lines plus one
    PAST-EOF cite, which is a different repair from a blank
    one — `ops/registry-backup-dr.md` ×2, `infra/embedded-reaper-cron.md`,
    `architecture/STORAGE-ARCHITECTURE.md`,
    `epics/2026-08-03-tortoise-hosted-platform/plans/7714-data-model.md`,
    `epics/1402-eval-battery/01-align.md`). ⛔ WIDENING `_TARGET_SCOPE` ALONE CANNOT
    REPRODUCE THAT TEN, and the recipe matters because someone will run it: that
    constant is matched against the target's SECOND path part, so a ROOT-LEVEL
    target (`docs/ONTOLOGY.md` → `('docs', 'ONTOLOGY.md')`) has no directory there
    and can never be admitted by any set of directory names. Widening
    `_TARGET_SCOPE` to every directory under `docs/` therefore returns **6**, with
    zero `ONTOLOGY.md` rows — a reader who follows the old wording would measure 6
    and "correct" a correct ten.
    ⛔ AND THE TEN IS A HEAD-BOUND READING, NOT A STANDING FACT — and the two that
    `main` moved are NOT repaired. Measured against `origin/main`: the guard stops
    reporting `docs/plans/2026-09-22-2814-rebuild-config-durability.md:270`
    (`ONTOLOGY.md:833` is now the `passes_frequency_gate` table row, not the
    "Tombstone" controlled term) and
    `docs/scoping/2026-10-13-8497-selfhost-journal.md:36`
    (`STORAGE-ARCHITECTURE.md:365` is now the *three movements* "Write:" bullet,
    not "a wiring gap, not a design gap"), because `main` inserted text ABOVE both
    targets. **Both still cite content that is not there** — they are exactly the
    "shift onto a DIFFERENT non-blank line stays green" case this docstring's
    limits name, so a line-number citation cannot distinguish "repaired" from
    "moved". Re-derive the number rather than trusting either one — a count in
    prose re-stales exactly like the line numbers this guard exists to catch. All
    of them are pre-existing and none is a target of #1165. A guard that shipped
    red would be disabled within a day, and repairing this backlog is not this
    change's job — they are tracked under `#5063` ("one binding from a written
    claim to the system it describes"), the shared root this guard is a symptom of,
    AND enumerated row by row in `#11573` so a reader can act on them rather than
    only read the root. Widening the scope is the follow-up once that backlog is
    paid.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import unicodedata
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOCS = REPO / "docs"

#: The artifact directories whose inbound pointers this guard keeps honest.
_TARGET_SCOPE = ("scoping", "research")

#: An absolute-in-repo markdown citation, with an optional `-END` range.
#: The lookbehind refuses a match that starts MID-token only: a path is normally
#: wrapped in backticks (`` `docs/....md:98` ``), so a backtick must NOT disqualify it.
_PATH_REF = re.compile(r"(?<![\w/])(docs/[A-Za-z0-9_./-]+\.md):(\d+)(?:-(\d+))?")

#: The shorthand form: `` `317` scoping:98 ``, `317 scoping:98`, `` `265`-scoping:128 ``.
#: ⛔ THE LEFT BOUNDARY IS LOAD-BEARING. Without it a DATE fragment matches: the
#: words `2026-08-13 research:99` contain `13 research:99`, which resolves to
#: EVERY `docs/research/` record carrying the `-13-` token — **6** at this head
#: (`…-265-…`, `…-309-…`, `…-316-…`, `…-317-…`, `…-388-…`, `…-405-…`) — and reds a
#: correct sentence naming six files (`scoping` holds 16 such records, one of them
#: dated `2026-10-13`, so the count is per-kind and not a constant).
#: `(?<![\w-])` refuses a match starting mid-token — mid-NUMBER and mid-DATE —
#: while leaving `` `317`-scoping:128 `` and `265 scoping:128` intact.
_SHORT_REF = re.compile(r"(?<![\w-])`?(\d+)`?[-\s](scoping|research):(\d+)(?:-(\d+))?")


def _in_scope(target: Path, root: Path) -> bool:
    """Is this citation's target one this guard owns?"""
    parts = target.relative_to(root).parts if target.is_relative_to(root) else ()
    return bool(parts) and parts[0] == "docs" and len(parts) > 1 and parts[1] in _TARGET_SCOPE


def _dead_reason(lines: list[str], start: int, end: int | None) -> str | None:
    """The reason a citation is dead, or None when it lands on text."""
    last = end if end is not None else start
    if start < 1 or start > len(lines) or last > len(lines):
        # Name the bound that is ACTUALLY outside the file. For a `:1-99` range
        # against a 3-line file the dead end is 99, so reporting `start` would
        # point a fixer at line 1 — the one line that is fine.
        # ⛔ THE START IS GATED AGAINST THE FILE TOO (measured, #11559 review).
        # An INVERTED range — `:9-3` on a 3-line file — puts the START past EOF
        # while `last` is INSIDE it, so testing only `last` fell through to
        # `lines[start - 1]` and raised IndexError: a traceback naming no
        # citation at all, from a typo a reader can easily type.
        bad = start if (start < 1 or start > len(lines)) else last
        cited = f":{start}-{end}" if end is not None else f":{start}"
        return (
            f"line {bad} is outside the file "
            f"({len(lines)} lines; cited as {cited})"
        )
    if not lines[start - 1].strip():
        return f"line {start} is BLANK"
    return None


def _resolve_shorthand(
    number: str, kind: str, docs: Path
) -> tuple[Path | None, str | None]:
    """The single `docs/<kind>/` record labelled `<number>`, or (None, why not).

    The label is a `-<number>-` token in the path — hyphen-WRAPPED, neither a
    prefix nor a suffix of the FILENAME. `docs/research/` is NESTED: the `#265`
    brief this change edits is
    `docs/research/2026-08-13-265-encryption/axis-research.md`, where the number
    lives in the DIRECTORY and the filename carries none. A `*-265-*.md` filename
    glob (one level or recursive) therefore reports a correct shorthand as
    unresolvable, so the match is made against the path's `-<number>-` tokens
    instead. ⛔ THE MATCH IS NARROWER THAN "the path mentions <number>", and the
    differences are measured, not hypothetical (all three fail CLOSED — they
    report the shorthand unresolvable rather than resolving it to a wrong
    record):
      * a number that is the path's FIRST token — `docs/research/` carries seven
        such `#1714` records (`1714-problem-diverge.md` and siblings), so a
        `` `1714` research:NN `` shorthand is reported unresolvable today;
      * a number that ENDS a directory segment (`foo-265/x.md`) — neither first
        nor last token, and `-265-` does not occur;
      * a label not hyphen-LEADING — `docs/scoping/` carries `.334-diamond-working.md`
        and `.334-solution-working.md`, which `-334-` cannot see, so a
        `` `334` scoping:NN `` shorthand resolves to the one hyphen-wrapped
        `#334` record and CANNOT report the others as ambiguous.
    What the ambiguity arm does guarantee is only this: two or more records that
    BOTH carry `-<number>-` return `(None, "ambiguous: …")` and never a silent
    pick.
    """
    candidates = sorted(
        path
        for path in (docs / kind).rglob("*.md")
        if f"-{number}-" in f"/{path.relative_to(docs / kind).as_posix()}"
    )
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"no docs/{kind}/ path carries the -{number}- label"
    return None, f"ambiguous: {[c.relative_to(docs).as_posix() for c in candidates]}"


def _citation_problems(docs: Path = DOCS) -> list[str]:
    """Every dead in-scope citation, as `citing:line -> target:NN (reason)`."""
    root = docs.parent
    problems: list[str] = []
    for citing in sorted(docs.rglob("*.md")):
        try:
            text = citing.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # pragma: no cover - unreadable doc
            problems.append(f"{citing}: unreadable ({exc})")
            continue
        source = citing.relative_to(root) if citing.is_relative_to(root) else citing
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in _PATH_REF.finditer(line):
                rel, start, end = match.group(1), int(match.group(2)), match.group(3)
                target = root / rel
                if not _in_scope(target, root):
                    continue
                if not target.exists():
                    problems.append(f"{source}:{lineno} -> {rel}:{start} (no such file)")
                    continue
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                reason = _dead_reason(lines, start, int(end) if end else None)
                if reason:
                    problems.append(
                        f"{source}:{lineno} -> {target.relative_to(root)}:{start} ({reason})"
                    )

            for match in _SHORT_REF.finditer(line):
                number, kind, start, end = (
                    match.group(1),
                    match.group(2),
                    int(match.group(3)),
                    match.group(4),
                )
                if kind not in _TARGET_SCOPE:
                    continue
                target, why = _resolve_shorthand(number, kind, docs)
                if target is None:
                    problems.append(
                        f"{source}:{lineno} -> {number} {kind}:{start} ({why})"
                    )
                    continue
                lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
                reason = _dead_reason(lines, start, int(end) if end else None)
                if reason:
                    shown = target.relative_to(root) if target.is_relative_to(root) else target
                    problems.append(f"{source}:{lineno} -> {shown}:{start} ({reason})")
    return problems


def test_every_scoping_and_research_citation_lands_on_a_line_of_text():
    """No pointer into docs/scoping or docs/research lands on whitespace.

    Non-vacuous by construction: at the pre-fix head this FAILED naming
    `docs/scoping/2026-08-13-265-content-encryption-scoping.md:128 ->
    docs/scoping/2026-08-13-317-cross-encoder-scoping.md:98 (line 98 is BLANK)`
    and its `axis-research.md` sibling.
    """
    problems = _citation_problems()
    assert not problems, (
        "dead doc citations — cite the target by NAME (docs/architecture/S2-DECISION-SEAM.md:503, "
        '"assertions by NAME — never by line number") or repair the line number:\n  '
        + "\n  ".join(problems)
    )


def test_the_guard_reports_a_pointer_that_lands_on_whitespace(tmp_path: Path):
    """The guard is not vacuous: a blank-line target is reported, not tolerated.

    Builds a throwaway docs tree whose citation points at a blank line — the exact
    shape the real regression had — and asserts the checker names it. This keeps
    the test above from passing merely because its regexes matched nothing.
    """
    (tmp_path / "docs" / "scoping").mkdir(parents=True)
    target = tmp_path / "docs" / "scoping" / "2026-01-01-999-target-scoping.md"
    target.write_text("first line\n\nthird line\n", encoding="utf-8")
    (tmp_path / "docs" / "citing.md").write_text(
        "see `docs/scoping/2026-01-01-999-target-scoping.md:2` and 999 scoping:2\n",
        encoding="utf-8",
    )

    problems = _citation_problems(docs=tmp_path / "docs")

    assert len(problems) == 2, problems
    assert all("line 2 is BLANK" in p for p in problems), problems


def test_the_guard_reports_an_in_scope_target_that_does_not_exist(tmp_path: Path):
    """The third defect class — a citation to a file that is GONE — is pinned.

    The dead-path arm is the one branch no other test reached (measured on this
    PR's first revision: deleting it from `_citation_problems` left all eight
    tests green), so a guard whose docstring claims three covered classes could
    lose one silently. The target is IN scope (`docs/scoping/`) and absent; the
    sibling pointer at an existing target beside it must not be reported.
    """
    (tmp_path / "docs" / "scoping").mkdir(parents=True)
    (tmp_path / "docs" / "scoping" / "2026-01-01-999-live-scoping.md").write_text(
        "first line\nsecond line\n", encoding="utf-8")
    (tmp_path / "docs" / "citing.md").write_text(
        "gone: `docs/scoping/2026-01-01-999-deleted-scoping.md:3`\n"
        "fine: `docs/scoping/2026-01-01-999-live-scoping.md:2`\n",
        encoding="utf-8",
    )

    problems = _citation_problems(docs=tmp_path / "docs")

    assert len(problems) == 1, problems
    assert "(no such file)" in problems[0], problems
    assert "2026-01-01-999-deleted-scoping.md:3" in problems[0], problems


def test_the_past_eof_arm_handles_an_inverted_range_without_crashing():
    """An INVERTED range (`:9-3`) puts the START past EOF and the END inside it.

    The past-EOF gate used to test only `last`, so this shape fell through to
    `lines[start - 1]` and raised IndexError — a traceback that names NO citation,
    from a typo a reader can type (`:9-3`, `:5-0`). It must report instead, blaming
    the START, which is the bound actually outside the file.
    """
    inverted = _dead_reason(["a", "", "c"], 9, 3)
    assert inverted is not None, "an inverted range must not be reported healthy"
    assert "line 9 is outside the file" in inverted, inverted

    # The ordinary past-EOF range still blames the END — the bound that is out.
    past_end = _dead_reason(["a", "", "c"], 1, 99)
    assert past_end is not None and "line 99 is outside the file" in past_end, past_end


def test_the_past_eof_arm_reports_a_zero_start():
    """`:0` is outside the file at the START; it must name 0, not crash."""
    zero = _dead_reason(["a", "", "c"], 0, None)
    assert zero is not None and "line 0 is outside the file" in zero, zero


def test_the_guard_ignores_targets_outside_its_declared_scope(tmp_path: Path):
    """The scope filter narrows the guard — it must not hide an in-scope target.

    A pointer at an OUT-OF-SCOPE target (docs/architecture/) is not this guard's
    claim, so it is skipped; the in-scope pointer beside it is still reported. This
    pins the filter as a scope, not an escape hatch that swallows real findings.
    """
    (tmp_path / "docs" / "architecture").mkdir(parents=True)
    (tmp_path / "docs" / "architecture" / "OUT.md").write_text("a\n\nb\n", encoding="utf-8")
    (tmp_path / "docs" / "research").mkdir()
    (tmp_path / "docs" / "research" / "2026-01-01-999-x-research.md").write_text(
        "a\n\nb\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "citing.md").write_text(
        "`docs/architecture/OUT.md:2`\n`docs/research/2026-01-01-999-x-research.md:2`\n",
        encoding="utf-8",
    )

    problems = _citation_problems(docs=tmp_path / "docs")

    assert len(problems) == 1, problems
    assert "research" in problems[0] and "architecture" not in problems[0], problems


def test_a_date_fragment_is_not_a_shorthand_citation(tmp_path: Path):
    """A DATE beside the word `research:`/`scoping:` is not a label.

    Without the `(?<![\\w-])` boundary on `_SHORT_REF`, the prose
    `the 2026-08-13 research:99 note` matches `13 research:99`, which resolves to
    EVERY `docs/research/` record carrying the `-13-` token — 6 at this head (and
    16 in `docs/scoping/`, measured, one of them dated `2026-10-13` rather than
    `2026-08-13`) — and reds a CORRECT sentence naming those files. The guard must
    see no citation here at all.
    """
    (tmp_path / "docs" / "scoping").mkdir(parents=True)
    for name in ("2026-08-13-265-a-scoping.md", "2026-08-13-307-b-scoping.md"):
        (tmp_path / "docs" / "scoping" / name).write_text("a\nb\n", encoding="utf-8")
    (tmp_path / "docs" / "citing.md").write_text(
        "Per the 2026-08-13 research:99 note and the 2026-08-13 scoping:99 list.\n",
        encoding="utf-8",
    )

    assert _citation_problems(docs=tmp_path / "docs") == []


def test_a_nested_record_resolves_by_its_path_label(tmp_path: Path):
    """`docs/research/` is NESTED — the number can live in the DIRECTORY.

    The `#265` brief this change edits is
    `docs/research/2026-08-13-265-encryption/axis-research.md`: the number is in the
    directory and the FILENAME carries none, so a `*-265-*.md` filename glob (one
    level or recursive) reports a correct shorthand as unresolvable. Resolution is
    by the path's `-<number>-` token instead (see `_resolve_shorthand` for the
    three shapes that still fail closed).
    """
    nested = tmp_path / "docs" / "research" / "2026-08-13-265-encryption"
    nested.mkdir(parents=True)
    (nested / "axis-research.md").write_text("first line\nsecond line\n", encoding="utf-8")
    (tmp_path / "docs" / "citing.md").write_text(
        "see `265` research:2 for the carve-out\n", encoding="utf-8"
    )

    assert _citation_problems(docs=tmp_path / "docs") == []

    # …and the arm is not vacuous: the same shorthand at a BLANK line is reported,
    # naming the nested file, so the recursive resolution really did find it.
    (nested / "axis-research.md").write_text("first line\n\nthird line\n", encoding="utf-8")
    problems = _citation_problems(docs=tmp_path / "docs")
    assert len(problems) == 1, problems
    assert "axis-research.md:2 (line 2 is BLANK)" in problems[0], problems


def test_the_real_docs_tree_is_not_empty():
    """A mis-rooted DOCS path would make every check above vacuous."""
    assert DOCS.is_dir()
    assert len(list(DOCS.rglob("*.md"))) > 100


#: ⛔ ONE GAP, SIX UNIT KINDS, NO BUDGET — AND `_GAP_UNIT` IS THE LIST (#13921 review, F5).
#:
#: This block used to describe TWO gaps: a WIDE one for the FULL command
#: (`git diff --name-only origin/main...HEAD -- tortoise/`) budgeted at 40 units of text that
#: stopped at `;` and `|`, and a NARROW one for the ABBREVIATION (`-- tortoise/`). Both were
#: retired by the verification round that measured the prose admission fail-open, and this
#: write-up was left behind describing them: there is ONE gap, it has NO budget, and `;`, `|`
#: and `.` are ordinary single characters (#13921 review, P2-3). What survives from that split
#: is the REASON it existed — the abbreviation is a SUBSTRING of every longer `git diff <other
#: revs> -- tortoise/` invocation — and it is honoured by the SUBJECT LIST the callers pass
#: (`_TORTOISE_CENSUS_SUBJECTS`), not by two gap constants. `_GAP_FULL` / `_GAP_ABBREV` are
#: gone: a second name for the same closure is what let the selection between the two be a
#: no-op that kept these paragraphs alive.
#:
#: The gap is a run of SIX UNIT KINDS and nothing else. Listing them is not an enumeration of
#: SPELLINGS — the earlier attempts died because they enumerated those — it is the closure
#: itself, stated once, and each kind is defined structurally:
#:
#:   1. a single character — a non-word one, plus `_`, which Python counts as a word character
#:      and markdown emphasis depends on. `&`, `<` and `[` are EXCLUDED from this class: each of
#:      them has its own unit below, so each has exactly one way to parse. ⛔ THAT SENTENCE WAS
#:      FALSE FOR `<` UNTIL `_OPEN_TAG` (#13921 review, finding 1, MEASURED): `<` reached only
#:      `_TAG`, which requires a closing `>`, so an UNTERMINATED `<` had ZERO ways to parse and the
#:      walk stopped on it — a fail-open the `&` row and `_OPEN_BRACKET` were both added under.
#:      It is true now because the pair is disjoint, not because it was ever argued to be;
#:   2. a TAG — any run between `<` and `>` in which a QUOTED attribute value is one unit (so
#:      `title="a > b"` does not close early: CommonMark allows any character but the matching
#:      quote inside a quoted value) and in which a SOFT break is allowed but a BLANK line is
#:      not: `_TAG` below;
#:   3. a BRACKETED run — `[...]`, which covers `[^1]`, `[^note]`, `[^A]`, `[12]` and a markdown
#:      link label alike; a run of DIGITS or of LETTERS is not spelled separately anywhere;
#:   4. an AMPERSAND — `_AMP` below, which is EITHER an entity (`&...;` with `[#\w]{1,10}`
#:      inside, covering `&nbsp;`, `&#160;` and `&#xA0;`) OR a bare `&` that does not begin one.
#:      The single-character unit excludes `&`, which is reached ONLY through `_AMP`, whose two
#:      branches a negative lookahead keeps mutually exclusive, so `&` has one parse.
#:      ⛔ THE BARE BRANCH IS NOT DECORATION (#13900 review, cycles 9-13, MEASURED): the first
#:      form of this unit dropped it and a claim went GREEN. ⛔ THE LIVE WITNESS IS THE BARE-`&`
#:      ROW IN `test_the_bundles_tortoise_set_answers_are_not_typed_against_the_tree` —
#:      ``... ` & prints TWO paths``, MEASURED at #13921: `[('prints TWO paths', False)]` with
#:      the branch and `[]` without it, so deleting the branch REDS there. ⛔ NOT the `R&D` row:
#:      that one carries a WORD (`R`, `D`) between the command and the verb, which is this gap's
#:      DECLARED out-of-scope form (see the `_GAP_UNIT` block), so it returns `[]` whether or
#:      not the branch exists and CANNOT red for it — naming it as this branch's witness points
#:      a future fixer at a row that can never fail (#13921 review, P2, MEASURED; corrected
#:      here, same over-stated-attribution class as `_BRACKET` and the `[is]` row). `&` is a
#:      character `origin/main`'s gap already admitted, and a closure that drops a form the
#:      previous rule had is a regression wearing a widening's clothes.
#:   5. an UNTERMINATED bracket — `_OPEN_BRACKET` below, a `[` with no `]` (bare or ESCAPED)
#:      reachable from it;
#:   6. an UNTERMINATED tag — `_OPEN_TAG` below, a `<` that `_TAG` cannot parse. That INCLUDES a
#:      `<` with no reachable `>` (which is what a bare `<` in a separator is) but is NOT limited
#:      to it: `_TAG`'s catch-all excludes `"`, so `<"` with a later `>` also reaches here
#:      (#13921 review, finding 2, MEASURED — the previous wording said "no reachable `>`", which
#:      was a smaller unit than the code needs and than `origin/main` had).
#:
#: ⛔ TWO SAME-FIRST-CHARACTER PAIRS, AND ORDER — NOT A LOOKAHEAD — IS WHAT KEEPS EACH PAIR
#: DISJOINT. AND WHAT KEEPS THE RUN FROM BEING EXPONENTIAL IS EACH UNIT'S OWN BRANCHES, NOT EITHER
#: (#13921 review, findings 1-4, MEASURED). `_BRACKET`/`_OPEN_BRACKET` both begin `[` and
#: `_TAG`/`_OPEN_TAG` both begin `<`, so the reason this block used to give — "each kind begins with
#: a unique first character" — is FALSE, and a stale reason in this file is the defect class it
#: keeps reviewing itself for. (`_OPEN_TAG` makes this TWO pairs, not one: the previous revision of
#: this paragraph called `[` "the ONE same-first-character pair", and the `<` hole below is what
#: that miscount was hiding.) The real reason each pair is disjoint is that the SECOND branch
#: reaches only when the first cannot: `_BRACKET` is tried first and requires a closer, and
#: `_OPEN_BRACKET` is a bare `[`; `_TAG` is tried first and requires the `>`, and `_OPEN_TAG` is
#: a bare `<`. ⛔ NEITHER SIBLING HAS A GUARD, AND THE BRACKET ONE WAS DELETED AS INERT (#13921
#: review after cycle 2, finding 4, MEASURED). Its lookahead asked the SAME question as
#: `_BRACKET` over the SAME alphabet — a strict subset of `_BRACKET`'s interior — so wherever
#: `_BRACKET` fails, no path through the subset reaches a `]` either and the negative lookahead is
#: ALWAYS satisfied. MEASURED: replacing it with a bare `[` changed no answer across 69,904 fuzz
#: cases and HALVED the run (the ONE copy of that figure lives beside `_OPEN_BRACKET` below;
#: do not restate it here). ⛔ An earlier revision of this
#: paragraph said "the lookahead is what keeps each pair disjoint" and named a `_OPEN_TAG`
#: lookahead that an earlier round DELETED — the tag half of that sentence described code that no
#: longer existed, one commit after the sentence was written (#13921 review, finding 3).
#: The BRACKET pair's two branches NEVER both start: `_BRACKET` is tried first and the closing
#: unit takes `[a\n]` WHOLE, so the bare `[` is not reached there at all (MEASURED — an earlier
#: form of this sentence said "the one input where the BRACKET pair can both start is `[a\n]`",
#: which its own second clause disproved, #13921 review, final round, finding 1). What DOES differ
#: between them is ALPHABETICAL: `_BRACKET`'s interior admits `_SOFT` and the bare `[` is one
#: character. That difference is INERT — it can only matter where `_BRACKET` already failed, and
#: then there is no closer to take — so a soft-break interior is a bounded residual, not an
#: exponential.
#: ⛔ AND THE RUN IS **NOT** LINEAR — the word this block used to carry, on the strength of a
#: single 200-point reading (#13921 review, finding 4). MEASURED at this head: 200 × `[` ->
#: 0.0011 s, 400 -> 0.0040, 800 -> 0.0152, 1600 -> 0.0612, 3200 -> 0.2332 s — about x3.9 per
#: DOUBLING, i.e. ~n^2, because at every `[` the closing unit scans to the end and fails, and a
#: run of `[` is ~n such starts. 200 × `<` behaves the same way (0.0009 -> 0.1896 s over the same
#: range): `_TAG` scans the run and fails, then the bare `<` unit takes one character.
#: ⛔ AND IT IS BRANCH DISJOINTNESS THAT REMOVED THE EXPONENT, NOT ANY LOOKAHEAD (#13921 review,
#: finding 5). `_BRACKET`'s overlapping branches held a 2^n that this file's walk made reachable
#: on every `[`; making the branches disjoint removed it, and a lookahead is family with the atomic
#: group this block names as a MASK rather than a fix. The five `[` points were RE-MEASURED after
#: that fix and after the inert guard was dropped; 3200 repeated brackets is not a shape a
#: document reaches; the honest statement is ~n^2, measured at these five points.
#:
#: A SOFT BREAK is not a paragraph break (`7557a52cf5`'s measurement, kept): a single newline is
#: crossed, a BLANK line is the boundary — and a blank line ends the gap with no guard of its
#: own, because `_SOFT` refuses to cross one and the single-character unit excludes `\n`.
#: ⛔ THE `<` RESIDUAL IS ATTACHED TO THE INPUT THAT HAS IT (#13921 review, finding 3, MEASURED).
#: This paragraph used to say "the cost of a tag probe on an UNTERMINATED `<` run (measured 28
#: characters -> 0.19 s, growing about x1.7 per character)" — and a bare `<` run is not that
#: input: 200 `<` takes 0.0009 s and scales ~n^2 like every other pair above (the `_TAG` probe
#: scans the run and fails, then the bare `<` unit takes one character). The exponential
#: belongs to a tag that OPENS and never CLOSES with a QUOTE inside it, `<a` + a run of `'`, where
#: the quoted branch and the catch-all both consume each quote. ⛔ THE BRACKET PAIR'S SIBLING
#: OVERLAP IS NOW GONE (#13921 review, finding 1 — disjoint branches, flat at every n); the TAG's
#: is the one that REMAINS, and it is DECLARED. MEASURED at this head on the exact input the
#: timing row below uses (`"`-- tortoise/`" + "<a" + "'" * n`, three runs, min): n=20 ->
#: 0.0016 s, 24 -> 0.0107, 26 -> 0.0283, 28 -> 0.0682, 32 -> 0.5154 s (a x1.6-1.7 step per
#: character). ⛔ THESE FIGURES ARE STATED HERE AND NOWHERE ELSE (#13921 review, finding 3):
#: the timing row below used to restate them and the two copies had drifted 2.3x apart —
#: 0.194 s vs 0.0841 s for the SAME input at the SAME head — which is exactly the rot the module
#: docstring records for a count typed into prose. That row now POINTS HERE; if you re-measure,
#: correct this line and do not start a second copy.
#: That residual is DECLARED, not fixed, and it is pinned by its own timing witness at
#: the `< 2.0 s` row, which uses the SAME input (`"<a" + "'" * 28`) — a future reader who guards
#: "a `<` run" would guard the wrong shape and conclude the residual was closed. Otherwise the
#: measured cost is flat: 8/16/28/40/80/200 spaces and 40 repeated units of each kind ->
#: 0.000-0.002 s; the ambiguous form `7557a52cf5` replaced took 15.8 s at 24 spaces.
_AMP = r"(?:&[#\w]{1,10};|&(?![#\w]{1,10};))"
#: ⛔ A SOFT BREAK IS CROSSED INSIDE A UNIT, NOT ONLY BETWEEN UNITS (#13900 review,
#: verification round, MEASURED). Three holes, each reproduced on the LIVE document:
#:   * a BRACKETED run was `\[[^\]\n]*\]`, newline-excluded — so
#:     "`-- tortoise/` [the census\nsection above] prints FIVE paths" returned `[]`
#:     and the suite stayed `592 passed` over a false claim (the label unwrapped reds);
#:   * a QUOTED attribute value was `"[^"\n]*"`, so a soft break inside a tag's value
#:     was refused although the tag is a unit and the newline rule is B3's;
#:   * the tag catch-all excluded `'`, so `<that row's own path>` — live-document
#:     vocabulary, twice — was refused while `<path>` graded. A LONE apostrophe is
#:     ordinary text: the quoted alternatives are ordered FIRST, so a real quoted run is
#:     still one unit, and anything else falls through to the catch-all.
#: The blank line remains the boundary in every one of them (`(?!...\n)` in each unit).
_SOFT = r"\n(?![^\S\n]*\n)"
_TAG = (r'<(?:"(?:[^"\n]|' + _SOFT + r')*"|\'(?:[^\'\n]|' + _SOFT + r')*\''
        r'|[^>"\n]|' + _SOFT + r')*>')
#: …and an ESCAPED character inside a label is a unit too (`[a\]b]` is one label), which
#: is the same ordering rule as the tag's quoted value: the escape is tried first so the
#: `]` it protects cannot end the run. ⛔ AND THAT ORDERING IS TIE-BREAKING ONLY — it says
#: nothing about PARSE COUNT (#13921 review, finding 1, MEASURED). The catch-all below
#: EXCLUDES `\`, so a backslash is reachable through the escape ALONE: the two branches are
#: DISJOINT and this closure repeats a disjoint set. While it admitted `\` the pair
#: OVERLAPPED and a `[` whose `]` was unreachable matched 2^n ways — and this file's walk,
#: which calls `_unit_pat.match` at EVERY position, must therefore evaluate `_BRACKET` on
#: every `[`, where the single-pass gap this replaced could skip it. MEASURED through that
#: walk, `[` + 20 backslashes took 0.0196 s, 24 -> 0.0973, 28 -> 0.5960, 32 -> 3.8566 s
#: (≈x1.6 per character, i.e. a hang with no diagnostic and no witness that reds), and
#: 0.00004 s flat at every one of them once the pair is disjoint. Disjoint branches is the
#: remedy the ReDoS literature prescribes for exactly this shape (each character parses one
#: way); an atomic group would mask the ambiguity without removing it.
#: ⛔ AND THE CLOSER HAS TWO ALTERNATIVES, `\]` OR AN ESCAPED `\]` (#13921 review, finding 1 of
#: the NEXT round, MEASURED). Excluding `\` from the catch-all moves a backslash from
#: ORDINARY CHARACTER to ESCAPE INTRODUCER, so `[a\]` — a label whose closer is written `\]` —
#: stopped closing: `_BRACKET` consumed `a` then `\]` as an escape and ran out of text, so the
#: walk stopped and `origin/main` (which graded it THROUGH the very ambiguity being removed) had
#: an admission this head did not. MEASURED on a 30,941-case punctuation sweep of the ABBREVIATION
#: subject, where a bare word character cannot sit in the gap at all: head differed from
#: `origin/main` on exactly FOUR rows and every one was `[` + a word + `\]` (`[a\]`, `[1\]`,
#: `[x\]`, `[b\]`); with the escaped closer below the same sweep differs on ZERO. So the closer
#: is not decoration — it is what keeps this unit's ADMISSIONS equal to the rule it replaced,
#: while the repetition alone stays disjoint so the exponent stays gone (measured flat at 800
#: backslashes, and `[\]`-runs get FASTER, not slower, because the label now closes on the
#: first try instead of failing and re-scanning).
_BRACKET = r"\[(?:\\.|[^\\\]\n]|" + _SOFT + r")*(?:\]|\\\])"
#: ⛔ ONE GAP, UNITS ONLY — NO PROSE (#13900 verification, MEASURED). A prose-crossing gap
#: was tried and it flags FIVE live-document sentences, two of them QUOTATIONS of refuted
#: claims ("`prints nothing` would over-claim") which the arm's contract says must not be read
#: as assertions: telling a quotation from an assertion is semantics, not punctuation, and no
#: gap can do it. It also LATCHED on the `is`/`has`/`:` inside its own separator and dropped
#: the claim, and it backtracked exponentially where `[` began two units (16 × `[\]` = 22.8 s).
#: So a WORD between the command and the verb is NOT DETECTED — a declared limit, pinned by TWO
#: witnesses (the `R&D` row, and this PR's own two latching reproductions), and the unit kinds
#: above are what may sit there instead. ⛔ AND THAT DECLARATION IS WHY #13921'S BODY HAD TO BE
#: CORRECTED: those two reproductions are NOT graded by this gap and this PR no longer claims
#: they are (#13921 review, F2/P1-1) — what the retry fixes is a listed verb sitting INSIDE the
#: separator (`is`, a URL's `:`), not a word between the command and the verb.
#: ⛔ AND THE LOOKAHEAD MUST LEARN THE SAME INTERIOR AS `_BRACKET` (#13921 review, finding 1).
#: `(?![^\]\n]*\])` counted a `\` as ORDINARY, so it asked "is a `]` reachable?" under a
#: different grammar from the unit it guards. ⛔ THE SHAPE WHERE THAT BITES IS `[a\]`, NOT
#: `[a\]b]` AS AN EARLIER FORM OF THIS COMMENT SAID (#13921 review, finding 2 of the next round,
#: MEASURED): `[a\]b]` grades under EVERY variant including `origin/main`, because `_BRACKET` is
#: tried first and takes it whole, so the two grammars' disagreement on it is INERT; and `[a\]b`
#: is `[]` under every variant including `origin/main`, because the unclosed label exposes a word
#: character to the declared prose stop. Naming those two is what hid the row that does bite.
#: ⛔ AND THE LOOKAHEAD ITSELF IS NOW GONE, BECAUSE IT WAS INERT (#13921 review after cycle 2,
#: finding 4, MEASURED). Once its alphabet was made the SAME as `_BRACKET`'s interior, it asked a
#: question `_BRACKET` had already answered: that alphabet is a strict SUBSET of the interior's,
#: so wherever `_BRACKET` fails no path through the subset reaches a `]` either, and the negative
#: lookahead is ALWAYS satisfied at the positions this constant is reached. MEASURED: replacing it
#: with a bare `[` changed NO answer in 69,904 fuzz cases and HALVED the run (`[` x3200 0.3987 ->
#: 0.2212 s). So this pair's disjointness is ORDER alone, exactly as the `<` pair's is: `_BRACKET`
#: is tried first and requires its closer. ⛔ Do not re-add a lookahead over `_BRACKET`'s interior:
#: it cannot change an outcome and it doubles the scan.
_OPEN_BRACKET = r"\["
#: ⛔ AND AN UNTERMINATED TAG IS ONE TOO (#13921 review, finding 1, MEASURED). `_TAG` requires a
#: closing `>`, so BEFORE this constant a bare `<` matched NOTHING in the alternation and the
#: separator walk stopped dead on it: `` `-- tortoise/` < prints TWO paths `` returned `[]` at
#: `b19c056151`, while `origin/main`'s `_GAP_ABBREV` (`[^\w&\n]`, which admitted `<`) graded
#: it. That is a fail-open regression wearing a widening's clothes — the same argument the `&`
#: branch and `_OPEN_BRACKET` were added under — and the comment at item 1 asserting that `<`
#: "has exactly one way to parse" was FALSE for an unterminated one: it had ZERO.
#: The ORDER is what keeps the pair disjoint — `_TAG` is tried first and requires the `>`, so
#: a `<` reaches this alternative only when `_TAG` cannot parse. ⛔ AND THE LOOKAHEAD THAT USED
#: TO GUARD IT WAS ITSELF A REGRESSION, SO IT IS GONE (#13921 review, finding 2, MEASURED):
#: `_OPEN_TAG` is a bare `<`. `(?![^>\n]*>)` asserted MORE than "_TAG failed" — it REFUSED a
#: `<` whose `>` WAS reachable but which `_TAG` still could not parse, and `_TAG`'s catch-all
#: excludes `"`, so an UNBALANCED `"` did exactly that: at `8f108b5211` the separators `<">`,
#: `<…!">` and `<"-$>→` returned `[]` where `origin/main` GRADED them. A 30,000-case
#: punctuation fuzz found 10 such losses and ALL 10 were `<` + an unbalanced `"` + a later `>`;
#: every other difference carried a word character, i.e. the DECLARED prose limit. With the bare
#: `<` those three grade again and **0 of 24 witness rows moved** — `<` unterminated, terminated
#: tags and tag-over-soft-break stay byte-identical. Dropping the lookahead cannot buy back an
#: `_OPEN_BRACKET`-style residual: a bare `<` is ONE character, so there is nothing left to scan
#: to the end of a line. A tag whose interior crosses a SOFT break (`<span\ntitle="a">`) is
#: taken whole by `_TAG` as before, so that declared residual is untouched.
_OPEN_TAG = r"<"
#: ⛔ ONE GAP, ONE UNIT LIST, AND THE UNITS ARE WHAT THE MATCHER WALKS (#13921 review, F5).
#: `_GAP_UNIT` is the single alternation every separator character goes through; the separator is
#: its `*`-CLOSURE, and the matcher walks that closure unit by unit (below) instead of letting a
#: greedy `_GAP*` skip it — so the position list the walk produces is what decides where a verb
#: may start. There is no `_GAP` constant to drift from `_GAP_UNIT`: a second name for the same
#: closure is what let `_GAP_FULL` / `_GAP_ABBREV` alias it and two gaps go on being described.
_GAP_UNIT = (r"(?:[^\w<&[\n]|_|" + _TAG + r"|" + _BRACKET + r"|" + _AMP + r"|"
             + _OPEN_BRACKET + r"|" + _OPEN_TAG + r"|" + _SOFT + r")")

def _typed_answers_to_tortoise_cmd(doc: str, cmd: str) -> list[tuple[str, bool]]:
    """Every typed ANSWER to `cmd` in `doc`, as `(answer, claims_empty)` pairs.

    The SHAPE is what is matched — the command, then an ANSWER FORM immediately after it
    (`prints`, `returns`, `yields`, `reports`, `gives`, `outputs`, `carries`, `lists`,
    `shows`, `names`, `includes`, `contains`, `has`, `->`, `→`, `is`, `=`, `:`), then the
    answer — because a literal list cannot hold the CLASS this exists for (#13728 review,
    finding 4): `returns nothing` and `is empty at this head` are one-word variations of
    the refused `prints NOTHING`, and `carries no `tortoise/` path at all`, `lists the two
    COMMENT-ONLY modules` and `names two `tortoise/` paths` are the same answer in another
    listed verb.

    ⛔ THE ANSWER-FORM LIST IS FINITE ON PURPOSE, AND THAT IS THE CONTRACT (#13795 review,
    finding 2). A match on the answer's CONTENT alone — no form required — cannot tell an
    ASSERTION from a QUOTED RETRACTION, and this document carries both: it quotes
    `"returns exactly those two"` and marks it FALSE in every half, and it says the command
    `... and only refuses those spellings when the command actually prints a path`, where
    the answer sits mid-sentence. Reading either as a live membership claim would red the
    document for retracting a falsehood. So the class covered here is "the same answer,
    behind another LISTED form", NOT "any English sentence whose object is the same": a
    re-wording onto a verb outside this list is not detected. Said plainly because a guard
    may be narrow, but may not describe itself as complete.

    `claims_empty` says which DIRECTION the answer asserts, and it is read from the HEAD of
    the answer — an optional leading negation, then the FIRST answer token — never from any
    word that happens to sit in the window (#13795 review, round 3, C1/C2). Scanning the
    whole window was wrong in BOTH directions, measured: `prints the two comment-only
    modules and nothing else` was read as EMPTINESS (the trailing `nothing` won) and a
    refuted membership claim stayed green; `prints NOTHING at this head, though it was not
    empty at `2a7f24e241`` was read as MEMBERSHIP (the contrast clause's `not` won) and a
    TRUE emptiness claim was refused. Both are one defect: a qualifying word that binds a
    different predicate, or a different head, was deciding the answer's direction.

    So a head negation (`is NOT empty`, `never empty`, `isn't empty`) INVERTS the head
    token's direction; `nothing but …` / `none other than …` are MEMBERSHIP in their own
    right; `non-empty`/`nonempty` are MEMBERSHIP without needing the negation rule; and
    anything after the head token is context, not direction. A window whose head token is
    none of these (`is the command that answers it`, `is the command that prints the
    paths`) is not an answer and is skipped — refusing the document's own pointer to the
    command would be the same class of defect in the other direction.

    ⛔ THE MEMBERSHIP WORD LIST IS THE PART THAT WENT STALE (#13795 review, finding 3). It
    read `two`/`three`/`four`/`seven`/`both` — no `one` — so changing this document's own
    `prints NO path at all` to `prints one path at all`, a ONE-WORD edit onto a verb already
    in the list, restored the refuted membership claim and the guard stayed GREEN. The
    numerals `one`…`ten` and `single` are therefore IN the class, and the `one`-word
    mutation is pinned as a witness below.
    """
    answers: list[tuple[str, bool]] = []
    # ⛔ ONE LIMIT THIS CLASS DOES NOT COVER, STATED BECAUSE IT CANNOT BE INFERRED (#13831
    # review): a HARD-WRAPPED ANSWER is not detected — the ANSWER window is cut at the first
    # newline, so `prints only the\ntwo comment-only modules` and `returns\nnothing` are
    # skipped. Pre-existing, and the live document does not wrap; it is named here so the
    # closure claim above is read as "closed under formatting ON ONE LINE". ⛔ THE SEPARATOR
    # IS NOT CUT THERE (#13900 review, cycle 10, P2): a hard-wrapped COMMAND→VERB pair stays
    # graded, because the gap crosses ONE newline through its own soft-break branch
    # (`\n(?![^\S\n]*\n)` — a soft break is not a paragraph break) and `origin/main` graded it
    # too. ⛔ NOT "because `\s` is in the separator class" (#13900 review, P2-3): the
    # single-character unit excludes `\n` in both gaps, so a newline is graded ONLY by that
    # branch — the earlier wording named a mechanism the code no longer had.
    # ⛔ AND A SECOND LIMIT, measured by TWO review cycles on the SAME sentence family
    # (#13845): a claim that names the command by PRONOUN — "the command this clause
    # prescribes returns NOTHING AT ALL" — carries no command token, so no shape rule can
    # reach it; and a command SHORTENED to a fragment is reachable only if the caller passes
    # that fragment as `cmd`. Both are handled by the callers (the two literal tripwires in
    # the claim list, and `_typed_answers_to_the_census_command`, which grades EVERY subject
    # in `_TORTOISE_CENSUS_SUBJECTS` rather than the full command alone), NOT by this
    # function — so this docstring states the boundary rather than implying the class is
    # closed over every way a document can point at the command.
    # ⛔ THIS PARAGRAPH NAMED A MECHANISM THAT DID NOT EXIST (#13807 review round 16,
    # MEASURED at `4301ac1e81`): it said the fragment case was handled by "the second
    # `_typed_answers_to_tortoise_cmd` call over `` `-- tortoise/` ``", and there was no
    # second call — the document assertion passed `_TORTOISE_CENSUS_CMD` alone. Two
    # fragment-shaped membership verdicts therefore appended to `05-verify.md` left the
    # whole file GREEN (`14 passed`): `-- tortoise/` returns three modules`, and
    # `-- tortoise/` carries three modules at this head`. The answer words and the verb were
    # BOTH listed; only the SUBJECT was abbreviated, which is the spelling the document uses
    # most. Fixed where the prose said it was, so the sentence and the code agree.
    # The residual is a sentence that refers to the measurement without naming it
    # at all; the live document states those as PAST TENSE with their head named, which is
    # a convention kept by reading, not by an assertion (the label exemption that tried to
    # enforce it mechanically was measured fail-open and false-blocking, and is deleted).
    # ⛔ THE VERB IS SOUGHT INSIDE THE GAP, AND ONLY AT A UNIT BOUNDARY (#13921 review,
    # F2/F3/P1-1/F4, MEASURED). Three facts force this shape:
    #
    #  * The gap is `_GAP_UNIT*` with nothing trailing it, so a greedy `_GAP*` consumes to the
    #    FURTHEST position any unit can reach — a position at which, by construction, no unit can
    #    start. Scanning verbs from there meant the retry never retried: a separator carrying a
    #    listed verb (`… — the row that IS measured above — prints TWO paths`) or a URL's
    #    `protocol:` was still dropped, and the four answer FORMS that are themselves separator
    #    characters (`=`, `:`, `->`, `→`) became UNREACHABLE — a coverage regression against
    #    `origin/main` (#13921 review, F3).
    #  * So the scan starts at the END OF THE COMMAND, and the separator is the TEST: a verb
    #    candidate counts only when everything between the command and it is a whole number of
    #    gap units. That is a strict superset of the old candidate set — the old single position
    #    is still one of the boundaries — and it re-grades `=` / `:` / `->` / `→` as the answer
    #    forms the docstring lists.
    #  * ⛔ A SLICE FULL-MATCH IS NOT ENOUGH TO SAY "UNIT BOUNDARY" (#13921 review, F4, MECHANISM
    #    CORRECTED in the last round, MEASURED): a slice that ENDS INSIDE a unit cannot require
    #    that unit to match — `_BRACKET` needs its `]`, so on the prefix `" ["` the bare `[` unit
    #    takes it and a full-match of the slice succeeds while a unit boundary was never reached,
    #    which would grade the `is` INSIDE the bracket. ⛔ An earlier form of this sentence said
    #    "a unit's LOOKAHEAD cannot see past the slice" — there is no lookahead in that unit any
    #    more (`_OPEN_BRACKET` is a bare `[`), and it added a tag example (`title="a: b"` through
    #    its `:`) that MEASURED does NOT diverge: a slice full-match on that row still yields the
    #    outer `prints`, because the inner `:` produces an unclassifiable head and the classifier
    #    skips it, so it was never a witness for this property. The `[is]` row is. So the
    #    boundaries are
    #    ENUMERATED from the document itself, walking `_GAP_UNIT` with `match`. ⛔ THAT IS THE
    #    **FIRST-MATCH** PARSE, NOT THE CLOSURE'S PARSE, AND THE DIFFERENCE IS DECLARED AND
    #    WITNESSED (#13921 review, final round, finding 2, MEASURED — an earlier form of this
    #    sentence claimed `match` gave "exactly the parse the greedy closure took", which is
    #    FALSE): a `*`-closure BACKTRACKS into a different partition of the same characters —
    #    `[<]a>` is `_BRACKET` whole, or a bare `[` plus the tag `<]a>` — and `match` commits to
    #    the first. MEASURED: `origin/main` graded `[<]a>`, `[<]1>` and `<[>a]`, and this head
    #    returns `[]`, because the committed parse stalls 2 units in on the word character and the
    #    closure's partition is never tried. THREE admissions, and ⛔ THE NESTING RUNS IN EITHER
    #    DIRECTION (#13921 review, final round, finding 2, MEASURED — an earlier form of this
    #    sentence called all three "a bracketed run whose interior contains a tag's `<`", which
    #    describes only two of them): `[<]a>` and `[<]1>` are `_BRACKET` whole (`[<]`, abandoned
    #    partition = a bare `[` plus the tag `<]a>`), while `<[>a]` is `_TAG` whole (`<[>`, abandoned
    #    partition = a bare `<` plus the bracket `[>a]`) — the mirror. `_unit_pat.match` commits to
    #    whichever unit starts first, so the class is "a `[`/`<` run whose interior holds the OTHER
    #    pair's opener", not one direction of it; none occurs in the live tree (all 855 docs
    #    identical to `origin/main`, both subjects), which is why this is DECLARED as a residual
    #    and pinned by its own witness rather than left to drift. A candidate that is not one of
    #    those positions is not this occurrence's verb. That enumeration is also what replaces the blank-line `break` this
    #    function used to carry: `_SOFT` refuses to cross one and the single-character unit
    #    excludes `\n`, so a blank line simply ends the boundary list (pinned by a witness).
    _cmd_pat = re.compile(r"`?(?P<cmd>" + re.escape(cmd) + r")", re.IGNORECASE)
    _unit_pat = re.compile(_GAP_UNIT, re.IGNORECASE)
    _verb_pat = re.compile(
        r"(?:(?:still|now|actually|also)\s+)?"
        r"(prints|returns|yields|reports|gives|outputs|carries|lists|shows|"
        r"names|includes|contains|has|->|→|is|=|:)", re.IGNORECASE)
    for _cm in _cmd_pat.finditer(doc):
      _after_cmd = _cm.end("cmd")
      _bounds = {_after_cmd}
      _pos = _after_cmd
      while True:
          _unit = _unit_pat.match(doc, _pos)
          if _unit is None:
              break
          _pos = _unit.end()
          _bounds.add(_pos)
      for match in _verb_pat.finditer(doc, _after_cmd, min(len(doc), _pos + 200)):
        if match.start() not in _bounds:
            continue
        # ⛔ A COMMAND PINNED TO ANOTHER REV ANSWERS A DIFFERENT QUESTION (#13845 review,
        # cycle 3, P1). The short form a caller may pass is a SUBSTRING of every longer
        # `git diff … -- <pathspec>` invocation, so the tail of a sentence the writer fixed
        # to explicit revs matched here and was graded against THIS head's measurement.
        # That refused two TRUE, head-named sentences on an empty set — measured: `git diff
        # --name-only origin/main...2a7f24e241 -- tortoise/` names exactly
        # `tortoise/activation_scorecard.py` and `tortoise/hosted_api.py` (true at any head),
        # and `git diff --name-only e61694255a...2584a86ec1 -- tortoise/` prints FIVE paths
        # there. Both passed only while this head happened to be non-empty, and both would
        # have been REFUSED on a docs-only head — a false block on the guard whose job is to
        # protect history, the one direction this file's docstring forbids.
        # ⛔ NO SKIP — EVERY OCCURRENCE IS GRADED. Four attempts to tighten a pathspec skip
        # each closed the reported spelling and left another open, so it is gone. NINE
        # spellings defeated it; SIX of them were measured returning `[]` against a rule that
        # actually skipped them, and the other three are kept in the witness list below as
        # the shapes the LAST rule let through. (A review of #13900 measured three of the
        # nine against `origin/main`'s arm and found they were GRADED there, so the honest
        # record is six-plus-three, not nine — the justification does not need the other
        # three, and stating them as skipped would be a claim this file cannot support.)
        # The six, in the order each was found: a dropped backtick; an `—` separator; a `? `
        # (not a sentence break); markdown emphasis around the command; a fenced transcript;
        # and two invocations in one sentence. The three that survived the last rule: no
        # backticks at all; a command split across two code spans; and a bare `git diff`
        # earlier on the SAME line.
        #
        # Prose and a command line are not distinguishable by punctuation, so the arm does
        # not try. The property the skip reached for is kept by the DOCUMENT instead: a claim
        # about another head is written so it is not an answer to the command — PAST TENSE
        # (`returned`, `named`, `printed`, which are not in the verb list). ⛔ NAMING THE HEAD
        # FIRST DOES NOT PROTECT ANYTHING, and that is measured, not argued (#13900 review,
        # cycle 11, P1): `at `2a7f24e241` `git diff … -- tortoise/` names exactly …` is still
        # GRADED, because this matcher reads the VERB and not the head. The convention is
        # stated in the RESIDUAL note at
        # THIS function's head (`_typed_answers_to_tortoise_cmd`, immediately above the
        # matcher) — NOT in the module docstring, which states the NAME-vs-line rule and says
        # nothing about past-tense answers (#13900 review, finding 2) — and it is pinned by the
        # witnesses below, which assert the OPPOSITE of a skip: a claim that is an answer is
        # graded wherever it sits on the line, whatever surrounds it — the gap between the
        # command and the verb is a run of separator UNITS (the SIX kinds stated at the
        # constants, not a character class), with NO budget, ONE gap and ONE boundary:
        # ⛔ NO BUDGET, NO SECOND GAP, AND NO `;`/`|` TERMINATORS (#13921 review, F5). This
        # sentence used to say "the FULL command admits at most 40 units and the ABBREVIATION is
        # unbounded; in both, `;` and `|` stop the wide gap and a `.` does not" — all four
        # clauses false since the prose admission was retired: there is ONE gap, it has no
        # budget, and `;`, `|` and `.` are ordinary single characters. What DOES hold, and is
        # what the witnesses pin: a SOFT break is crossed and a BLANK line is the boundary. ⛔ AND
        # TWO CLAUSES SURVIVED THAT CORRECTION, WHICH MADE THEM FALSE TOO (#13921 review,
        # finding 2, MEASURED): the sentence above them still read "the FOUR kinds" (the
        # constants state SIX — `_OPEN_TAG` was added in this same round) and "the budget and the
        # boundary differ BY SUBJECT" (there is ONE gap: no budget, no per-subject boundary) — a
        # self-contradiction two lines long, in the file whose stated purpose is refusing stale
        # claims, left standing while the sentence BELOW it was rewritten. The setup sentence now
        # states what the constants state, and the count is checked against the constant block by
        # eye on every edit: a count typed into prose rots exactly like a line number. The
        # exceptions are the residuals THIS docstring declares (a hard-wrapped ANSWER, a verb or
        # an answer word outside the listed classes, a pronoun subject), and they are named
        # THERE, not left to be inferred from here.
        #
        # RESIDUAL, stated: a PRESENT-TENSE answer about another head is now refused here.
        # That is the deliberate trade — re-word a sentence, in exchange for a class of
        # fail-open that nine punctation rules could not close.
        rest = doc[match.end():match.end() + 90]
        rest = re.split(r"[.;|)\n]", rest, maxsplit=1)[0].strip()
        window = f"{match.group(1)} {rest}".strip()
        low = rest.lower()
        # ⛔ NORMALISE THE HEAD ONCE (#13795 review, rounds 5-7): markdown emphasis and
        # backticks are not the answer's content, and this document writes both — its answers
        # close with `**` (`prints NOTHING**`, `prints NO path at all**`: two occurrences at
        # the head this was written for) and its paths are backticked after `->`
        # (`-> `tortoise/…``: one). Stripping them here is what makes the class below CLOSED
        # under formatting rather than under one spelling of it; the `**prints** …` and
        # `` prints `two` path `` forms this witness list pins are MUTATIONS of those, not
        # quotations of the tree — stated as mutations so the comment cannot go stale with it.
        low = re.sub(r"[`*_]", "", low).strip()
        # ⛔ PUNCTUATION AFTER THE VERB IS NOT A DIFFERENT ANSWER (#13900 review, cycle 10,
        # P2, MEASURED at `706709c4df` and still open at `47b3ea7001`): `_member` and
        # `_empty` below are anchored at `^`, so `prints — two modules at this head`,
        # `prints: two modules …` and `prints (two modules …` all returned `[]` while
        # `prints two modules …` was graded — a ONE-CHARACTER edit silenced a red, in a
        # document that writes `—` 402 times. The lead here is punctuation, never content,
        # so it is stripped to the first word; a head that is ONLY punctuation leaves an
        # empty window and is skipped as before. Repeated because one mark may sit behind
        # another (`— **nothing**`).
        _lead = None
        while _lead != low:
            _lead = low
            low = re.sub(r"^[^\w\s]+", "", low).strip()
        if not rest:
            continue
        # ⛔ THE HEAD OF THE ANSWER DECIDES, AND NOTHING ELSE DOES (#13795 review,
        # round 3). Scanning the whole window was wrong in BOTH directions, measured:
        # `prints the two comment-only modules and nothing else` was read as EMPTINESS
        # (the trailing `nothing` won) so a refuted membership claim stayed GREEN, and
        # `prints NOTHING at this head, though it was not empty at `2a7f24e241`` was read
        # as MEMBERSHIP (the contrast clause's `not` won) so a TRUE emptiness claim was
        # REFUSED. Both are one defect: a qualifying word that binds a different predicate,
        # or a different head, was deciding the answer's direction. Read the head instead.
        negated = False
        _head_negation = re.match(r"(?:not|never|n['’]t)\b[\s,]*", low)
        if _head_negation:
            negated = True
            low = low[_head_negation.end():]
        if re.match(r"(?:nothing\s+but\b|none\s+other\s+than\b)", low):
            # an emptiness WORD doing membership duty — `nothing but the two modules`
            claims_empty = False
        else:
            # ⛔ QUANTIFIER FILLERS, AND A BACKTICKED PATH TOKEN (#13795 review, round 4).
            # The cut before this one matched a BARE `tortoise/` only, so the document's own
            # spelling — a BACKTICKED path, which is how every path in this file is written
            # — was skipped and a refuted membership answer stayed green; and a quantifier
            # before the numeral (`just one`, `only one`, `at least one`, `no fewer than
            # two`) skipped it the same way.
            # ⛔ FILLERS ARE STRIPPED UNTIL NONE REMAINS — QUANTIFIERS **AND** ARTICLES
            # (#13795 review, round 5; #13728 review, P1). One quantifier pass left
            # `prints now only one path` skipped, because both `now` and `only` sit in
            # front of the numeral. The determiner strip then still ran ONCE, BEFORE this
            # loop, so a quantifier removed inside the loop re-exposed a determiner that
            # nothing removed and the answer fell out of the `continue` below — measured,
            # `prints only the two COMMENT-ONLY modules`, `prints only a single path`,
            # `prints just the two comment-only modules`, `prints at least the two
            # comment-only modules` and `prints exactly the two comment-only modules` were
            # all SKIPPED, while the control `prints the two COMMENT-ONLY modules` (one
            # determiner fewer) classified correctly. That is in-class vocabulary — a
            # listed form, a listed quantifier and the document's own `the two COMMENT-ONLY
            # modules` head — so a refuted membership claim walked through green. The
            # determiner alternation is therefore INSIDE the loop: stripping stops only
            # when a whole pass changes nothing, whichever filler it removed.
            _before = None
            while _before != low:
                _before = low
                low = re.sub(
                    r"^(?:the|a|an|its|this|that|those|these"
                    r"|just|only|still|now|actually|also|at\s+least|at\s+most"
                    r"|more\s+than|no\s+fewer\s+than|exactly)\s+", "", low, count=1)
            low = re.sub(
                r"^(?:prints|returns|yields|reports|gives|outputs|carries|lists|shows"
                r"|names|includes|contains|has)\s+", "", low, count=1)
            # (no leading `` `? ``: :573 already stripped every backtick from `low`, so that
            # branch could never match — dropped rather than left as an inert alternative.)
            _member = re.match(
                r"(?:tortoise/|comment-only\b|non[- ]?empty\b"
                r"|\d+\b|one\b|two\b|three\b|four\b|five\b|six\b|seven\b|eight\b"
                r"|nine\b|ten\b|both\b|single\b)", low)
            # ⛔ A ZERO NUMERAL IS EMPTINESS, FULL STOP (#13795 review, rounds 4 and 5):
            # `prints 0 paths`, `prints exactly zero paths`, `prints 0 files` and
            # `prints 0 lines` are all TRUE at an empty head. The first cut gated it on the
            # following NOUN, so `0 files` fell through to `\d+` and was read as MEMBERSHIP —
            # refusing a true sentence. Zero is emptiness whatever it counts.
            _empty = re.match(
                r"(?:empty\b|nothing\b|none\b|zero\b|blank\b|0\b"
                r"|no\b\s*(?:path|paths|tortoise/))", low)
            if _empty:
                claims_empty = True
            elif _member:
                claims_empty = False
            else:
                continue
            if negated:
                claims_empty = not claims_empty
        answers.append((window, claims_empty))
        break
    return answers


#: The command whose output the `#7949` bundle's census paragraph quotes. Named here (never
#: interpolated from a measurement) because the arm below is a TEXT ban.
def _base_ref_absent_detail() -> str | None:
    """Why THIS checkout cannot see `origin/main`, or ``None`` when it can.

    A reading that cannot be taken is UNPROVABLE, never FALSE. The `test` job checks
    out with `actions/checkout@v4` at its DEFAULT `fetch-depth: 1`
    (`.github/workflows/python-ci.yml:968` has no `with:`; the file states the depth
    itself at `:489`), which fetches ONLY the PR ref
    (`refs/remotes/pull/<N>/merge`) — nothing maps `origin/main`. Measured in that
    exact topology: `git show origin/main:docs/00_index.md` exits 128 with
    `fatal: invalid object name 'origin/main'`, and
    `git rev-parse --is-shallow-repository` prints `true`.

    Failing there would red the REQUIRED `test` job on a CORRECT artifact in every
    pull request in the repo — this file is `core`+`tier1`, so `select()` runs it in
    every PR. That ruling is already recorded for this exact shape, and the shape below
    is copied from it rather than invented: `tests/test_11739_quote_population_probe.py`
    — *"a full clone of `main` (and CI, which checks out `fetch-depth: 1`) can resolve
    neither. **Failing there would red the required `test` job permanently on a correct
    artifact** (review F1) ... So the skip is keyed off what THIS checkout can see, read
    from git rather than from the artifact."*
    """
    try:
        resolved = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "origin/main^{commit}"],
            cwd=REPO, capture_output=True, text=True, check=False)
    except FileNotFoundError:  # no `git` on this host at all
        return "`git` is not installed here, so no ref can be read"
    if resolved.returncode == 0:
        return None
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()
    return (f"`origin/main` does not resolve in this checkout "
            f"(shallow={shallow or 'UNKNOWN'})")


def test_the_cites_this_document_attributes_to_this_diff_are_measured_not_typed():
    """The doc attributed a `docs/00_index.md` cite re-point to THIS diff; it re-points none.

    `05-verify.md` said, in the D5 cell and again in the file-table row, that this
    repair re-points the capstone row's cite (`:21604` -> `:21679` -> `:21715`), and
    that the ONE `docs/00_index.md` line in this diff IS that re-point. Measured at the
    head that merged (#13746), both halves are `main`'s, not the branch's:

        git show origin/main:docs/00_index.md | grep -o 'sdk[.]py:[0-9]*'
        grep -o 'sdk[.]py:[0-9]*' docs/00_index.md
        -> the SAME pair on both sides (`:21715`, `:28178`)
        git show e679d22286:tortoise/sdk.py | cmp - tortoise/sdk.py
        -> DIFFERS at line 45175, so the two blobs are NOT identical — the claim that
           `cmp` prints nothing (round 8, P1) was FALSE. What carries the refutation is
           the RANGE: `:21715` and `:28178` both sit before 45175

    ⛔ Whether the file is in the residual at the head you merge is
    `git diff --name-only origin/main...HEAD -- docs/00_index.md` and
    `git rev-parse origin/main:docs/00_index.md HEAD:docs/00_index.md`, read THERE —
    ⛔ **no answer is typed here, and an earlier cut of this paragraph did type one**,
    which is the document's own defect class applied to the paragraph that names it
    (typing the answer is true of the commit that typed it and false of the next). The
    sentence was the document's own defect class — a typed head-bound figure — applied
    to the document it lives in. It survived several review rounds for
    the same reason the census green did: the D5 arms in
    `test_7949_capstone_measure.py` scope to D5's OWN line, and this claim is in the
    file-table section, so nothing read it.

    NON-VACUITY, MEASURED: on the pre-fix document this arm reds on the FIRST entry
    of `_ATTRIBUTION_PHRASES`, `re-pointing that row's `sdk.py` cite from` — present once in
    `git show f6f202cf5c:…/05-verify.md`, and earlier in that text than the phrase an
    earlier cut of this paragraph named. That named phrase (`and THAT shift is this
    repair's, not main's`) does NOT reproduce: `origin/main` credits the move to
    itself ("and THAT shift is `main`'s, not this repair's"), so it reads 0 there —
    the arm was right and the paragraph was wrong (#13802 review, P2).
    The prohibition is applied only where the measurement supports it: on a head whose
    diff really does move a cite the attribution is legitimate and this leg stands
    down, while the resolution leg — ordered ABOVE this arm so that it runs in the
    depth-1 checkout too (`#13802` review, finding 3) — still has to hold. Where the
    base ref cannot be read at all, the leg takes the UNCONDITIONAL direction rather
    than reporting UNPROVABLE: a `pytest.skip` there would silence the only detector of
    this PR's own defect class in the only job that gates merges (#13802 review, P1).
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    index = (DOCS / "00_index.md").read_text(encoding="utf-8")

    # Every spelling in `_ATTRIBUTION_PHRASES` (module level, beside the live-diff
    # claims it must pair with) asserts that THIS DIFF moved a cite in
    # `docs/00_index.md`, so each is refused while this tree has no measurement that
    # supports it — cite sets equal, or the base ref unreadable (#13802 review P1: the
    # unconditional direction, not a skip).

    # ─────────────────────────────────────────────────────────────────────────────
    # ARMS THAT NEED NO BASE REF, ORDERED FIRST ON PURPOSE (#13802 review, finding 3).
    # Both read THIS tree and THIS document, so they are answerable in EVERY checkout.
    # They stay ABOVE the two cite-set arms because those take the unconditional
    # direction when the base is unreadable (`_base_ref_absent_detail`, #13802 review
    # P1 — the inert-enforcer shape, `426d0c0c45`), and an arm that fails must fail on
    # its own evidence, not on whether a later arm bailed out first.
    # ─────────────────────────────────────────────────────────────────────────────

    # ⛔ HOISTED OUT OF THE SET-EQUALITY GUARD (#13802 review, finding 2 — FAIL-OPEN).
    # Standing the prohibition down when the measurement supports the attribution is
    # defensible; standing THIS requirement down is not. Executed only while the cite
    # sets are EQUAL, it asserted nothing on the normal state of a branch that has not
    # been re-based after `main` re-derived a row (measured at least four times:
    # #13617/#13675, #13724, #13750, #13790 — and NOT #13786, which re-derived four
    # `sdk.py` cites on the TEST side and moved no `docs/00_index.md` row, #13802 review
    # round 5 P3) — i.e. exactly the heads where the
    # reader most needs the command to be there. It is unconditional now.
    assert ("git diff -U0 --word-diff=plain origin/main...HEAD -- docs/00_index.md"
            in doc), (
        "05-verify.md no longer states the command that decides whether this diff "
        "re-points a cite in `docs/00_index.md` — without it the sentence is "
        "uncheckable at the head a reader merges, whatever the diff did "
        "(#13746 review, round 3, P2; hoisted out of the set-equality guard by "
        "#13802 review, finding 2)")

    # A re-point's NEW side has to resolve in the file it lands in, whatever the diff
    # did: the cites the document presents as the row's CURRENT values must name the
    # content the sentence says they name, at this head.
    # ⛔ SCOPED TO WHAT IT ACTUALLY GRADES (#13802 review, finding 2 — P2). This loop
    # used to REQUIRE both cites to be present and to blame `05-verify.md` for
    # "present[ing] them as the row's current value" — a claim the document explicitly
    # refuses to make ("read its value ... at the head you merge, never from this
    # sentence"), and a requirement that reds every PR the moment `main` re-derives that
    # row again (the document records that happening four times). The invariant that IS
    # the tree's: WHEREVER `docs/00_index.md` carries one of these cites, the line it
    # names must be the content that cite is for. A row `main` has moved is then not a
    # false accusation against this document — the cite is simply no longer there.
    _sdk = (REPO / "tortoise" / "sdk.py").read_text(encoding="utf-8").splitlines()
    for _cite, _needle in (
            ("sdk.py:21715", "_capture_stats_with_step_witness("),
            ("sdk.py:28178", "dedup_reviewed IS NULL")):
        if _cite not in index:
            continue
        _n = int(_cite.rsplit(":", 1)[-1])
        assert _needle in _sdk[_n - 1], (
            f"`docs/00_index.md` carries `{_cite}`, but `tortoise/sdk.py:{_n}` is "
            f"{_sdk[_n - 1].strip()!r} — that cite does not name the content it is "
            "for, so a reader who follows it lands on an unrelated line "
            "(#13746 review, round 3, P2; scoped by #13802 review, finding 2)")

    # ⛔ THE RETIRED SIDE OF THE SAME PAIR, AND IT NEEDS NO BASE REF EITHER (#13802
    # review, P2 — the leg `main`'s deleted arm carried, measured open at the pre-fix head:
    # injecting `sdk.py:21604` alongside the surviving `sdk.py:21715` left this suite green).
    # A re-point moves a cite FORWARD, so the index carrying one of these again means the
    # file was reverted or a stale figure was pasted back in — either way a reader follows a
    # cite to the wrong line. If this reds on a deliberate revert, update `_RETIRED_INDEX_CITES`
    # and say which head restored it; do not widen the tuple to "the current value".
    _resurrected = [c for c in _RETIRED_INDEX_CITES if c in index]
    assert not _resurrected, (
        f"`docs/00_index.md` carries the RETIRED cite(s) {_resurrected!r} again, "
        "alongside or instead of the value the row was re-pointed to. A re-point moves a "
        "cite forward only, so this is either a revert or a pasted-back stale figure: a "
        "reader following it lands on a line that does not name that row's content "
        "(#13802 review, P2 — the absence leg `main`'s deleted arm carried, restored here)")

    # ─────────────────────────────────────────────────────────────────────────────
    # ARMS THAT NEED THE BASE REF (#13802 review, finding 3 — P1, blocking).
    #
    # ⛔ THIS READ USED TO FAIL CLOSED, AND THAT REDED THE REQUIRED `test` JOB IN
    # EVERY PR IN THE REPO. `assert probe.returncode == 0` on
    # `git show origin/main:docs/00_index.md` is unfailable-and-green on a full clone
    # and unpassable on the depth-1 PR checkout `python-ci.yml` uses, so the fix for a
    # documentation-claim defect broke the merge rail repo-wide — this file is
    # `core`+`tier1`, and `select()` unions `tier1` into EVERY PR selection. Dropping
    # the arm is NOT the repair (it is the only thing that measures the cite sets);
    # raising the shared `test` job's checkout depth is not either (a full-history
    # fetch on all 11 shards). Key it off what THIS checkout can see, per
    # `_base_ref_absent_detail` and the `#11739` precedent it names.
    # ─────────────────────────────────────────────────────────────────────────────
    _absent = _base_ref_absent_detail()
    # ⛔ NOT A `pytest.skip` (#13802 review, P1 — THE INERT-ENFORCER SHAPE). Every job
    # that runs a suite checks out at `actions/checkout@v4`'s DEFAULT `fetch-depth: 1`
    # (`python-ci.yml:968` is bare inside the `test` job; `post-merge-validation.yml`
    # declares no `fetch-depth` at all — `grep -c fetch-depth` -> 0 — and its `:96`
    # passes a `ref:` only, while `:222` is bare), so `origin/main` never resolves on the rail and a skip here means this arm
    # asserts NOTHING in the only place the rail reads it. MEASURED on a real depth-1
    # clone of this branch with the document's attribution flipped back to "and THAT
    # shift is this repair's, not `main`'s`: `11 passed, 2 skipped` AT THAT HEAD —
    # green, defect swallowed. ⛔ The counts are THAT head's, not this file's: this
    # suite has grown since (a later round added arms), so re-run the probe rather
    # than carrying a figure that was true once — the rule this row's own sibling in
    # `config/ci-surfaces.yml` states, which is why that copy was deleted outright. The fallback is the UNCONDITIONAL direction instead — the same
    # shape this file's own `config/ci-surfaces.yml` row now records for these two
    # legs: with the base unreadable, NOTHING shows this diff moves a cite, so no
    # attribution may be asserted. (The sibling `-- tortoise/` leg needs no base at
    # all any more — it is a TEXT BAN, `#13802` review findings 1 and 2.) It keeps the file hermetic (its row
    # promises "no network", so fetching a base ref inside the suite is not available
    # to it) and the trade it buys is an OVER-BLOCK — a head whose diff really does
    # move a cite would red in a depth-1 checkout — which outranks a false PASS.
    if _absent is None:
        probe = subprocess.run(
            ["git", "show", "origin/main:docs/00_index.md"],
            cwd=REPO, capture_output=True, text=True, check=False)
        # `origin/main` RESOLVES here, so a failed read is no longer an environment
        # fact — it is an artifact fault, and it still fails closed.
        assert probe.returncode == 0, (
            "`origin/main` resolves in this checkout, but `docs/00_index.md` cannot "
            "be read at it — `git show origin/main:docs/00_index.md` exits "
            f"{probe.returncode} ({probe.stderr.strip()!r}). That is an artifact "
            "fault, not a shallow clone, so this arm fails closed "
            "(#13802 review, finding 3)")
        _main = sorted(re.findall(r"sdk\.py:(\d+)", probe.stdout))
        _head = sorted(re.findall(r"sdk\.py:(\d+)", index))
        _may_attribute = _main != _head
        _basis = (
            f"that file's cite set is IDENTICAL on `origin/main` and at this head "
            f"({_head}) — no cite moves here, so no re-point is this diff's")
    else:
        _may_attribute = False
        _basis = (
            f"the base ref is UNPROVABLE in this checkout ({_absent}), so nothing "
            "shows this diff moves a cite at all — the depth-1 `test` job is the "
            "normal case, and an unmeasured premise may not carry an attribution")
    if not _may_attribute:
        for phrase in _ATTRIBUTION_PHRASES:
            assert phrase not in doc, (
                f"05-verify.md says this diff re-points a `docs/00_index.md` cite "
                f"({phrase!r}), but {_basis}. State the command (`git diff -U0 "
                "--word-diff=plain origin/main...HEAD -- docs/00_index.md`) and let "
                "the reader re-derive it (#13746 review, round 3, finding 4; the "
                "depth-1 fallback is the unconditional direction, #13802 review P1)")


_TORTOISE_CENSUS_CMD = "git diff --name-only origin/main...HEAD -- tortoise/"

#: The ABBREVIATED subject the bundle uses for that same command, and a SUBJECT of arm
#: `(c-ii)` in its own right (#13807 review round 16, MEASURED at `4301ac1e81`). Ten of the
#: document's `-- tortoise/` occurrences are this form, and the shape arm graded only the
#: full command, so a membership verdict stated on the abbreviation was unread by ANY arm:
#: `_TORTOISE_MEMBERSHIP_SPELLINGS` covers the spellings that were LIVE, one row per incident
#: (`returns ONE module`, round 5), which is a list that grows after each miss. The matcher
#: already reads this subject correctly; only the call was missing — so it is called.
#: ⛔ THERE IS NO PATHSpec SKIP ANY MORE (#13900 review, finding 3): every occurrence is
#: graded, and the reason is recorded at the matcher. A claim about another head is written
#: so it is not an answer to the command. PAST TENSE is the only form that does that, and it
#: is measured (#13900 review, cycle 10, finding 5): naming the head FIRST does not help,
#: because the matcher reads the VERB — `at `2a7f24e241` `git diff … -- tortoise/` names
#: exactly …` is still GRADED and still refused. So the convention this arm relies on is the
#: convention the document keeps, and it is the half this arm relies on now.
_TORTOISE_CENSUS_CMD_ABBREV = "-- tortoise/"

#: Every SUBJECT arm `(c-ii)` grades. A tuple, not two calls at the assertion, so that a row
#: added here is graded without a second edit and a row deleted here reds the witness below.
_TORTOISE_CENSUS_SUBJECTS = (_TORTOISE_CENSUS_CMD, _TORTOISE_CENSUS_CMD_ABBREV)


def _typed_answers_to_the_census_command(doc: str) -> list[tuple[str, bool]]:
    """Every typed answer to the census command in `doc`, under EVERY subject it is named by.

    ⛔ WHY THIS EXISTS (#13807 review round 16). `_typed_answers_to_tortoise_cmd` reaches only
    the SUBJECT it is handed, and the claim in its docstring — that a fragment subject was
    "handled by the callers" — was not implemented: MEASURED at `4301ac1e81`, appending
    either of these to `05-verify.md` left the suite GREEN at `14 passed`:

        "`-- tortoise/` returns three modules"
        "`-- tortoise/` carries three modules at this head"

    while the SAME answer under the full command reds. A verdict in either direction is
    retired by this PR's policy anyway (a membership is HEAD-BOUND), so the subject the
    verdict is stated on cannot be the thing that decides whether it is read.

    ⛔ ONE SUBJECT IS DELIBERATELY STILL UNREACHED, stated rather than implied: an answer
    whose SUBJECT is the set named in the PREVIOUS clause — "the `-- tortoise/` set carries
    three modules" — is not detected, because no listed verb follows the token (the same
    finiteness the module docstring declares for verbs and answer words). That form is
    refused by ``_TORTOISE_MEMBERSHIP_SPELLINGS`` when it names a retired spelling; a new
    paraphrase of it is a reading duty, not an assertion, and this docstring is where a
    reviewer should find that said.
    """
    answers: list[tuple[str, bool]] = []
    for _subject in _TORTOISE_CENSUS_SUBJECTS:
        answers.extend(_typed_answers_to_tortoise_cmd(doc, _subject))
    return answers

#: Spellings the `#7949` bundle used to state a `-- tortoise/` MEMBERSHIP verdict in the
#: AFFIRMATIVE direction — that the diff carries these modules. Retired by name.
_TORTOISE_MEMBERSHIP_SPELLINGS = (
    "THREE are in this head",
    "two comment-only `tortoise/` modules",
    "prints exactly `tortoise/activation_scorecard.py`",
    "which two `tortoise/` paths this diff carries",
    "prints the two modules named above",
    "`activation_scorecard.py` IS in the diff",
    f"{_TORTOISE_CENSUS_CMD}` is NOT empty",
    f"{_TORTOISE_CENSUS_CMD}` returns exactly those two",
    f"{_TORTOISE_CENSUS_CMD}` prints the two COMMENT-ONLY modules",
    f"{_TORTOISE_CENSUS_CMD}` prints exactly `tortoise/activation_scorecard.py`",
    "the `tortoise/` set above is the two COMMENT-ONLY modules",
    # ⛔ AND THE AFFIRMATIVE ROW IN THE ABBREVIATED SHAPE (#13807 review round 5, F2 —
    # MEASURED). The ledger carried the abbreviated form in the EMPTINESS direction only
    # (`-- tortoise/` returns NOTHING, below), so the mirror-image affirmative spelling was
    # read by NEITHER arm: (c-i) had no such row, and (c-ii) requires the FULL command, which
    # an abbreviation never matches. It was LIVE in `05-verify.md:134` and green — the same
    # one-sided blindness `#13746` finding 7 recorded, in the abbreviated shape. The row is
    # added together with the document edit that removes the string, because an
    # unconditional ban row would otherwise red the document it polices.
    "`-- tortoise/` returns ONE module",
    # ⛔ AND THE ORDER-REVERSED FORM (#13807 review round 6, F2 — MEASURED, and it was LIVE).
    # Every row above is either a bare phrase or a COMMAND-THEN-ANSWER string, and the
    # answer-form matcher requires the command to PRECEDE the verdict. A sentence that puts
    # the ANSWER FIRST and the command after it therefore slipped past both arms while it was
    # FALSE at the head it described — the spelling below is the one that did, and it survived
    # round 5's abbreviated-command work because that closed the ABBREVIATION axis, not the
    # ORDER axis. Retired by name for that reason; the ordering sensitivity it exposes is
    # DECLARED in `_census_membership_verdicts`'s docstring rather than fixed by teaching the
    # matcher to read prose in either order, because a heuristic that guesses in the permissive
    # direction is a FALSE PASS — the same trade this ledger already states twice.
    "carries `tortoise/` paths",
)
#: Spellings that state the NEGATIVE direction — that the set is EMPTY. Mirror images of the
#: list above, and every bit as carryable: the set is a property of the HEAD the command was
#: run at, so a verdict in either direction is stale one merge later.
_TORTOISE_EMPTY_SPELLINGS = (
    f"neither is `{_TORTOISE_CENSUS_CMD}`",
    f"{_TORTOISE_CENSUS_CMD}` -> empty",
    f"{_TORTOISE_CENSUS_CMD}` prints NOTHING",
    # ⛔ THE TWO THAT WERE STILL LIVE IN THE DOCUMENT (#13807 review round 3, F1, MEASURED).
    # The three rows above retire the spellings that sit BESIDE the command, and the document
    # states the same head-bound emptiness verdict WITHOUT it — a direct hit at
    # `05-verify.md:147` (`at THIS head the `tortoise/` set above is EMPTY`) and at `:139`
    # before this round (`… that command prints NO path at all`). Both were measured GREEN by
    # every arm while present. A ledger scoped to "the command, then the answer" cannot see
    # an answer whose SUBJECT is the set in the previous sentence — which is this document's
    # house style, so the gap is not hypothetical.
    "prints NO path at all",
    "the `tortoise/` set above is EMPTY",
    # ⛔ AND THE ABBREVIATED-COMMAND FORMS (#13807 review round 4, finding 1, MEASURED). The
    # two rows above still assumed the answer sat beside SOMETHING. Four more verdicts were
    # live in the document with the command ABBREVIATED (`-- tortoise/`, `that command`) or
    # omitted altogether, and every arm returned `[]` over them — while a claim that is
    # FALSE at this head in the same abbreviated shape stayed GREEN (`at this head
    # `-- tortoise/` returns ONE module` -> `14 passed`). These four were the live phrasings;
    # they are retired by name for the same reason as every other row.
    "returns NOTHING AT ALL",
    "`-- tortoise/` returns NOTHING",
    "that command returns nothing for all three",
    "contributes no `tortoise/` path at all",
    # ⛔ `"this diff carries no `tortoise/` path"` IS DELIBERATELY **NOT** A ROW — for the
    # OVER-BLOCK, not for a live occurrence: a QUOTED RETRACTION of that claim has to stay
    # legal, because retiring the string would red a document for retracting a falsehood.
    # ⚠️ AND THE COUNTEREXAMPLE THIS NOTE USED TO CITE IS GONE — measured, not assumed (#13802
    # review, cycle 7, P2): it said "that exact string still occurs in the document exactly
    # once, as a QUOTED RETRACTION", and at this head `grep -rn 'this diff carries no
    # `tortoise/` path' docs/` returns NOTHING, while `git grep` at `origin/main` returns ONE
    # — because THIS diff rewrote that retraction sentence (the ban began refusing the
    # spelling it quoted). The carve-out therefore rests on the CLASS and not on an
    # instance, and the note says which, so the next reader does not measure a stale witness.
    # The clause that ASSERTED the claim was rewritten to a structural statement (`#12943`
    # landed on `main`, so it sits on both sides of the merge) that needs no reading of the
    # set at all.
)
#: Both directions, because a ban that covers one of them is the defect these lists record.
_TORTOISE_RETIRED_SPELLINGS = (
    *_TORTOISE_MEMBERSHIP_SPELLINGS,
    *_TORTOISE_EMPTY_SPELLINGS,
)


def _census_membership_verdicts(doc: str) -> list[str]:
    """Every retired `-- tortoise/` membership verdict still spelled in `doc`.

    ⛔ A TEXT BAN, AND THAT IS DELIBERATE (#13807 review, F1, and the false-red direction
    set out in the next paragraph — settled together). The set
    is HEAD-BOUND in both directions: it is a property of the head the command was run at,
    so `#7949`'s own sentences about it go stale on the next merge. Judging them by
    re-measuring the CURRENT head is therefore not a guard but a false-red generator — at
    any head carrying a `tortoise/` path (i.e. essentially every product change) the
    emptiness list would red a sentence that is true of the head it names. And under a
    shallow checkout the measurement is unavailable at all, so a measurement-keyed arm
    asserted NOTHING in the job that gates the merge (F1, measured: the three-dot walk exits
    `128` with `fatal: bad revision`). Reading the document's TEXT needs neither the merge
    base nor a data model, so this arm fires in every environment.

    The classification arm below is the SHAPE half of the same policy
    (`_typed_answers_to_tortoise_cmd`); this one is the ledger of exact spellings that
    actually shipped and survived a merge — including the ones that do not sit beside the
    command, which a shape match cannot reach.

    ⛔ SCOPE: THIS PAIR OF ARMS POLICES ONE COMMAND — `-- tortoise/` — AND NOTHING
    ELSE (#13807 review round 9, P2). The ledger and the shape matcher are both keyed
    to `_TORTOISE_CENSUS_CMD`, so a head-bound verdict about `docs/00_index.md`, the
    epic's `04-plan.md`, or any other path's residual sails past them; a green here is
    the absence of RETIRED `-- tortoise/` SPELLINGS, never a proof that the section
    contains no head-bound claim. Stated so the next lane does not read a green as
    completeness.

    ⛔ THE OVER-BLOCK THIS ACCEPTS, STATED RATHER THAN HIDDEN (#13807 review round 3, F3).
    Matching is an unconditional substring test, so a QUOTED RETRACTION that re-spells one
    of these strings verbatim is refused, where `_typed_answers_to_tortoise_cmd`'s
    answer-form rule deliberately lets it through — carrying a retraction is not asserting
    it. This is a category-B cost (something right is refused) taken over a category-A one
    (something wrong slips through), because the arm it replaces asserted NOTHING at all in
    the gating job. MEASURED, so it is not theoretical: appending `… said the command
    `prints exactly `tortoise/activation_scorecard.py``, and that was FALSE at the head it
    was written for` to the document REDS this arm today.

    The escape is one edit, and it is the one the document already uses everywhere: retract
    WITHOUT re-spelling the retired string (`the sentences that used to sit here are
    DELETED, not softened`), or state the command. A retraction-aware carve-out was
    CONSIDERED AND REJECTED: every version of it is a heuristic over prose (which span is
    inert? which sentence is a retraction?), and a heuristic that guesses wrong in the
    permissive direction is a FALSE PASS — the failure class this whole PR exists to close.
    Narrow, blunt and stated beats wide, clever and silently wrong.

    ⛔ A SECOND OVER-BLOCK, FOUND IN ROUND 4 AND ALSO ACCEPTED (#13807 review round 4,
    finding 3): a TRUE verdict ATTRIBUTED TO A NAMED HEAD — ``at `49dc839715`,
    `git diff --name-only origin/main...HEAD -- tortoise/` prints NOTHING`` — is refused
    too. That is uncomfortable, because the section's own rule is "a count names the head it
    was read at" and this file REQUIRES a named head elsewhere (the `2584a86ec1` assert pins
    exactly that). It is accepted anyway because the same ledger cannot tell an attributed
    verdict from an unattributed one without parsing prose, and the sentence the rule wants
    is one edit away: name the head AND state the command, which is what every surviving
    clause in the section does. Stated here rather than left to be discovered as a bug.

    ⛔ AND THE OPERATIVE BOUNDARY, STATED PLAINLY (#13807 review round 4, finding 1). The
    answer-form matcher requires the FULL command string; this ledger carries the
    abbreviated forms that shipped (`-- tortoise/`, `that command`) as exact rows, in BOTH
    directions — the emptiness row from round 4 and the affirmative row from round 5, the
    latter being the half that was still live and still green when round 4 closed — but a
    FUTURE re-wording onto a NEW phrasing — an abbreviated command this ledger has not seen
    before — is not detected. The class is finite in both arms, and a guard may be narrow;
    what it may not do is describe itself as complete.

    ⛔ AND THE ORDER SENSITIVITY IS DECLARED HERE (#13807 review round 6, F2 — MEASURED).
    `_typed_answers_to_tortoise_cmd` requires the command to PRECEDE the answer, so a
    sentence that states the verdict FIRST and names the command AFTER it is invisible to the
    shape half. That is not hypothetical: one such sentence was live in `05-verify.md` and
    FALSE at its own head, with every arm green, and it survived round 5's
    abbreviated-command work because that closed the ABBREVIATION axis, not the ORDER axis.
    The order half is NOT fixed by teaching the matcher to read prose in either order (a
    permissive heuristic over prose is a FALSE PASS — the same reason a retraction-aware
    carve-out was rejected above); it is closed where it was measured, by retiring that
    spelling BY NAME in the ledger, and this declaration is the part that keeps the next lane
    from mistaking the shape half for complete.
    """
    return [spelling for spelling in _TORTOISE_RETIRED_SPELLINGS if spelling in doc]


def _census_agreement_verdict() -> int:
    """The verdict `--check` prints, measured on the SAME path the verb takes.

    ⛔ `census()` DEFAULT-ARGUMENTS the frozen `DEFAULT_PIVOT` (`census(pivot: int =
    DEFAULT_PIVOT, ...)`), while the verb's `--check` path calls `census(sites[0])` with
    `sites = lifetime_query_sites()`. Measuring `census()` therefore asserted agreement on a
    path the named verb does not take. MEASURED (#13807 review round 7, P1): with the frozen
    literal one line behind the tree, `_sweep(census())` -> `0` while
    `_sweep(census(lifetime_query_sites()[0]))` -> `1` — the arm reported "agreement" over a
    tree the verb it names reported as STALE, i.e. the false PASS the arm exists to refuse,
    reproduced inside the arm. The shape `--check` refuses (more than one site) is refused
    here rather than silently graded on the wrong pivot.

    ⛔ NOT A STYLE CHOICE — a union merge resolution (`49fe860faa`) dropped this derivation
    and restored the frozen call into the merged file, so the repair is pinned by
    `test_the_agreement_arm_reads_the_derived_pivot_not_the_frozen_default` rather than
    left to a reader of the diff.
    """
    from tools.hosted_api_cite_census import _sweep, census, lifetime_query_sites

    _lifetime_sites = lifetime_query_sites()
    assert len(_lifetime_sites) == 1, (
        f"{len(_lifetime_sites)} `query(LIFETIME_MEMORY_QUERY)` sites in "
        f"`tortoise/hosted_api.py` ({_lifetime_sites}). `--check` refuses to run on that "
        "shape, so this arm cannot claim the verdict that verb prints "
        "(#13807 review round 7, P1)")
    return _sweep(census(_lifetime_sites[0]), refresh=False)


def test_the_agreement_arm_reads_the_derived_pivot_not_the_frozen_default(monkeypatch):
    """The agreement arm must measure the pivot `--check` DERIVES, not the frozen literal.

    ⛔ NON-VACUITY IS THE POINT, so the probe does not assume which path is green: it
    replaces `lifetime_query_sites` with a value one line away from `DEFAULT_PIVOT` and
    records the pivot the arm actually passes to `census`. Both legs are required — (a) the
    probe must SEPARATE the two paths, or the test would pass on an arm that ignores the
    pivot, and (b) the arm must take the DERIVED one. Restoring `verdict = _sweep(census())`
    reds (b), which is the regression the `49fe860faa` union resolution introduced.
    """
    from tools import hosted_api_cite_census as census_mod

    derived = census_mod.DEFAULT_PIVOT + 1
    calls: list[int] = []
    _real_census = census_mod.census

    def _spy(pivot: int = census_mod.DEFAULT_PIVOT, *a, **k):
        calls.append(pivot)
        return _real_census(pivot, *a, **k)

    monkeypatch.setattr(census_mod, "lifetime_query_sites", lambda *a, **k: [derived])
    monkeypatch.setattr(census_mod, "census", _spy)
    _census_agreement_verdict()

    assert calls and calls[0] != census_mod.DEFAULT_PIVOT, (
        "the probe did not separate the derived pivot from the frozen `DEFAULT_PIVOT` "
        f"(the arm called `census` with {calls!r}), so this test could pass on an arm that "
        "ignores the pivot entirely")
    assert calls == [derived], (
        "the agreement arm measured `census` with "
        f"{calls!r}; `--check` derives the pivot via `lifetime_query_sites()` "
        f"({derived!r} here), so an arm reading the frozen `DEFAULT_PIVOT` reports agreement "
        "over a tree the verb it names reports as STALE — the false PASS the arm exists to "
        "refuse (#13807 review round 7, P1; re-introduced by the `49fe860faa` union "
        "resolution and pinned here)")


def test_the_bundle_does_not_report_a_census_verdict_it_did_not_measure():
    """⛔ A document reported a checker's EXIT CODE as green while the checker was RED.

    `docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md` stated, in three
    cells, that `tools/hosted_api_cite_census.py --check` "exits 0" / "reports every
    stated figure `== measured`" / "reports both figures `== measured`". MEASURED at
    that head the verb exits **1** with twelve `STALE` lines, which means no merge of
    that branch could have made the claim true.

    ⛔ AND NOTHING READ THOSE CELLS. This file resolves POINTERS (a `file:line` that
    must land on text), and the D5 arms in `test_7949_capstone_measure.py` are scoped
    to D5's own markdown line — so a FALSE PASS survived several review rounds inside
    the document whose subject is stale claims.

    ⛔ THEN THE FIRST FIX FROZE THE OTHER SIGN OF THE SAME COIN (#13750 review, cycle 1,
    MEASURED). That repair pinned the verdict it had measured — "`--check` is RED" and
    "`git diff --name-only origin/main...HEAD -- tortoise/` is empty" — as LITERALS, so
    when this PR's own `--refresh` made the verb exit 0 and put two comment-only
    `tortoise/` modules into the diff, the guard went GREEN over a document asserting a
    red and an empty set the tree no longer showed. A guard that pins a MEASUREMENT has
    to READ the measurement; a frozen sign is the same defect as a frozen number.

    So the arms come in three kinds, and the difference is the point:
      (a) AGREEMENT — the verdict the document states is compared against `_sweep`, the
          census module's OWN checker, run in-process here. A green claim reds on a
          stale tree and a red claim reds on a current one, so neither sign is carryable;
      (b) POINTERS — the document still names the verb (deleting a false verdict must
          send the reader to the command, never away from it) and names every path
          `_note_paths` returns — the two `tortoise/` notes the verb grades and the test
          and tool rows it also lists — which is WHICH files the verdict is about;
      (c) TYPED ANSWERS — the document must type NO answer to the `-- tortoise/` command, in
          EITHER direction, and it is refused FROM THE TEXT ALONE. Arm `(c-i)` refuses the
          recorded SPELLINGS by name (`_census_membership_verdicts`, inline below); arm
          `(c-ii)` refuses the same class by SHAPE — the command followed by one of the
          LISTED answer forms, then an answer — which catches the one-word re-wordings a
          literal list cannot (`test_the_bundles_tortoise_set_answers_are_not_typed_against
          _the_tree`, #13728 review, findings 1 and 4; #13795 review, findings 2 and 3).
          Both directions, because a membership is HEAD-BOUND: the set is a property of the
          head the command was run at, so a verdict either way is stale one merge later, and
          the bundle's own rule is what is enforced — state the COMMAND and read it at the
          head being merged.
          ⛔ NEITHER ARM TAKES A MEASUREMENT, AND THAT IS THE POINT (#13807 review,
          F1, plus the false-red direction settled with it). A measurement-keyed cut judged
          a sentence about ONE head by re-measuring ANOTHER, which turned every head carrying
          a `tortoise/` path — i.e. essentially every product change — into a red on a sentence
          that is true of the head it names; and (F1) its direction came from `git diff
          origin/main...HEAD`, which the depth-1 `test` job cannot answer at all (`rc=128`,
          empty stdout), so it SKIPPED and asserted NOTHING in the only job that gates the
          merge — the inert-enforcer defect these arms replace. Reading TEXT needs no merge
          base, so both arms fire in every shard and no checkout depth can switch them off.
          The shape class stays FINITE and the docstring above says so: a verb outside the
          list, or an answer carrying none of the listed words, is not detected.
    """
    from tools.hosted_api_cite_census import _note_paths

    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")

    # (a) AGREEMENT, measured in-process by the checker the document quotes — ON THE PATH
    # `--check` TAKES. See `_census_agreement_verdict`: `census()`'s default is the FROZEN
    # `DEFAULT_PIVOT`, and measuring that compared the notes against themselves (#13807
    # review round 7, P1). The derivation and its single-site refusal live in the helper so
    # that `test_the_agreement_arm_reads_the_derived_pivot_not_the_frozen_default` can pin
    # the pivot this arm actually passes.
    verdict = _census_agreement_verdict()
    _green_claims = (
        "--check` exits 0",
        "--check` reports every stated figure `== measured`",
        "--check` reports both figures `== measured`",
    )
    # ⛔ BOTH DIRECTIONS ARE FALSIFIABLE BY THE TEXT THAT SURVIVES (#13750 review, cycle 2).
    # An earlier cut tested the red branch only against three frozen `_green_claims`
    # spellings, and the document's LIVE green sentence was not one of them — so a stale
    # tree passed over a document still asserting a green (measured: `_sweep` red on a
    # mutated census, all three spellings absent, `exits 0` present). The two strings below
    # are the ones the document actually uses, so each direction now has a real witness.
    _green_witness = "every stated figure is the measured one"
    if verdict == 0:
        assert _green_witness in doc, (
            "`tools/hosted_api_cite_census.py --check` exits 0 at this head (measured "
            "here, in-process, with the module's own `_sweep`), and 05-verify.md does "
            f"not carry its own output line {_green_witness!r}. State the verdict the verb "
            "prints at the head being merged; a document that keeps a red it no longer "
            "measures sends the next reader to repair drift that does not exist "
            "(#13750 review, cycle 1)")
    else:
        for green in (*_green_claims, "exits 0", _green_witness):
            assert green not in doc, (
                f"05-verify.md asserts {green!r} while `--check` exits {verdict} here "
                "(measured in-process with the module's own `_sweep`). A merge would put "
                "a false PASS claim into a document beside a checker that refutes it. "
                "State the COMMAND and the head it must be read at — never a green "
                "(#13489 review, P0)")

    # (b) POINTERS — the verb, and the modules the verdict is about.
    assert "tools/hosted_api_cite_census.py --check" in doc, (
        "05-verify.md no longer names `tools/hosted_api_cite_census.py --check` at all "
        "— removing a false verdict must send the reader to the verb, not delete the "
        "pointer to it (#13489 review, P0)")
    # ⛔ THE CONSTANT, NOT A SECOND LITERAL (#13807 review round 3, F2). While this was its
    # own copy, editing `_TORTOISE_CENSUS_CMD` left `assert _tortoise_cmd in doc` green
    # against the OLD string, so the ledger policed a command the document never contained.
    _tortoise_cmd = _TORTOISE_CENSUS_CMD
    assert _tortoise_cmd in doc, (
        "05-verify.md no longer states the command that shows which `tortoise/` paths "
        "the census verdict is about. Without it the reader cannot tell whether the two "
        "graded notes are `origin/main`'s or this branch's (#13489 review, P0)")
    for _path in _note_paths():
        assert _path in doc, (
            f"05-verify.md no longer names `{_path}` in the census paragraph. `--refresh` "
            "rewrites exactly those notes, so naming them is what tells the reader WHICH "
            "`tortoise/` modules the verdict is about; the empty-list reading that stood "
            "here before was FALSE at this head (#13750 review, cycle 1)")

    # (c-i) RETIRED SPELLINGS — BOTH directions, refused BY NAME, from the TEXT.
    # ⛔ THE HISTORY THIS ARM MUST CARRY, because each entry is how the other direction went
    # wrong and each was measured on a real head:
    #   • (#13746 review, finding 7) the list was five literals assuming a NON-EMPTY
    #     `-- tortoise/` set. `main`'s own `#13790`/`#13786` then re-took the same census
    #     figures, so at an EMPTY head every one of those five became the TRUE statement
    #     while the AFFIRMATIVE direction — the false one — was read by nothing:
    #     `prints exactly `tortoise/activation_scorecard.py`` and "which two `tortoise/`
    #     paths this diff carries" survived two merges past the head where they became false.
    #   • (#13807 review, F1) the repair that keyed the two lists on a LIVE measurement of
    #     this command was an INERT ENFORCER where it actually runs: every job that runs this
    #     file checks out at the default depth 1, which carries neither `origin/main` nor the
    #     parents a three-dot walk needs, so the probe exits 128 (`fatal: bad revision`),
    #     `_tortoise_measured` is False, and BOTH directions were skipped — zero assertions
    #     in the gating job. The arm returned to making no assertion at all, which is the
    #     exact defect it was written to remove.
    # ⛔ SO IT READS NO MEASUREMENT, AND THAT IS THE FIX (F1 and F4 are settled together).
    # A membership is HEAD-BOUND in both directions: the set is a property of the head the
    # command was run at, so `#7949`'s sentences about it are stale one merge later, AND
    # judging a sentence about THAT head by re-measuring THIS one is a false-red generator —
    # at any head carrying a `tortoise/` path (essentially every product change) the
    # emptiness list would red a sentence that is true of the head it names. The bundle's own
    # rule is the one enforced here: state the COMMAND and read it at the head being merged,
    # never a verdict — so every recorded verdict spelling, in EITHER direction, is retired
    # by name. Being a text ban, it needs no merge base, so it fires in every environment
    # (including the depth-1 shards) and cannot be satisfied by a checkout that cannot
    # answer. This is the SPELLING half; the SHAPE half of the same policy is (c-ii), in
    # `test_the_bundles_tortoise_set_answers_are_not_typed_against_the_tree`.
    _surviving = _census_membership_verdicts(doc)
    assert not _surviving, (
        f"05-verify.md spells a RETIRED `-- tortoise/` membership verdict: {_surviving!r}. "
        f"`{_TORTOISE_CENSUS_CMD}` is HEAD-BOUND — the set is a property of the head it was "
        "run at — so the document states the COMMAND and lets the reader read it at the head "
        "being merged. These are the exact spellings that were FALSE at a head and survived "
        "anyway: the affirmative ones for two merges on `main` because nothing read them "
        "(#13746 review, finding 7), and the emptiness ones because no shallow checkout "
        "could reach the arm that would have read them (#13807 review, F1). ⛔ This is a "
        "ban BY NAME, in both directions: a paraphrase of the same verdict is (c-ii)'s "
        "business, so the fix for a hit is to delete the typed membership and keep the "
        "command.")

    # ⛔ A HISTORICAL STATEMENT MUST NAME THE HEAD IT WAS MEASURED AT (#13489 review, P1).
    # The module claim below is TRUE of the MERGE head and FALSE of this one, so without a
    # head beside it the sentence reads as current — which is exactly how the two module
    # statements above it went stale in the first place.
    _module_claim = [p for p in doc.split("\n\n") if "`#12943` did not touch" in p]
    assert _module_claim, (
        "05-verify.md no longer states which `tortoise/` module `#12943` did not touch — "
        "this leg reads the head that claim was measured at (#13489 review, P1)")
    for _para in _module_claim:
        assert "2584a86ec1" in _para, (
            "05-verify.md states that `activation_scorecard.py` was the only `tortoise/` "
            "module `#12943` did not touch WITHOUT naming the head it was measured at. "
            "That count is the MERGE head's; name it beside the claim (`2584a86ec1`, with "
            "the merge-base command that prints its set) or the two module statements stay "
            "uncheckable (#13489 review, P1)")


    # (c) RETIRED SPELLINGS — the spellings measured FALSE against the tree. The arm below
    # does NOT re-measure to decide which direction is refuted: it refuses the whole class,
    # because a direction claim about that command is head-bound by construction.
    # ⛔ MEASURED (#13746 review, finding 7): this list was five literals assuming a NON-EMPTY
    # `-- tortoise/` set. `main`'s own `#13790` then re-took the same census figures,
    # ⛔ and `#13786` is NOT in that chain (#13802 review, round 5, P3 — MEASURED): its file
    # list is `tests/test_github_indexer.py` and `tortoise/indexer/github_indexer.py`, so it
    # re-derived four `sdk.py` cites on the TEST side and touched no census figure.
    # so at this head the command prints NOTHING and every one of those five spellings became
    # the TRUE statement — the same class of error as the frozen sign this arm replaced
    # (a guard that pins a MEASUREMENT has to READ it). The prohibition is therefore applied
    # only where the measurement supports it.
    # ⛔ THIS IS A TEXT BAN, NOT A MEASUREMENT (#13802 review, findings 1 and 2 — P1+P2).
    # Two failures met in the version this replaces, and they were the same defect read
    # from both sides. It MEASURED the diff and refused a spelling only where the set was
    # non-empty, so: on the depth-1 PR checkout `python-ci.yml` uses (`origin/main`
    # unresolvable) the probe exited rc=128, printed nothing, the set came back EMPTY and
    # every spelling was `.continue`d over — the arm asserted NOTHING in the only job that
    # gates merges (measured on a real `--depth 1` clone: GREEN, defect swallowed
    # and silent) — while on a full clone whose diff DOES carry a `tortoise/` path it
    # refused a sentence that was true at the head it was written for. A prohibition that
    # depends on the diff is inert in one checkout and a false red in the other.
    # So the prohibition is on the DOCUMENT'S TEXT: a direction claim about that command
    # is head-bound by construction (the set moves whenever `main` lands the same
    # re-derivation), which is this row's own thesis one table up. Naming the command and
    # refusing the direction claim therefore holds in EVERY checkout, and needs no base.
    _direction = [
        m.group(0) for m in _TORTOISE_SET_DIRECTION_CLAIM.finditer(doc)]
    assert not _direction, (
        f"05-verify.md states the `-- tortoise/` set's DIRECTION ({_direction[0]!r}) — a "
        "direction claim about that command is a head-bound figure: read it with "
        f"`{_tortoise_cmd}` at the head you merge. State the command and let the reader "
        "run it; a set named here rots the moment `main` lands the same re-derivation "
        "(#13489 review, P1; text ban by #13802 review, findings 1 and 2)")
    # ⛔ AND EVERY ANSWER THE SIBLING SHAPE DETECTOR KNOWS, NOT JUST THIS REGEX'S VERBS
    # (#13802 review, round 4, P2 — MEASURED): at that round this regex named four answer
    # FORMS while the sibling `_typed_answers_to_tortoise_cmd` named fourteen, and a
    # refuted claim spelled with one of the other ten (`reports exactly two paths`,
    # `carries no paths`, `lists one path`, …) was seen by NEITHER arm where it matters.
    # ⛔ THOSE TWO COUNTS ARE HISTORY, NOT THIS HEAD'S (round 6 review, P3 — MEASURED): the
    # regex has since been widened to the detector's whole VERB class, so `reports exactly
    # two paths`, `lists one path`, `shows`/`names`/`includes`/`contains`/`has`/`=`/`:` and
    # `outputs`/`→` all match it here. ⛔ ITS ANSWER CLASS IS NOT A STRICT SUBSET, and the
    # earlier cut of this comment said it was: the example it gave is read by THIS regex too
    # (`none` is one of its alternatives, measured), and the `config/ci-surfaces.yml` row for
    # this file says the opposite of "subset" — **ZERO** detector-only tokens left. What holds
    # is that the ban reads every emptiness / count / bare-verb answer the sibling detector
    # does, `blank` included, so the detector is still asked separately for its DIRECTION
    # LOGIC and its SUBJECT anchoring, never for a wider answer list
    # (#13802 review round 10, P2: the correction re-made the very class it was correcting,
    # against the row it named).
    # ⛔ AND "ZERO" WAS AN ASSERTION UNTIL CYCLE 6 MEASURED IT FALSE (#13802 review, cycle 6,
    # P1): three of the detector's OWN answer forms were read by it and by nothing here —
    # `non empty` (the detector's class is `non[- ]?empty`, a SPACE spelling this pattern did
    # not carry), a bare count word with no noun (`prints one`, `prints both`), and a bare
    # `prints tortoise/`. On the `set`/deictic anchors, where this ban is the ONLY layer,
    # each was a refuted claim no arm refused in ANY checkout — measured `ban=False` against a
    # non-empty detector verdict, and the injected sentence stayed green. All three are read
    # here now (witnesses below pin them), so the residue is a MEASUREMENT — `[]` over the
    # detector's `_member`/`_empty` alphabet — rather than the claim it used to be.
    # measured split (ZERO detector-only forms; `no files` and `no diff` are BAN-only).
    # the sibling arm SKIPS once the base is unreadable (which is every job that gates a
    # merge), and this regex did not match the verb. Measured on the depth-1 topology with
    # `` `…-- tortoise/` reports exactly two paths `` injected: GREEN over a refuted
    # membership claim. Both checks are TEXT-ONLY, so neither needs a base ref and neither
    # can be skipped away; they are kept BESIDE each other because the regex knows the
    # `-- tortoise/` shorthand and a bare verb, while the detector carries the direction
    # LOGIC (`shows`/`names`/`includes`/`contains`/`has`/`=`/`:`) that decides whether an
    # answer is a claim at all.
    # ⛔ AND IN BOTH SPELLINGS OF THE COMMAND (#13802 review, round 5, P2 — MEASURED). The
    # detector anchors on the FULL command, so the document's own shorthand —
    # "`-- tortoise/` reports exactly two paths" — was seen by NEITHER arm: the regex did
    # not then list `reports` (it does now, round 6), and the detector was asked only about
    # the long form.
    # MEASURED on the rail's topology (no `origin/main`): that injected shorthand stayed
    # GREEN over a refuted claim, while the same claim in the long
    # spelling reds. The second element carries its backticks ON PURPOSE: a bare
    # "-- tortoise/" would also match inside the document's head-pinned historical command
    # (`git diff --name-only origin/main...2a7f24e241 -- tortoise/`), and over-block the
    # real document (measured). With them, only the shorthand spelling matches.
    _typed_direction = [
        _answer for _cmd in (_tortoise_cmd, "`-- tortoise/`")
        for _answer, _claims_empty
        in _typed_answers_to_tortoise_cmd(doc, _cmd)]
    assert not _typed_direction, (
        f"05-verify.md TYPES {_typed_direction[0]!r} as the answer to `{_tortoise_cmd}` — "
        "a typed present-tense ANSWER to a head-bound measurement is the defect class this "
        "arm exists for, and it is refused in EVERY checkout because the ban is on the "
        "TEXT, not on a reading this clone may be unable to take (#13802 review, round 4, "
        "P2). State the command and let the reader run it at the head they merge")

class _Unmeasurable(RuntimeError):
    """A MEASURED reading could not be taken here, so its direction is UNKNOWN.

    ⛔ IT NO LONGER DESCRIBES A `-- tortoise/` SET (#13807 review round 4, finding 2). The
    census arm takes no measurement at all now, so nothing measures that set; the one
    surviving reader is `_measured_index_word_diff`, over `docs/00_index.md`. The old
    docstring named a mechanism this file no longer has, which is how a description rots
    into a false claim about the code beside it.
    """


def test_the_tortoise_set_text_ban_reads_the_bundle_documents():
    """The shape ban must have a CALLER, or it enforces nothing (#13802 review, round 8, P1).

    `_TORTOISE_SET_DIRECTION_CLAIM` was measured with exactly two call sites: its own
    definition and the witness test over synthetic strings. No document text was ever passed
    to it, so the class it declares was guarded by nothing — the inert-enforcer defect this
    PR keeps naming, inside the arm written to remove it. Meanwhile `05-verify.md` shipped a
    membership answer the ban would refuse and every live arm returned nothing for it.

    This test is that caller: every `#7949` bundle document is read and matched. A hit is a
    head-bound answer about the `-- tortoise/` set in a document being merged, and the fix is
    the bundle's own rule — state the COMMAND, never the answer. A sentence that NAMES the
    head it is about (a sha, or a `#PR`) is allowed by `_HeadBoundBan`, which is what keeps
    historical and refuted-quote passages readable.
    """
    bundle = sorted((DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone").glob("*.md"))
    bundle += sorted((DOCS / "evidence" / "7949-capstone").glob("*.md"))
    assert len(bundle) >= 10, (
        f"the glob found {len(bundle)} bundle documents — the ban is reading almost nothing, "
        "so its silence would prove nothing")
    _hits: list[str] = []
    for _path in bundle:
        _text = _path.read_text(encoding="utf-8")
        for _m in _TORTOISE_SET_DIRECTION_CLAIM.finditer(_text):
            _hits.append(f"{_path.name}: {_text[max(0, _m.start() - 60):_m.end()]!r}")
    assert not _hits, (
        "a bundle document types an answer about the `-- tortoise/` set, which is HEAD-BOUND "
        f"and must be read from the command at the head being merged: {_hits}. State "
        "`git diff --name-only origin/main...HEAD -- tortoise/` (or name the head the "
        "reading was taken at) and delete the answer (#13802 review, rounds 7 and 8)")


def test_the_bundles_tortoise_set_answers_are_not_typed_against_the_tree():
    """⛔ Refuse a typed answer to the `-- tortoise/` command in BOTH directions.

    This is arm `(c-ii)`: the SHAPE half of the policy whose SPELLING half is
    `_census_membership_verdicts` in the sibling test. A literal list cannot hold the class
    this exists for (#13728 review, finding 4) — `returns nothing` and `is empty at this
    head` are one-word variations of the refused `prints NOTHING` — so the shape is matched
    and every answer found is refused, whichever direction it asserts.

    ⛔ IT TAKES NO MEASUREMENT, AND NEITHER DIRECTION IS JUDGED AGAINST A TREE (#13807
    review, F1 + F4, settled together). The earlier cut read `git diff origin/main...HEAD`
    to pick the direction, which failed twice over: at a depth-1 checkout (every job that
    runs this file, `rc=128`) it could not answer, so the test SKIPPED and asserted nothing
    in the only job that gates the merge — the inert-enforcer defect the arm exists to
    remove — and at any head whose diff carries a `tortoise/` path it refused a sentence
    that is TRUE of the head it names, i.e. a tree-wide red on unrelated product PRs. The
    bundle's own rule settles it: a membership is HEAD-BOUND, so the document states the
    COMMAND and the reader reads it at the head being merged; a typed verdict is refused in
    either direction, from the text alone, in every checkout.

    ⛔ THE NON-VACUITY WITNESSES RUN BEFORE THE DOCUMENT ASSERTIONS, on purpose: they are
    direction-free, so they prove the detector works in every environment. Four paths need a
    witness, and the MEMBERSHIP branch needs its own — it has one now (#13795 review,
    finding 1: deleting or inverting it used to red NOTHING, because every document match
    took the emptiness path).
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    _tortoise_cmd = _TORTOISE_CENSUS_CMD

    # NON-VACUITY (#13728 review, finding 4): the detector must SEE an answer in both
    # directions and must read a NEGATION as a membership claim, or the arm below is inert
    # and its silence proves nothing.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` returns nothing", _tortoise_cmd) == [("returns nothing", True)], (
            "the shape detector does not see `returns nothing` as an EMPTINESS answer, so "
            "the re-wording class this arm exists for would walk through green")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` is NOT empty", _tortoise_cmd) == [("is NOT empty", False)], (
            "the shape detector reads `is NOT empty` as an emptiness claim; it is a "
            "MEMBERSHIP claim, and inverting it would refuse the true sentence and pass "
            "the false one")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` is the command that answers it", _tortoise_cmd) == [], (
            "the shape detector treats `is the command that answers it` as a typed answer, "
            "which would refuse the document's own pointer to the command")
    # ⛔ THE MEMBERSHIP BRANCH'S OWN WITNESS (#13795 review, finding 1). The three asserts
    # above reach the emptiness, negation and skip paths only; at this head no sentence in
    # the document reaches the membership branch either, so DELETE it (or flip it to
    # `claims_empty = True`) and every row stayed GREEN. `prints the two COMMENT-ONLY
    # modules` is the false claim the branch exists for.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints the two COMMENT-ONLY modules", _tortoise_cmd
    ) == [("prints the two COMMENT-ONLY modules", False)], (
        "the shape detector does not read `prints the two COMMENT-ONLY modules` as a "
        "MEMBERSHIP answer, so the membership branch is inert and a false `prints two "
        "modules` claim would walk through green (#13795 review, finding 1)")
    # ⛔ AND THE SYNONYM CLASS (#13795 review, finding 2). `carries` and `names` are forms
    # the closed 8-token verb list missed; the ANSWER's content still decides the direction,
    # so `carries no `tortoise/` path at all` is EMPTINESS while `names two `tortoise/`
    # paths` is MEMBERSHIP — the same answer behind a different listed verb.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` carries no `tortoise/` path at all", _tortoise_cmd
    ) == [("carries no `tortoise/` path at all", True)], (
        "the shape detector does not read `carries no `tortoise/` path at all` as an "
        "EMPTINESS answer — the one-word re-wording the `(ii)` comment names as the reason "
        "a shape match exists (#13795 review, finding 2)")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` names two `tortoise/` paths", _tortoise_cmd
    ) == [("names two `tortoise/` paths", False)], (
        "the shape detector does not read `names two `tortoise/` paths` — a synonym for "
        "`prints` that the closed 8-token list missed (#13795 review, finding 2)")
    # ⛔ THE ONE-WORD ANSWER MUTATION (#13795 review, finding 3). The membership word list
    # stopped at `two`, so this document's own `prints NO path at all` could be edited to
    # `prints one path at all` — ONE word, a verb already in the list — and the guard stayed
    # GREEN over a membership claim the empty set refutes. The witness is that mutation.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints one path at all", _tortoise_cmd
    ) == [("prints one path at all", False)], (
        "the shape detector does not read `prints one path at all` as a MEMBERSHIP answer, "
        "so the `NO` -> `one` one-word edit reinstates the refuted claim green "
        "(#13795 review, finding 3)")
    # ⛔ AND THE DIGIT SPELLING (#13795 review, round-2 finding 1): the docstring says a
    # numeral makes a MEMBERSHIP claim, and a spelled-only list left `prints 2 paths`
    # GREEN over the same refuted claim. Both spellings are the class, so both are pinned.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints 2 paths", _tortoise_cmd
    ) == [("prints 2 paths", False)], (
        "the shape detector does not read `prints 2 paths` as a MEMBERSHIP answer, so the "
        "digit spelling of the `two` claim passes green (#13795 review, round-2 finding 1)")
    # ⛔ AND A HEDGED, TRUE EMPTINESS CLAIM STAYS AN EMPTINESS CLAIM (#13795 review,
    # round-2 finding 2): scoping the negation to any `not` in the window re-classified
    # this document's own house-style hedge as MEMBERSHIP and REFUSED it — pushing an
    # author to delete a true hedge to go green. The negation must bind the emptiness word.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints NOTHING at this head, and that is not evidence the row "
        "was left alone", _tortoise_cmd
    ) == [("prints NOTHING at this head, and that is not evidence the row was left alone",
           True)], (
        "the shape detector reads a HEDGED true emptiness claim as a MEMBERSHIP claim "
        "because an unrelated `not` sits in the window; that refuses the true sentence and "
        "rewards deleting the hedge (#13795 review, round-2 finding 2)")
    # ⛔ AND BOTH DIRECTIONS OF THE SAME ORDERING BUG (#13795 review, round 3, C1/C2): the
    # direction is the HEAD token's, never any word later in the window. A trailing
    # emptiness word must not turn a membership answer into an emptiness claim (C1), and a
    # contrast clause about ANOTHER head must not turn a true emptiness claim into a
    # membership one (C2). Both are pinned because both were measured green/red the wrong
    # way round before the rewrite.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints the two comment-only modules and nothing else",
        _tortoise_cmd
    ) == [("prints the two comment-only modules and nothing else", False)], (
        "the detector reads a trailing `nothing` as the answer's direction, so a refuted "
        "MEMBERSHIP claim passes green at an empty head (#13795 review, round-3 C1)")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints NOTHING at this head, though it was not empty at "
        "`2a7f24e241`", _tortoise_cmd
    ) == [("prints NOTHING at this head, though it was not empty at `2a7f24e241`", True)], (
        "the detector lets a contrast clause about ANOTHER head decide this head's "
        "direction, so a true emptiness claim is refused (#13795 review, round-3 C2)")
    # ⛔ AND THE HEAD-WHITELIST GAPS (#13795 review, round-4 P1/P2). This document writes
    # every path BACKTICKED, and a quantifier or a doubled form may precede the answer — so
    # `prints `tortoise/x.py``, `prints just one path` and `prints contains `tortoise/x.py``
    # were all SKIPPED and a refuted membership answer stayed green. And a ZERO numeral is
    # an EMPTINESS claim, not a member: `prints 0 paths` / `prints exactly zero paths` are
    # TRUE at this empty head and were being REFUSED. Each spelling is pinned.
    for _member_spelling in (
            "prints `tortoise/activation_scorecard.py`",
            "prints just one path",
            "prints only one path",
            "prints at least one path",
            "prints no fewer than two paths",
            "prints contains `tortoise/hosted_api.py`",
            "still prints the two comment-only modules"):
        assert _typed_answers_to_tortoise_cmd(
            f"`{_tortoise_cmd}` {_member_spelling}", _tortoise_cmd
        ) and _typed_answers_to_tortoise_cmd(
            f"`{_tortoise_cmd}` {_member_spelling}", _tortoise_cmd)[0][1] is False, (
            f"the shape detector skips or mis-reads {_member_spelling!r} — a MEMBERSHIP "
            "answer spelled the way this document writes it, so the refuted claim passes "
            "green (#13795 review, round-4 P1)")
    for _zero_spelling in ("prints 0 paths", "prints exactly zero paths", "prints 0 files"):
        assert _typed_answers_to_tortoise_cmd(
            f"`{_tortoise_cmd}` {_zero_spelling}", _tortoise_cmd
        ) == [(_zero_spelling, True)], (
            f"the shape detector reads {_zero_spelling!r} as a MEMBERSHIP claim, so it "
            "REFUSES a true emptiness claim at an empty head — the round-2 hedge harm in "
            "the other direction (#13795 review, round-4 P2)")
    # ⛔ AND FORMATTING IS NOT CONTENT (#13795 review, round 5): the head is normalised once
    # (markdown emphasis and backticks stripped, fillers stripped to a fixpoint, the form
    # matched case-insensitively), because the class this guard declares is "a listed form +
    # a member/emptiness head token", not "one spelling of it". Each variant is pinned.
    # ⛔ AND A FILLER IN FRONT OF A FILLER (#13728 review, P1): the last five spellings put a
    # DETERMINER between the quantifier and the numeral, which is where the fixpoint used to
    # stop — restoring the one-shot determiner strip reds exactly those rows.
    for _formatted_spelling in (
            "prints `two` paths",
            "**prints** the two COMMENT-ONLY modules",
            "_prints_ the two comment-only modules",
            "__prints__ the two comment-only modules",
            "`prints` the two comment-only modules",
            "prints _two_ paths",
            "Prints two paths",
            "prints now only one path",
            "prints only the two COMMENT-ONLY modules",
            "prints only a single path",
            "prints just the two comment-only modules",
            "prints at least the two comment-only modules",
            "prints exactly the two comment-only modules"):
        _seen = _typed_answers_to_tortoise_cmd(
            f"`{_tortoise_cmd}` {_formatted_spelling}", _tortoise_cmd)
        assert _seen and _seen[0][1] is False, (
            f"the shape detector skips or mis-reads {_formatted_spelling!r} — a "
            "MEMBERSHIP answer whose only difference is emphasis, backticks, casing, a "
            "second quantifier or a determiner between the quantifier and the numeral, so "
            "a refuted claim in that format passes green (#13795 review, round-5 P1; "
            "#13728 review, P1)")
    # ⛔ AND THE QUALIFIED NEGATION (#13795 review, finding 3): `non-empty` CONTAINS the
    # word `empty`, and reading it as an emptiness claim inverts the direction — it passes
    # a false membership claim at THIS (empty) head and would refuse the true sentence at a
    # non-empty one. The negation test has to see the qualifier, not just a bare `not`.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` returns a non-empty set", _tortoise_cmd
    ) == [("returns a non-empty set", False)], (
        "the shape detector reads `returns a non-empty set` as an EMPTINESS claim because "
        "the word `empty` sits inside `non-empty`; it is a MEMBERSHIP claim, so the "
        "direction is inverted (#13795 review, finding 3)")
    # ⛔ AND ITS THREE SIBLINGS (#13795 review, finding 4): a CONTRACTION, the HYPHENLESS
    # spelling, and an emptiness word doing membership duty behind `but`. Each carried the
    # inversion or the skip past the fix above, so each is pinned.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` isn't empty", _tortoise_cmd
    ) == [("is n't empty", False)], (
        "the shape detector does not read the CONTRACTION `isn't empty` as a MEMBERSHIP "
        "claim, so the inversion survives the next spelling (#13795 review, finding 4)")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` is nonempty", _tortoise_cmd
    ) == [("is nonempty", False)], (
        "the shape detector does not see `is nonempty` at all — no `not` to find and no "
        "`empty` word boundary inside `nonempty` — so the sentence is skipped and a "
        "membership claim passes green (#13795 review, finding 4)")
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` prints nothing but the two comment-only modules", _tortoise_cmd
    ) == [("prints nothing but the two comment-only modules", False)], (
        "the shape detector reads `prints nothing but the two comment-only modules` as an "
        "EMPTINESS claim; `nothing but` asserts MEMBERSHIP, so a false membership claim "
        "passes green at an empty head (#13795 review, finding 4)")
    # ⛔ AND THE POINTER MUST STILL BE SKIPPED (#13795 review, note 5): a bare
    # `path`/`paths` NOUN is deliberately not a member token, so this sentence is not
    # refused. Losing this would over-block the document's own pointer to the command.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_tortoise_cmd}` is the command that prints the paths", _tortoise_cmd
    ) == [], (
        "the shape detector treats the POINTER `is the command that prints the paths` as a "
        "typed membership answer, which would refuse the document's own pointer to the "
        "command — the same class of defect in the other direction (#13795 review, note 5)")

    # ⛔ EVERY LISTED SUBJECT IS ACTUALLY GRADED, AND THE ABBREVIATED ONE IS THE SUBJECT THAT
    # WAS NOT (#13807 review round 16, MEASURED at `4301ac1e81`). This file's own docstring
    # said a fragment subject was "handled by the callers … the second
    # `_typed_answers_to_tortoise_cmd` call over `` `-- tortoise/` ``" — and no such call
    # existed: the document assertion passed `_TORTOISE_CENSUS_CMD` alone, so TWO
    # membership verdicts in the document's own house spelling appended to `05-verify.md`
    # left the suite GREEN at `14 passed` while the same answer under the full command red
    # it. The witnesses below run over `_typed_answers_to_the_census_command` itself, so
    # dropping a subject from `_TORTOISE_CENSUS_SUBJECTS` — or reverting the document
    # assertion to the single call — reds here rather than going silent, which is the
    # defect class this PR exists to remove (an arm that cannot fire).
    assert _TORTOISE_CENSUS_CMD_ABBREV in _TORTOISE_CENSUS_SUBJECTS, (
        f"the abbreviated subject `-- tortoise/` is no longer graded by arm (c-ii) "
        f"({_TORTOISE_CENSUS_SUBJECTS}). Ten of the `#7949` document's occurrences use it, "
        "and a membership or emptiness verdict stated on it then walks through green "
        "(#13807 review round 16)")
    for _fragment_membership in (
            "`-- tortoise/` returns three modules",
            "`-- tortoise/` carries three modules at this head",
            "`-- tortoise/` prints one path at all",
            "`-- tortoise/` prints the two COMMENT-ONLY modules"):
        _seen = _typed_answers_to_the_census_command(_fragment_membership)
        assert _seen and all(_empty is False for _a, _empty in _seen), (
            f"a MEMBERSHIP verdict on the abbreviated subject walks through green: "
            f"{_fragment_membership!r} -> {_seen}. The verb and the answer words are both in "
            "the studied list; only the SUBJECT is abbreviated, which is how the document "
            "writes it (#13807 review round 16, MEASURED at `4301ac1e81`)")
    # The mirror direction, and a TRUE emptiness claim, must stay classified as emptiness.
    assert _typed_answers_to_the_census_command("`-- tortoise/` returns NOTHING") == [
        ("returns NOTHING", True)], (
        "the abbreviated subject does not read an EMPTINESS verdict as emptiness, so the "
        "fragment pass is inert in the direction the retired rows cover "
        "(#13807 review round 16)")
    # …and the FALSE-RED direction the POINTER RULE exists for. (The pathspec skip that used
    # to be named here is deleted — #13900 review, finding 3 — so a pointer is the only
    # false-red shape left to pin.)
    assert _typed_answers_to_the_census_command(
        "`-- tortoise/` is the command that answers it") == [], (
        "the abbreviated subject treats the document's POINTER as a typed answer, the "
        "over-block direction (#13795 review, note 5)")
    # ⛔ AND THE DELETED SKIP'S OWN WITNESS IS REPLACED, NOT KEPT (#13900 review, finding 3).
    # The row that stood here asserted `== []` on
    #   "See `git diff origin/main...HEAD -- tortoise/` for it."
    # and COULD NOT FAIL: `for` is not in the verb list, so `re.finditer` yields no match and
    # the value is `[]` with the skip PRESENT or ABSENT — MEASURED by extracting this function
    # from `origin/main` and from here and running both (old -> [], new -> []). On `main` it
    # returned `[]` *via* the skip, so it never distinguished the two states. It was presented
    # as the guard that the skip could not widen into silence and guarded nothing
    # (agent-infra#3442 rule 3) — and a comment explaining its failure message cannot make it
    # fail. The row that DOES vary pins the contract the deletion creates: a claim whose
    # subject is the TAIL OF A LONGER `git diff …` invocation is GRADED. Measured, old -> `[]`,
    # new -> one emptiness answer, so reintroducing the skip reds it.
    assert _typed_answers_to_the_census_command(
        "See `git diff origin/main...HEAD -- tortoise/` prints NOTHING for it.") == [
            ("prints NOTHING for it", True)], (
        "the abbreviated subject did not read a claim whose SUBJECT is the TAIL OF A LONGER "
        "INVOCATION as a typed answer — reintroducing a pathspec skip reverses exactly this. "
        "The row this replaced asserted `[]` on the same shape WITHOUT an answer token and "
        "could not red under any mutation (#13900 review, finding 3)")

    # (c-ii) THE DOCUMENT ASSERTIONS — no measurement, so both directions, always.
    # ⛔ A TYPED VERDICT IS REFUSED IN EITHER DIRECTION (#13807 review, F1, and the
    # false-red direction it must not re-create). The set is
    # HEAD-BOUND: it is a property of the head the command was run at, so no direction is
    # "the refuted one" at an arbitrary head — a membership claim is false at a head that
    # prints nothing, and an emptiness claim is false at a head that prints a path, and
    # `#7949`'s sentences are about ONE head while a PR's diff carries paths at ANOTHER. So
    # neither is adjudicated against this tree: the document states the command and types
    # no answer, and this arm enforces exactly that. That is why it needs no merge base and
    # therefore does not SKIP at the depth-1 checkout the gating job uses.
    _typed = _typed_answers_to_the_census_command(doc)
    assert not _typed, (
        f"05-verify.md types {[' '.join(_a.split()) for _a, _e in _typed]!r} as the answer "
        f"to `{_tortoise_cmd}` (or to its `` `-- tortoise/` `` abbreviation) "
        f"({['an EMPTINESS' if _e else 'a MEMBERSHIP' for _a, _e in _typed]}"
        " at this head). That command is HEAD-BOUND — its output is a property of the head it"
        " was run at — so a typed verdict is stale one merge later, in EITHER direction, and"
        " `#7949`'s own sentences about it are true of the head they name rather than of this"
        " one. State the COMMAND and let the reader read it at the head being merged"
        " (#13489 review, P1; #13746 review, finding 7; #13728 review, finding 4; #13807"
        " review, findings 2 and F4)")

    # ⛔ AND THE DOCUMENT'S OWN SHORTHAND IS SCANNED TOO (#13807 review round 16, F1 —
    # MEASURED, and this pass was LOST ONCE by this PR's own conflict resolution against
    # `8bdd34c631`). The document shortens the command to `` `-- tortoise/` `` in several
    # places, and a short-form occurrence never matches the full-command pass — the full
    # string is not a substring of the abbreviation — so without a second SUBJECT the whole
    # abbreviated class is read by NEITHER arm. Measured at `4301ac1e81`, with
    # `git diff --name-only origin/main...HEAD -- tortoise/` printing TWO paths:
    #
    #   doc + "… and the earlier cut said `-- tortoise/` is empty about this bundle …"
    #     ledger (c-i)  -> []      full-command subject -> []      short-form -> [(…, True)]
    #
    # i.e. a FALSE emptiness verdict in the document's own house style was GREEN under both
    # surviving arms. `main`'s `#13845` ran this second call for exactly this reason; the
    # merge that resolved this branch onto `main` dropped it, which is a FAIL-OPEN regression
    # against `main` rather than a hole this branch inherited (#13728 review, finding 4).
    # ⛔ IT IS EXPRESSED AS A SUBJECT ROW, NOT A SECOND HAND-WRITTEN CALL (#13746 review
    # round 16 follow-on — the drop happened ONCE and nothing reded, so the shape is what
    # needs closing, not only the missing line): `_typed` above already carries both
    # subjects from `_TORTOISE_CENSUS_SUBJECTS`, and deleting the abbreviation from that
    # tuple — or reverting the scan to a single literal call — reds the witness block at the
    # top of this arm rather than going silent. `_typed_answers_to_tortoise_cmd` is
    # unchanged; it always read this subject correctly, and only the call was ever missing.
    # The emptiness verdict the lost pass was caught on, kept as the measured witness:
    assert _typed_answers_to_the_census_command(
        "and the earlier cut said `-- tortoise/` is empty about this bundle at that head") == [
            ("is empty about this bundle at that head", True)], (
        "the abbreviated-subject scan does not read the EMPTINESS verdict the lost pass was "
        "restored for, so the regression this arm was repaired for would go unreported "
        "(#13807 review round 16, F1)")

    # ⛔ THIS PASS USED TO BRING A SKIP, AND NOW BRINGS ITS ABSENCE (#13807 review round 16,
    # F2; #13900 review, findings 1 and 3). `"-- tortoise/"` is a SUFFIX of every longer
    # `git diff … -- tortoise/` invocation, so an occurrence that is a pathspec was skipped
    # here: the writer named the revs, which is the head-pinned spelling this document asks
    # for, and this arm cannot fetch that head. Four attempts to keep that skip honest each
    # closed the reported spelling and left another open — the last exempted any claim that
    # merely SHARED A LINE with an unbackticked `git diff` — so it is DELETED, and the
    # blocking direction it protected is now carried by the CONVENTION (a claim about another
    # head is written as one, in PAST TENSE — the named-head-first form was measured NOT to
    # protect anything, since the matcher reads the verb (#13900 review, cycle 10, finding 5)). Both halves of that are
    # pinned below, because the two ways this rule fails are opposite and both silent: a skip
    # that widens to every occurrence looks green while grading nothing, and a skip no
    # witness reaches is dead code nothing reads.
    # ⛔ THE OPPOSITE CONTRACT, PINNED IN NINE SPELLINGS (#13845 review, cycles 3-7). There
    # is no skip: a claim that is an answer to this command is graded wherever it sits on the
    # line and whatever surrounds it. SIX of the nine were measured returning `[]` against
    # the rule that was live at the time; the remaining three are the shapes that survived
    # the LAST rule and are kept as regressions, not as history.
    for _refuted in (
            "Some history here. We ran git diff earlier. `-- tortoise/` prints ONE path at this head.",
            "See `git diff origin/main...HEAD -- docs/` for the docs; `-- tortoise/` prints the two COMMENT-ONLY modules at this head.",
            "We ran git diff --name-only origin/main...HEAD -- docs/ and then `-- tortoise/` prints ONE path at this head.",
            "History — git diff origin/main...HEAD -- docs/ — and `-- tortoise/` names two modules at this head.",
            "Did it change? git diff origin/main...HEAD -- docs/ was clean, and `-- tortoise/` lists two modules at this head.",
            "History: *git diff origin/main...HEAD -- docs/* then *`-- tortoise/` returns ONE path at this head*.",
            "git diff origin/main...HEAD -- docs/ then git diff origin/main...HEAD -- tortoise/ prints ONE path at this head.",
            "(`git diff --name-only origin/main...2a7f24e241` `-- tortoise/` names exactly `tortoise/activation_scorecard.py`)",
            "(`git diff --name-only origin/main...2a7f24e241 -- tortoise/` names exactly `tortoise/activation_scorecard.py`)",
    ):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — it is an answer to the command in a form the arm "
            "must grade. A pathspec skip was measured fail-open in SIX spellings and is "
            "DELETED; if this reds, one was reintroduced (#13845 review, cycles 3-7)")
    # ⛔ AND THE SEPARATOR CASE, which is a hole a review of #13900 MEASURED and this
    # witness now holds shut: a table cell and a parenthetical both came back `[]`.
    for _refuted in ("| `-- tortoise/` | prints ONE path at this head |",
                     "(`-- tortoise/`) prints ONE path at this head.",
                     "`-- tortoise/` | prints ONE path at this head"):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — punctuation between the command and the verb "
            "is still the same claim (#13900 review, finding 2)")
    # ⛔ AND THE TWO SHAPES CYCLE 10 FOUND STILL OPEN (#13900 review, cycle 10, P2,
    # MEASURED at `706709c4df` AND STILL OPEN at `47b3ea7001`): (i) ONE punctuation mark
    # AFTER the verb, which `_member`/`_empty` miss because they are anchored at `^` — a
    # one-character edit silenced a red — and (ii) a HARD-WRAPPED pair, which the separator
    # class excluded by excluding `\n` although `origin/main` graded it. Non-vacuity is
    # MEASURED against `47b3ea7001`'s own function, not asserted: the three lead-punctuation
    # rows and the hard-wrap row each returned `[]` there and are graded here. The last two
    # rows are REGRESSION PINS instead, and say so: the unbounded quantifier that commit
    # shipped already grades them, and they returned `[]` only under the retired `{0,4}`.
    for _refuted in ("`-- tortoise/` prints — two modules at this head",
                     "`-- tortoise/` prints: two modules at this head",
                     "`-- tortoise/` prints (two modules at this head)",
                     "`-- tortoise/`      prints ONE path at this head",
                     "| **`-- tortoise/`** | prints ONE path at this head |"):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — a lead punctuation mark after the verb and a "
            "separator run longer than four are the SAME answer; each returned `[]` before "
            "this fix (#13900 review, cycle 10, P2)")
    # ⛔ AND THE SIX SEPARATOR UNITS, EACH PINNED (#13900 review, cycles 9-13; the count is SIX,
    # not the FOUR this header carried — `_OPEN_BRACKET` and `_OPEN_TAG` are units too, as the
    # constant block states and the rows above witness — #13921 review, finding 3). The unit list IS
    # the closure, so a unit with no witness is a claim and not a test. Non-vacuity is MEASURED
    # against `ff83caa198`'s own function, not asserted, and the ABBREVIATION rows below are SPLIT
    # by what that measurement said: five returned `[]` there (repaired holes), one was already
    # graded (a regression pin). Two earlier forms of the closure bounded the tag at the first
    # `>` and the footnote at `[\^?\d+]`, which is why a tag whose QUOTED attribute carries `>`
    # and a NAMED footnote (`[^note]`, in-house spelling) walked through.
    for _refuted in ("`-- tortoise/`[^note] prints TWO paths",
                     "`-- tortoise/`[^A] prints TWO paths",
                     "`-- tortoise/`&#160;prints TWO paths",
                     "`-- tortoise/`&#xA0;prints TWO paths",
                     '`-- tortoise/`<span title="a > b">prints TWO paths'):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — a bracketed run, a NUMERIC entity and a tag whose "
            "quoted attribute carries `>` are each one separator unit, and each of these "
            "returned `[]` at `ff83caa198` (#13900 review, cycles 9-13)")
    # THIS row is a REGRESSION pin, not a repaired hole: `ff83caa198` graded `[12]` (through the
    # digit-only footnote form) and the structural bracketed unit must keep grading it.
    assert _typed_answers_to_tortoise_cmd(
        "`-- tortoise/`[12] prints TWO paths", "-- tortoise/"), (
        "the BRACKETED unit regressed a spelling the digit-only footnote form already "
        "graded — `[12]` was GREEN-to-RED at `ff83caa198` (#13900 review, cycles 9-13)")
    # …and on the subject that grades a claim about THIS head, where the entity unit was open
    # in the same way (both rows measured `[]` at `ff83caa198`).
    _full_subject = _TORTOISE_CENSUS_CMD
    for _refuted in (f"`{_full_subject}`&#160;prints TWO paths",
                     f"`{_full_subject}`&#xA0;prints TWO paths"):
        assert _typed_answers_to_tortoise_cmd(_refuted, _full_subject), (
            f"the arm SKIPPED {_refuted!r} — a NUMERIC entity between the command and the "
            "verb is a separator, and it returned `[]` at `ff83caa198` "
            "(#13900 review, cycles 9-13)")
    # ⛔ AND `&` IS NOT DROPPED BY THE ENTITY UNIT (#13900 review, cycles 9-13, MEASURED). This is
    # a REGRESSION pin, and it says so: `ff83caa198` GRADED this row (its single-character class
    # admitted `&`), and the first form of the entity unit excluded `&` from that class, so a `&`
    # that forms no entity STOPPED the gap and this claim went GREEN. `_AMP`'s second branch is
    # what keeps the character the previous rule already had.
    _seen_amp = _typed_answers_to_tortoise_cmd(
        f"`{_full_subject}` & prints TWO paths", _full_subject)
    assert _seen_amp and _seen_amp[0][1] is False, (
        "the arm SKIPPED a bare `&` that forms no entity — `&` is a character the previous "
        "rule already admitted, so the entity unit must not narrow the gap below it "
        "(#13900 review, cycles 9-13)")
    # ⛔ `R&D` PUTS A WORD BETWEEN THEM, which is this gap's declared out-of-scope form
    # (#13900 verification): `R` and `D` are word characters. Pinned so the limit is MEASURED —
    # it was graded while the gap admitted prose, and that same width flagged five live-document
    # sentences, two of them quotations of refuted claims.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_full_subject}` R&D prints TWO paths", _full_subject) == [], (
        "a WORD between the command and the verb is now detected — that contradicts the "
        "declared limit in the `_GAP_UNIT` block, so the declaration must change with it")
    # ⛔ THE TWO SENTENCES THIS PR IS TITLED AFTER ARE **NOT** GRADED, AND THAT IS THE DECLARED
    # LIMIT — PINNED, because the claim that they WERE graded is the sentence that had to be
    # withdrawn (#13921 review, F2/P1-1, MEASURED). Each carries a WORD between the command and
    # the verb (`the row that is measured above`, `see`) — exactly the form the unit list refuses —
    # so no retry can reach them: what the retry fixes is a listed verb sitting INSIDE the
    # separator (`is`, a URL's `:`), not a word. Both returned `[]` at `4d3e031710` and both still
    # do. Non-vacuity: widening the single-character unit to admit `\w` reds both this and the
    # `R&D` row above.
    for _unreached in (
        f"`{_full_subject}` \u2014 the row that is measured above\n  \u2014 prints TWO paths at this head.",
        f"`{_full_subject}` \u2014 see https://example.com/13795 \u2014 prints TWO paths",
    ):
        assert _typed_answers_to_tortoise_cmd(_unreached, _full_subject) == [], (
            "a WORD between the command and the verb is now graded — if this ever passes, the "
            "DECLARATION in the `_GAP_UNIT` block must change with it, and #13921's body must "
            "stop describing a prose-crossing gap as the closed half of this repair (#13921 "
            "review, F2)")
    # ⛔ THE ANSWER FORMS THAT ARE THEMSELVES SEPARATOR CHARACTERS ARE REACHABLE AGAIN
    # (#13921 review, F3, MEASURED). `=`, `:`, `->` and `→` are each matched by a
    # SINGLE-CHARACTER unit, so when the scan started at the END of the maximal separator they
    # had already been consumed and all four returned `[]` — while this function's docstring
    # still listed all four as answer forms and `origin/main` graded all four. A shape the
    # previous rule graded going silent is a fail-open regression, which is why this is a
    # REGRESSION pin and not a widening: each row is `[]` at `4d3e031710` (that head's own
    # function, same input) and one emptiness answer here.
    for _form in ("=", ":", "->", "\u2192"):
        _seen_form = _typed_answers_to_tortoise_cmd(
            f"`-- tortoise/` {_form} empty at this head", "-- tortoise/")
        assert _seen_form and _seen_form[0][0].startswith(_form) and _seen_form[0][1] is True, (
            f"the arm SKIPPED the answer form {_form!r} — it is a separator character too, so a "
            "verb scan anchored at the END of the maximal separator can never see it; four forms "
            "`origin/main` graded returned `[]` at `4d3e031710` (#13921 review, F3)")
    # ⛔ AN UNTERMINATED `[` IS ONE UNIT, AND ONLY THIS WITNESS CAN SAY SO (#13921 review, F4,
    # MEASURED): `_OPEN_BRACKET` had NO assertion — deleting it from the unit list reddened
    # NOTHING in this file — so the unit that keeps `origin/main`'s admission of a bare `[` was
    # unfalsifiable, and an unguarded fix is one that reads as coverage and is not. The row reds
    # the moment the unit goes, because `[` is excluded from the single-character class.
    assert _typed_answers_to_tortoise_cmd(
        f"`{_full_subject}` [ prints TWO paths", _full_subject), (
        "an UNTERMINATED `[` ended the separator — `_OPEN_BRACKET` is the unit that admits it, "
        "and `[` is a character `origin/main`'s gap already had (#13921 review, F4)")
    # ⛔ AND A LABEL CLOSED BY AN ESCAPED `]` IS STILL ONE LABEL (#13921 review, finding 1 of the
    # NEXT round, MEASURED). Making `_BRACKET`'s repetition disjoint moved `\` from ORDINARY
    # CHARACTER to ESCAPE INTRODUCER, so `[a\]` stopped closing and the walk stopped on it: this
    # row returned `[]` while `origin/main` AND the previous head `8f108b5211` both graded it —
    # a dropped admission of exactly the class the `&` row and `_OPEN_TAG` were added under, in
    # the file's own named bad direction. MEASURED on a 30,941-case punctuation sweep of the
    # ABBREVIATION subject (where a bare word character cannot sit in the gap at all): this head
    # differed from `origin/main` on FOUR rows, all `[` + a word + `\]`, and on ZERO once the
    # closer learned the escaped form. ⛔ NON-VACUITY, EXECUTED: deleting the `\\\]` closer
    # alternative reds this row; it is the ONLY row that sees it, because every other bracket
    # witness closes with a bare `]`.
    for _escaped in ("[a\\]", "[1\\]", "[x\\]"):
        assert _typed_answers_to_tortoise_cmd(
            f"`` `-- tortoise/` {_escaped} prints TWO paths", "-- tortoise/"), (
            f"the arm SKIPPED {_escaped!r} — a label closed by an ESCAPED `]` is one unit, and "
            "`origin/main` graded this shape; excluding `\\` from `_BRACKET`'s catch-all without "
            "widening its closer drops it (#13921 review, finding 1 of the next round)")
    # ⛔ AND THE WALK'S FIRST-MATCH COMMITMENT HAS THREE DECLARED LOSSES, PINNED HERE (#13921
    # review, final round, finding 2, MEASURED). `_unit_pat.match` commits to the FIRST parse of a
    # position, while a `*`-closure can backtrack into another partition of the same characters; so
    # a `[`/`<` run whose interior holds the OTHER pair's opener is taken as ONE unit whole, stalls
    # on the word character behind it, and the closure's partition is never tried. ⛔ THE NESTING
    # RUNS IN EITHER DIRECTION: `[<]a>` and `[<]1>` are `_BRACKET` whole (`[<]`), while `<[>a]` is
    # `_TAG` whole (`<[>`) — the mirror, not the same shape (MEASURED, correcting an earlier form
    # of this comment that named only the bracketed direction). `origin/main` graded all three of
    # these and this head returns `[]`. They are DECLARED as a residual, not fixed — the fix is to
    # enumerate every partition at `[`/`<`, which is a larger change than this PR carries — and
    # they are pinned so the behaviour cannot drift silently. None occurs in the live tree: all
    # 855 `docs/**/*.md`, both subjects, are identical to `origin/main`. If a future document reds
    # one of these rows, that is the signal to fix the walk rather than to widen this list.
    for _lost in ("[<]a>", "[<]1>", "<[>a]"):
        assert _typed_answers_to_tortoise_cmd(
            f"`` `-- tortoise/` {_lost} prints TWO paths ``", "-- tortoise/") == [], (
            f"the walk now reaches {_lost!r} — the DECLARED first-match residual is gone, which "
            "is good news and means this row (and the comment above it) should be updated, not "
            "that something broke (#13921 review, final round, finding 2)")
    # ⛔ AN UNTERMINATED `<` IS THE SAME CLASS, AND IT WAS STILL OPEN (#13921 review, finding 1,
    # MEASURED). `_TAG` requires a closing `>`, so a bare `<` matched NOTHING in the unit list and
    # the separator walk stopped on it: `` `-- tortoise/` < prints TWO paths `` returned `[]` at
    # `b19c056151`, while `origin/main`'s `_GAP_ABBREV` (`[^\w&\n]`, which admitted `<`)
    # returned `[('prints TWO paths', False)]` — a fail-open REGRESSION, not a
    # declared limit, and the ABBREVIATION is the spelling this document uses most. The row is on
    # BOTH subjects: the abbreviation is the regression `origin/main` proves, and the full command
    # is the widening that closes the same hole there (it was `[]` at `e3f894b31b` too).
    # Non-vacuity, EXECUTED: `_OPEN_TAG` is the only branch that admits `<`, so deleting it from
    # the unit list reds this assertion (measured: `1 failed, 13 passed`, the failure naming the
    # full command's `[]`) — the unit cannot be reverted silently, which is the property
    # `_OPEN_BRACKET`'s row above has and this hole is exactly what its absence cost.
    for _subject in _TORTOISE_CENSUS_SUBJECTS:
        assert _typed_answers_to_tortoise_cmd(
            f"`{_subject}` < prints TWO paths", _subject), (
            f"an UNTERMINATED `<` ended the separator on subject {_subject!r} — `_OPEN_TAG` is "
            "the unit that admits it, `_TAG` requires the `>`, and `<` is a character "
            "`origin/main`'s gap already had (#13921 review, finding 1)")
    # ⛔ AND THE ADMISSION THE LOOKAHEAD USED TO REFUSE GETS ITS OWN WITNESS (#13921 review,
    # finding 2, MEASURED). No row in this file varied on the `<` shape whose `>` IS reachable but
    # which `_TAG` cannot parse, so restoring the dropped lookahead reddened nothing — the same
    # "a unit with no witness is a claim and not a test" failure as the `break` below. `_TAG`'s
    # catch-all excludes `"`, so an UNBALANCED `"` is exactly that shape: `_OPEN_TAG` must take the
    # bare `<` and let the walk continue. MEASURED at `8f108b5211` these three returned `[]`;
    # with the bare `<` they grade. Non-vacuity, EXECUTED: restoring
    # `_OPEN_TAG = r"<(?![^>\n]*>)"` reds this assertion.
    for _refuted in ('<">', '<…!">', '<"-$>→'):
        _seen_tag = _typed_answers_to_tortoise_cmd(
            "`-- tortoise/`" + _refuted + " prints TWO paths", "-- tortoise/")
        assert _seen_tag, (
            f"a `<` whose `>` is reachable but which `_TAG` cannot parse refused the separator: "
            f"separator {_refuted!r} returned `[]` — `_OPEN_TAG` must take the bare `<`, because "
            "its lookahead asserted more than \"_TAG failed\" and dropped an admission `origin/main` "
            "had (#13921 review, finding 2)")
    # ⛔ AND THE RETRY EARNS ITS OWN WITNESS, WHICH ONLY A VERB *INSIDE* A UNIT CAN GIVE
    # (#13921 review, F2/F4, MEASURED). Reducing the scan to its FIRST candidate reddened nothing
    # in this file, because under the old anchor the first candidate was already the occurrence's
    # verb. Now that the scan starts at the command, a verb sitting INSIDE a unit (`[is]`, a tag's
    # quoted `title="a: b"`) comes first and its separator IS a full gap match — it is only not a
    # UNIT BOUNDARY — so the scan must continue past it. Each row must grade the OUTER verb.
    # ⛔ WHICH ROW PROVES THE RETRY, STATED BECAUSE ONLY ONE DOES (#13921 review, mutation table,
    # MEASURED): the `[is]` row. Remove the boundary guard entirely and `[is]` yields
    # `('is ] prints TWO paths', False)` — RED — while the `<span title="a: b">` row stays GREEN,
    # because the inner `:` there produces an unclassifiable head and the classifier skips it. The
    # tag row is a genuine `_TAG` witness (deleting `_TAG` reds it) and it pins that a verb inside
    # a quoted attribute is not TAKEN — but a reader who strengthens the retry's removal test
    # against it would test nothing. ⛔ AND THE `[is]` ROW IS MASKED, so "reddened by the `[is]` row
    # alone" was FALSE (#13921 review, finding 5, MEASURED — corrected here): with the guard
    # removed the FIRST red in file order is the LIVE-DOCUMENT row (`assert not _typed`, which
    # takes 5 spurious answers on 05-verify.md), then `R&D == []` and both `unreached` rows, and
    # only then `[is]`. The property IS caught — but a fixer who strengthens the removal test
    # "against the `[is]` row alone" is working against a claim this suite does not demonstrate,
    # which is the over-stated-attribution class this file already records for `_BRACKET`.
    for _inner, _row in (("[is]", "`-- tortoise/` [is] prints TWO paths"),
                         ('<span title="a: b">',
                          '`-- tortoise/`<span title="a: b">prints TWO paths')):
        _seen_inner = _typed_answers_to_tortoise_cmd(_row, "-- tortoise/")
        assert _seen_inner and _seen_inner[0][0].strip() == "prints TWO paths", (
            f"the arm took a verb INSIDE the unit {_inner}: expected the outer `prints`, got "
            f"{_seen_inner!r} — a separator full-match cannot tell a unit boundary from a "
            "position inside a unit: a slice that ENDS INSIDE a unit cannot require that unit "
            "to match, so `_BRACKET` fails for want of its `]` and the bare `[` takes the prefix "
            "(#13921 review, F4)")
    # ⛔ AND THE `break` GETS A WITNESS, BECAUSE DELETING IT REDS NO OTHER ROW (#13921 review,
    # finding 4, MEASURED). That line is load-bearing: it is what restores ONE answer per command
    # occurrence now that the verb scan starts at the command rather than at the end of the
    # separator. Hence the count, not just the first answer — a two-verb sentence must yield
    # exactly ONE pair. MEASURED: with the `break` this is `1`, without it `2` (the same sentence
    # answered twice, which the live-document arm would then read as two typed verdicts). No row
    # in this file moved when the `break` was deleted, which is this file's own rule — "a unit
    # with no witness is a claim and not a test" — failing in the direction that matters.
    _seen_twice = _typed_answers_to_tortoise_cmd(
        "`-- tortoise/` : prints ONE path : returns TWO paths", "-- tortoise/")
    assert len(_seen_twice) == 1, (
        f"one command occurrence produced {len(_seen_twice)} answers, expected 1: "
        f"{_seen_twice!r} — the per-occurrence `break` is gone, so a second verb in the same "
        "separator run is graded as a second ANSWER (#13921 review, finding 4)")
    # ⛔ A SOFT BREAK IS CROSSED INSIDE A UNIT TOO, AND EACH UNIT KIND GETS ITS OWN WITNESS
    # (#13900 review, verification round, P2, MEASURED). The comment beside `_TAG` / `_BRACKET`
    # says "three holes, each reproduced on the LIVE document" — and before this block NOTHING
    # asserted any of them: reverting all three (the bracket's interior, the quoted attribute's
    # value, the tag catch-all's `'`) left this suite byte-for-byte identical, which is this
    # file's own rule — "a unit with no witness is a claim and not a test" — failing in the
    # direction that matters, because the fix could be silently reverted. Each row is ONE line,
    # and each returned `[]` under the reverted form (measured against the pre-fix constants).
    for _refuted in ("`-- tortoise/` [the census\nsection above] prints FIVE paths",
                     '`-- tortoise/`<span title="a\nb">prints TWO paths',
                     "`-- tortoise/`<that row's own path> prints TWO paths",
                     "`-- tortoise/`[a\\]b] prints TWO paths"):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — a soft break inside a bracket, a soft break inside "
            "a tag's quoted value, a bare apostrophe in a tag and an ESCAPED character in a "
            "label are each one unit; each was measured `[]` when its unit was narrowed "
            "(#13900 review, verification round, P2)")
    # …AND A `;` INSIDE A BRACKET DOES NOT END THE GAP: the bracketed run is ONE unit, so this
    # claim parses only if `_BRACKET` is consumed whole. ⛔ THIS ROW IS NOT WHAT A `_BRACKET`
    # REMOVAL REDS FIRST (#13921 review, finding 5, MEASURED): deleting `_BRACKET` from
    # `_GAP_UNIT` reds the `[^note]` row of the separator-unit block ~190 lines above (it asserts
    # a GRADED answer, so the mutant's `[]` is what reds it — ⛔ not "it asserts `[]`", which an
    # earlier form of this comment said and which inverts which side fails; #13921 review, last
    # round, finding 3), and this row is masked behind it. The
    # earlier form of this comment claimed "Dropping it reds this row and nothing else
    # (measured)" — a measurement claim the suite does not demonstrate, which is the same
    # over-stated-attribution class as the `[is]` row above. The ROW is still worth keeping: it
    # pins `_BRACKET` on an input no other bracket row spells. (#13900 verification: this row used
    # to carry prose — "in the note [a; b] above" — which is the declared out-of-scope form now
    # that the gap is units-only, so the prose is gone and the property it was testing, the
    # bracket unit, is what remains.)
    assert _typed_answers_to_tortoise_cmd(
        f"`{_full_subject}` [a; b] prints TWO paths", _full_subject), (
        "a `;` inside a bracket ended the gap — the bracketed run must be one unit "
        "(#13900 review, verification round, P2)")
    # ⛔ A SOFT BREAK IS GRADED, A PARAGRAPH BREAK IS THE BOUNDARY (kept from `7557a52cf5`,
    # whose measurement this replaces the earlier "hard wrap is a residual" pin with a measured
    # one). A single newline is a CommonMark SOFT BREAK — the claim continues — so the wrapped
    # form must be read. A BLANK line ends the paragraph, and crossing it is how a rule joins
    # two unrelated clauses, so that form must NOT be read.
    assert _typed_answers_to_tortoise_cmd(
        "`-- tortoise/`\nprints ONE path at this head", "-- tortoise/"), (
        "a hard-wrapped answer (one SOFT break) is not graded — it is one claim on two lines")
    assert _typed_answers_to_tortoise_cmd(
        "`-- tortoise/`\n\nprints ONE path at this head", "-- tortoise/") == [], (
        "the gap crossed a BLANK line — a paragraph break is the boundary, and crossing it "
        "joins two unrelated clauses (over-block side)")
    # …and a no-verb run is BOUNDED, which is all this row claims — it is NOT LINEAR, and
    # `_GAP_UNIT`'s block records the five points that say so: at every `[` the closing unit scans
    # to the end and fails, and a run of `[` is ~n such starts, so the run is ~n^2 (#13921 review,
    # finding 3: this line said LINEAR 1,300 lines from a paragraph that says NOT LINEAR). ⛔ IT IS
    # ONE SCAN PER START, NOT TWO: the second was `_OPEN_BRACKET`'s lookahead and it is gone — an
    # inert construct, deleted, which nearly HALVED this cost — the ONE copy of that figure lives
    # beside `_OPEN_BRACKET` above; do not restate it here. `7557a52cf5` measured the earlier
    # ambiguous form as
    # EXPONENTIAL; bounded here so a regression reds instead of hanging, 28 spaces being the size
    # that took 15.8 s there.
    import time as _time
    _t0 = _time.monotonic()
    _typed_answers_to_tortoise_cmd("`-- tortoise/`" + " " * 28 + "no verb here", "-- tortoise/")
    assert _time.monotonic() - _t0 < 2.0, (
        "the separator's alternatives are ambiguous again — 28 spaces after the subject took "
        "longer than 2 s, and at `d38f9152af` 24 spaces alone took 15.8 s (measured)")
    # ⛔ AND THE SAME BOUND FOR AN AMBIGUOUS UNIT, WHICH THE SPACES ROW CANNOT SEE (#13900
    # review, verification round, P2, MEASURED). Spaces are unambiguous, so timing them proves
    # nothing about the alternations that ARE ambiguous. This row pins the TAG's remaining
    # overlap — its catch-all admits `'` that the quoted branch also consumes, so an unterminated
    # `<a''''…` run stays exponential. ⛔ ONE OF THE TWO IS NOW FIXED (#13921 review, finding 1):
    # the bracket pair's overlap is gone and is flat, so THIS is the only exponential left.
    # ⛔ THE FIGURES ARE NOT RESTATED HERE — the declared-residual paragraph beside `_BRACKET`
    # states this exact input's measured growth, and this row points there because two copies had
    # drifted 2.3x apart (#13921 review, finding 3).
    # ⚠️ DECLARED RESIDUAL, not a fixed hole: that cost is EXPONENTIAL in the run length and is
    # left as one, DELIBERATELY — excluding `'` from the tag catch-all changes which inputs parse
    # and has NOT been measured (unlike the bracket pair, which was measured and fixed). What is
    # pinned is that it does not get WORSE, and what is
    # declared is that it exists: the live tree's slowest file is 16.7 ms and 855 files take
    # ~1.0 s, so the hazard is a future document with a long unterminated tag or label, not today's
    # tree. A `docs/` file that reds this row is a RESOURCE regression to be measured, not a red
    # to be silenced.
    _t0 = _time.monotonic()
    _typed_answers_to_tortoise_cmd("`-- tortoise/`<a" + "'" * 28, "-- tortoise/")
    assert _time.monotonic() - _t0 < 2.0, (
        "an ambiguous UNIT's alternatives exploded — an unterminated 28-character tag run took "
        "longer than 2 s; the declared-residual paragraph beside `_BRACKET` states this input's "
        "measured growth and why only the TAG's overlap is left — read it there, not here "
        "(#13921 review, finding 3)")
    # ⛔ AND THE BRACKET PAIR'S OWN EXPONENT GETS THE BOUND ABOVE, BECAUSE ITS FIX HAD NO WITNESS
    # (#13921 review, finding 1, MEASURED). The disjoint-prefix edit that removes the exponent is
    # exactly the kind of change that reads as coverage and is not: reverting `_BRACKET`'s
    # catch-all to admit `\` reddened NOTHING in this file, so the 2^n could come back silently —
    # the same "a unit with no witness is a claim and not a test" failure the `break` had. This is
    # the shape a reader would NOT guard by intuition: `[` is how `_OPEN_BRACKET` is usually
    # exercised, and a bare `[` run is ~n^2, so the reds-from-the-wrong-shape trap is live.
    # MEASURED through the walk: 0.0196 s at 20 backslashes, 0.0973 at 24, 0.5960 at 28, 3.8566 at
    # 32, 42.97 at 36 before the catch-all excluded `\`; ~0.00003 s at every one of them after,
    # 0.0014 s at 3200. Bounded at 2 s, so the exponent can only come back as a RED, never as a
    # hung suite. Non-vacuity, EXECUTED: restoring `[^\]\n]` as the catch-all reds this row.
    _t0 = _time.monotonic()
    _typed_answers_to_tortoise_cmd(
        "`` `-- tortoise/` [ ``" + "\\" * 36 + " prints TWO paths", "-- tortoise/")
    assert _time.monotonic() - _t0 < 2.0, (
        "the BRACKET pair's branches are ambiguous again — `[` + 36 backslashes with no reachable "
        "`]` took longer than 2 s; it measured 42.97 s while `_BRACKET`'s catch-all admitted `\\` "
        "and ~0.00003 s once it excluded it (#13921 review, finding 1)")
    # ⛔ AND THE PUNCTUATION CYCLE 9 MEASURED UNGRADED (#13900 review, cycle 9, P1): each of
    # these returned `[]` when the separator was an enumerated character class.
    for _refuted in ("`-- tortoise/` — prints ONE path at this head.",
                     "`-- tortoise/` – prints ONE path at this head.",
                     "`-- tortoise/`; prints ONE path at this head.",
                     "`-- tortoise/`, prints ONE path at this head.",
                     "`-- tortoise/` / prints ONE path at this head.",
                     "`-- tortoise/` (prints ONE path at this head).",
                     "`-- tortoise/`… prints ONE path at this head.",
                     "`-- tortoise/`[^1] prints ONE path at this head.",
                     "`-- tortoise/`\" prints ONE path at this head."):
        assert _typed_answers_to_tortoise_cmd(_refuted, "-- tortoise/"), (
            f"the arm SKIPPED {_refuted!r} — a separator is not a different claim, and this "
            "one is spelled the way this bundle writes (#13900 review, cycle 9, P1)")
    # …and the control: a standalone short form is still read, so the live question cannot
    # go ungraded.
    assert _typed_answers_to_tortoise_cmd(
        "and `-- tortoise/` prints NOTHING at this head", "-- tortoise/") == [
            ("prints NOTHING at this head", True)], (
        "the arm stopped reading a STANDALONE short-form answer — the live question would go "
        "ungraded (#13845 review, cycle 3, P1)")
    # …and an ANSWER-FREE pointer is still not an answer (the arm must not over-block the
    # document's own pointer to the command). ⛔ THAT ROW ALREADY EXISTS TWICE ABOVE, at the
    # `is the command that prints the paths` assertions in the (c-i) block, with the same
    # input and the same expectation; the third copy this PR's first cut added here was
    # DELETED rather than kept as apparent new coverage (#13900 review, finding 3). A
    # duplicate cannot add discriminating power, and a comment claiming it does is the kind
    # of claim this file exists to refuse.


def test_the_retired_membership_spellings_are_read_in_both_directions(monkeypatch):
    """Arm (c-i) is a TEXT ban, so it fires in every clone — pinned, not reasoned about.

    ⛔ THREE things are pinned here, and none of them is observable from the bundle alone:

    1. **BOTH lists are read, in BOTH directions, and neither may SHRINK** (#13807 review,
       F1; the SHRINK half is finding 5). The cut this replaces keyed the lists on a live
       measurement, so in the depth-1
       checkout the `test` job uses it read neither — and no row anywhere changed colour when
       it was broken, which is why it took a fresh-context reviewer to find it. Each injected
       spelling below reds.
    2. **The CONTENT is pinned, not the size** (#13807 review, finding 5). Length assertions
       left eight of the membership rows unpinned: appending one character to an unpinned
       row kept every count, every membership test and the injection loop green — because the
       loop iterates the MUTATED list — while that retired phrasing could be re-inserted into
       `05-verify.md` uncaught. That is the realistic bypass: a fixer loop "fixing" a doc hit
       by tweaking the spelling. Pinning the tuples WHOLE subsumes the counts.
    3. **The arm must not consult the clone** (F1's root; #13807 review, finding 4). It is a
       text ban, and the pin is BEHAVIOURAL rather than a grep of its source: an
       `inspect.getsource` check was under-inclusive (`subprocess.run` behind an alias, or a
       `_run_git()` helper, contains neither needle) and over-inclusive (a comment mentioning
       `git diff` inside the arm false-reds) — so it could not red the regression it claimed
       to pin. Making `subprocess.run` explode and asserting the arm STILL reds the same
       spelling is the property that actually matters: the document is its only input.

    The measurement the retired cut took is deliberately NOT re-taken: a membership is
    head-bound in both directions, so re-measuring THIS head both asserted nothing at depth 1
    and turned any code PR's head into a false red (#13807 review, F1, and the false-red
    direction recorded in `_census_membership_verdicts`).
    """
    assert (
        "THREE are in this head",
        "two comment-only `tortoise/` modules",
        "prints exactly `tortoise/activation_scorecard.py`",
        "which two `tortoise/` paths this diff carries",
        "prints the two modules named above",
        "`activation_scorecard.py` IS in the diff",
        f"{_TORTOISE_CENSUS_CMD}` is NOT empty",
        f"{_TORTOISE_CENSUS_CMD}` returns exactly those two",
        f"{_TORTOISE_CENSUS_CMD}` prints the two COMMENT-ONLY modules",
        f"{_TORTOISE_CENSUS_CMD}` prints exactly `tortoise/activation_scorecard.py`",
        "the `tortoise/` set above is the two COMMENT-ONLY modules",
        "`-- tortoise/` returns ONE module",
        "carries `tortoise/` paths",
    ) == _TORTOISE_MEMBERSHIP_SPELLINGS, (
        "the affirmative (MEMBERSHIP) ledger changed. These are the exact phrasings that were "
        "FALSE at a head and survived anyway, so a row that is edited, however slightly, is a "
        "ban the document can be re-inserted past (#13746 review, finding 7; #13807 review, "
        "finding 5). Retiring a NEW spelling means adding it; it never means re-wording one.")
    assert (
        f"neither is `{_TORTOISE_CENSUS_CMD}`",
        f"{_TORTOISE_CENSUS_CMD}` -> empty",
        f"{_TORTOISE_CENSUS_CMD}` prints NOTHING",
        "prints NO path at all",
        "the `tortoise/` set above is EMPTY",
        "returns NOTHING AT ALL",
        "`-- tortoise/` returns NOTHING",
        "that command returns nothing for all three",
        "contributes no `tortoise/` path at all",
    ) == _TORTOISE_EMPTY_SPELLINGS, (
        "the emptiness ledger changed — the mirror direction of the same head-bound claim, "
        "retired for the same reason — head-bound in both directions (#13807 review, F1). "
        "The rows after the first three sit beside an ABBREVIATED command or none at all "
        "(#13807 review round 3 F1, round 4 finding 1); they were the answers a "
        "command-scoped ban could not see, and each was measured live in the document.")
    # ⛔ THE COMMAND ITSELF IS PINNED (#13807 review round 3, F2 — MEASURED). Both sides of
    # every `f"{_TORTOISE_CENSUS_CMD}…"` row render from this ONE constant, and the injection
    # loop iterates the tuples, so editing the constant re-renders ledger AND witnesses
    # together and reds NOTHING: measured, retyping it as `-- docz/` left `2 passed` while
    # `_census_membership_verdicts` went blind to the real command (`[]` for a document that
    # spelled it). 3 of 3 emptiness rows and 5 of the membership rows went dead silently. The
    # literal below is the INDEPENDENT copy that makes the drift red.
    assert _TORTOISE_CENSUS_CMD == "git diff --name-only origin/main...HEAD -- tortoise/", (
        "`_TORTOISE_CENSUS_CMD` no longer names the command the `#7949` bundle quotes, so the "
        "ledger above now polices a string the document does not contain and the ban is "
        "blind to the real command (#13807 review round 3, F2)")

    # ⛔ AND THE ROW TEXT IS PINNED BY SOMETHING THE EDIT CANNOT REACH (#13807 review round
    # 5, F2 — MEASURED). The whole-tuple pins above are each a byte-identical copy of the
    # ledger they check, so the realistic bypass is a GLOBAL edit: `sed -i 's/all three/all
    # THREE/'` rewrites both copies, the tuple still equals itself, the test passes — and the
    # ledger now carries a spelling the document no longer contains, so the ban returns `[]`
    # over the retired phrasing. Deleting a row from both copies behaves the same way. A
    # digest is independent DATA, not a second copy of the same text: it cannot be edited by
    # a substitution that does not also deliberately recompute it. A legitimate ledger change
    # therefore updates the ledger, its readable tuple AND this digest — which is the point.
    assert hashlib.sha256("\n".join(_TORTOISE_MEMBERSHIP_SPELLINGS).encode()).hexdigest() == (
        "641b5c03277de43cf4410a8743029da3af02a56685b0b6c11be69bfe54b5943e"), (
        "the membership ledger's TEXT changed. If that was deliberate, recompute this digest "
        "and the tuple above; if it was a find-and-replace across the file, it just silently "
        "narrowed the ban (#13807 review round 5, F2)")
    assert hashlib.sha256("\n".join(_TORTOISE_EMPTY_SPELLINGS).encode()).hexdigest() == (
        "15aae3a6f9bfe9ec0c1b3690834ccaaa755e8fc8f4a9bedaadae5d49a56cb276"), (
        "the emptiness ledger's TEXT changed. If that was deliberate, recompute this digest "
        "and the tuple above; if it was a find-and-replace across the file, it just silently "
        "narrowed the ban (#13807 review round 5, F2)")

    for _spelling in _TORTOISE_RETIRED_SPELLINGS:
        _injected = f"… and the earlier cut said {_spelling} about this bundle …"
        assert _spelling in _census_membership_verdicts(_injected), (
            f"arm (c-i) does not read the retired spelling {_spelling!r}. An unread row is "
            "indistinguishable from a clean document — the inert-enforcer defect this arm "
            "replaces (#13807 review, F1; each row pinned by name is finding 5)")

    # ⛔ CLONE-INDEPENDENCE, PINNED BEHAVIOURALLY (#13807 review, finding 4). If a probe is
    # ever reintroduced, this arm stops answering from the injected text alone — make the
    # subprocess seam explode and require the SAME verdict.
    def _explode(*_a, **_k):
        raise AssertionError("(c-i) shelled out; it must read the document's TEXT only")
    # ⛔ THE WHOLE FAMILY, NOT ONE NAME (#13807 review round 5, F3 — MEASURED). Patching only
    # `subprocess.run` left a regression undetected whenever it reached a child process by a
    # primitive that does not go through the module-global `run`. ⛔ WHAT THIS PATCH ACTUALLY
    # BOUGHT, CORRECTED IN ROUND 6 (#13807 review round 6, F5 — MEASURED): an earlier cut of
    # this comment named `subprocess.check_output` as the bypass, and that is WRONG —
    # `check_output` dispatches to the module-global `run` at CALL time, so the single-name
    # patch already covered it. The genuinely NEW coverage is `Popen`, `os.popen` and
    # `os.system` (each measured: passing pre-patch, raising post-patch); `check_call` and
    # `call` are built on `Popen`, so they are reached through it. The seam is "a child
    # process", not one function, so every primitive IN THE LIST BELOW explodes. ⛔ "IN THE LIST
    # BELOW" IS THE HONEST SCOPE (#13807 review round 7, F2 — MEASURED): an earlier cut said
    # "every primitive that reaches one", which `os.fork` / `os.execv*` / `os.spawn*` refute,
    # and this file's own rule is that a guard "may be narrow; what it may not do is describe
    # itself as complete".
    for _seam in ("run", "check_output", "check_call", "Popen", "getoutput", "call"):
        monkeypatch.setattr(subprocess, _seam, _explode, raising=False)
    monkeypatch.setattr(os, "popen", _explode, raising=False)
    monkeypatch.setattr(os, "system", _explode, raising=False)
    # ⛔ AND A BLACKLIST OF PRIMITIVES CANNOT CLOSE AN ALIAS (#13807 review round 6, F4 —
    # MEASURED). The patches above set ATTRIBUTES, so a probe bound at import time
    # (`from subprocess import Popen as _P`, `_os_system = os.system`) reads the ORIGINAL
    # object and stays green — which is the very shape this pin's docstring names as the one
    # it is defending against. The whitelist below is the closure FOR GLOBAL NAME LOOKUPS: the
    # arm's bytecode may reach ONE global name, the ledger it ban-matches, and nothing else.
    # ⛔ TWO SCOPES ARE STATED RATHER THAN IMPLIED (#13807 review round 7, F2 — MEASURED):
    # `co_names` is a lookup table, so it does NOT see a seam captured in a DEFAULT ARGUMENT
    # (positional OR keyword-only) or a closure cell — `def arm(doc, _run=subprocess.run)`
    # satisfies `co_names` byte-for-byte while holding the PRE-patch `run` in
    # `__defaults__`, and `def arm(doc, *, _run=subprocess.run)` does the same and hides in
    # `__kwdefaults__` (#13807 review round 8, F3 — MEASURED: the earlier cut of this
    # assertion read `__defaults__` only, and the keyword-only form passed it). Those channels
    # are closed by the assertion BELOW, so the pair — not `co_names` alone — is the closure;
    # an earlier cut of this comment claimed `co_names` alone was it, which is the "describe
    # itself as complete" this file forbids by name.
    assert _census_membership_verdicts.__code__.co_names == (
        "_TORTOISE_RETIRED_SPELLINGS",
    ), (
        "arm (c-i) reaches a global name beyond its own ledger, so it can obtain a "
        "subprocess (or any other seam) behind an import-time alias — which the attribute "
        "patches above cannot see. Keep the arm a pure function of the document's TEXT "
        "(#13807 review round 6, F4)")
    assert (
        _census_membership_verdicts.__defaults__ is None,
        _census_membership_verdicts.__kwdefaults__ is None,
        not _census_membership_verdicts.__code__.co_freevars,
        not [
            _const for _const in _census_membership_verdicts.__code__.co_consts
            if hasattr(_const, "co_names")
        ],
    ) == (True, True, True, True), (
        "arm (c-i) captures a seam in a DEFAULT ARGUMENT (positional or keyword-only), a "
        "closure cell or a nested code object. `co_names` is a LOOKUP table and cannot see "
        "any of those, so the global-name whitelist alone would report clean over an arm that "
        "holds the pre-patch `subprocess.run` in `__defaults__` / `__kwdefaults__` "
        "(#13807 review round 6, F4; round 8, F3)")
    _probe = f"… {_TORTOISE_EMPTY_SPELLINGS[-1]} …"
    assert _census_membership_verdicts(_probe) == [_TORTOISE_EMPTY_SPELLINGS[-1]], (
        "arm (c-i) stopped answering from the document's text when a child-process seam "
        "was unavailable — a clone-dependent arm is unreachable at the depth-1 checkout "
        "the gating job uses and asserts nothing there (#13807 review, F1; the family is "
        "patched rather than one name because `Popen` and `os.popen` do not go through the "
        "patched `run`, round 5 F3 — corrected round 6 F5)")

    # The inverse: the COMMAND the document is supposed to keep is never itself a verdict.
    assert _census_membership_verdicts(
        f"read `{_TORTOISE_CENSUS_CMD}` at the head you merge") == [], (
        "the arm flags the command itself, so the document could not state the measurement "
        "it is required to state")

    _bundle = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
               / "05-verify.md").read_text(encoding="utf-8")
    assert _census_membership_verdicts(_bundle) == [], (
        "05-verify.md still spells a retired membership verdict: "
        f"{_census_membership_verdicts(_bundle)!r}")


#: Spellings that describe the `#`-only comment filter by a property its INPUT cannot have, or
#: that turn its EMPTY output into a "these lines are comments" verdict. Retired BY NAME
#: (#13853 review round 16, P2-1 and P2-2). ⛔ Every row is refuted by the filter's own
#: mechanism rather than by a measurement of this head, so this is a TEXT ban in the same
#: sense as the two ledgers above: it fires in the depth-1 checkout the gating job uses, and
#: no product PR can false-red it.
#:
#: WHY A BAN AND NOT A RE-MEASUREMENT. The lines reaching
#: `grep -vE '^[+-][[:space:]]*#'` already carry the diff marker, so their first non-space
#: character is ALWAYS `+` or `-`. The stage therefore drops a changed line whose first
#: non-space character AFTER the marker is `#` — a `#` comment, and equally a docstring line
#: whose own text opens with `#` (measured: `tortoise/ranking.py`'s `graph_boost` docstring
#: carries such a line). An empty result therefore proves that every changed line WHICH
#: REACHED STAGE 2 opens with `#`; it cannot prove those lines are comments, and it says
#: nothing at all about a blank-line-only change — stage 1 drops a line that is just
#: `+`/`-` and any line whose first character after the marker is `+`/`-`, ADDED OR REMOVED
#: alike (`--counter` is a removed line and stage 1 drops it just the same): the marker is one
#: character and the test is on the NEXT one, so the class is direction-blind (measured:
#: `printf '%s\n' '+' '++i' '--counter' | grep -E '^[+-][^+-]' | grep -vE '^[+-][[:space:]]*#'`
#: is silent while the input holds three changed lines). That was the over-claim that TWO
#: separate sentences of the D5 cell made — and the second one was not among the three the
#: review quoted, which is why the correction is a ban over the class and not a patch of one
#: sentence.
#:
#: ⛔ TWO ARMS, BECAUSE A LIST OF SPELLINGS IS NOT A CLASS BAN (#13853 review round 17, P2).
#: The tuple below bans the rows the round-16 review MEASURED; on its own it misses the same
#: false property RE-WORDED. Measured on the merged document: replacing "whose first non-space
#: character **after that marker** is `#`" with "whose first non-space character is `#`"
#: matched no row of the ledger tuple and left that arm green. ⛔ **the count that stood here
#: (`466 passed`) is DELETED (#13948 review, P2)**: it named no head, and it cannot be reproduced
#: — this file collects a couple of dozen tests, so the figure was some OTHER run's total, which is
#: the head-bound class this very file bans, sitting in the sentence that bans it. The witness is
#: now a COMMAND, re-derivable at any head: apply the re-wording above to `05-verify.md` and run
#: `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_docs_citation_refs.py -k comment_filter` —
#: measured at #13948's head: `1 failed, 23 deselected`, so the arm that reds is this cell and not
#: a neighbour. `_FALSE_FILTER_PROPERTY_RE` grades the
#: SHAPE in the spellings the pattern below covers. It does NOT close the class: the declared
#: escapes at the pattern are real, and the sentence that used to sit here ("the property cannot
#: come back in different words") was itself the over-claim round 19 reded.
#: ⛔ AND ONE ROW IS A PRONOUN BINDING, not a description of the filter's output: the companion
#: measurement beside the filter was described as "the command that counts them" — the nearest
#: antecedent being the TWO `+`/`-` sign classes stage 1 drops — while the command it named
#: (`grep -cE '^\+$'`) counts ONE of them (blank ADDED lines) and misses the sign-opening class
#: and the blank REMOVED line alike. A reader following it over a blank-removed-only diff read
#: `0` and concluded nothing was there, which is the false negative this whole cell exists to
#: stop, so the class is banned the same way its siblings are (#13956 review, P2).
_FILTER_FALSE_DESCRIPTIONS = (
    "drops only lines whose first non-space character is `#`",
    "drops exactly the lines whose first non-space character is `#`",
    "a CODE line and a DOCSTRING line both survive it and would print",
    "a docstring line or a code line WOULD appear in its output and an empty result rules "
    "both out",
    "an empty result therefore means no changed `tortoise/` line is anything but a `#` "
    "comment",
    "a docstring line whose content opens with anything else passes both stages and "
    "prints",
    "a docstring or code line whose content opens with anything else WOULD appear in "
    "its output",
    # The companion measurement's object, not the filter's: `grep -cE '^\+$'` counts blank
    # ADDED lines only, so "them" (the two sign classes) is a count it cannot produce.
    "is the command that counts them at YOUR head",
)

#: ⛔ THE SHAPE ARM — AND AN HONEST STATEMENT OF WHAT IT CANNOT DO. The false property says the
#: filter drops a line because a CHARACTER is `#`; the true one says the character AFTER the diff
#: marker is `#`. This arm reds any `char`/`character` … `is`/`equals`/`is equal to`/`is a`, then
#: up to 400 NON-ALPHANUMERIC marks, then `#` — inside a 400-character window that never names
#: the **marker**. The marks are bounded rather than enumerated on purpose (#13948 review round
#: 29, MEASURED): enumerating them as "backticked or not" was a FALSE CLAIM — a bold
#: (`character is **`#`**`) or quoted (`character is '#'`) spelling restored the false property
#: with the whole file at baseline, because the old tail accepted only whitespace and ONE
#: backtick. That is the same LAYOUT axis rounds 26-29 closed on the two bans above, and it is
#: closed here the same way.
#:
#: ⛔ AND THE WINDOW IS A LOCALITY BOUND, NOT A LAYOUT BUDGET (#13948 review rounds 33-34).
#: Two numbers lived in this pattern and only one of them was a mistake:
#:   · the DELIMITER RUN, copula -> `#`, is now `*`. It WAS a budget (`{0,8}` = seven spaces after
#:     the needle's backtick, then `{0,400}` = "a second number that can be one space too small",
#:     which 400 spaces falsified in one round). Layout cannot be counted out, so it is unbounded.
#:   · the NOUN -> COPULA `{0,400}` is deliberately finite and STAYS: it keeps the match inside
#:     one sentence, so "character" in one paragraph and "is #" in another cannot pair into a
#:     false RED. That is locality, not layout.
#: A restatement padded past 400 characters BETWEEN the noun and the copula is therefore a
#: DECLARED residual (pinned as an escape below), not a claim that the axis is closed everywhere.
#: Measured: 8, 9, 390, 400 and 2000 spaces between COPULA and `#` all caught; the real
#: `05-verify.md` still `[]`.
#:
#: ⛔ AND THE COPULA'S OWN WHITESPACE IS `\s+`, NOT A LITERAL SPACE (#13948 review round 34, P1
#: — MEASURED). `is equal to` / `is a` spelled with ASCII spaces missed a tab or a re-wrap inside
#: the copula (`is\tequal\tto`, `is\r\nequal to`) — the same layout axis, one token further in.
#:
#: ⛔ WHAT IT IS NOT: a semantics checker. A restatement that ALSO re-words the noun or the
#: copula ("the leading printable glyph … amounts to `#`"), or that merely MENTIONS the word
#: `marker` anywhere in the window — as a decoy, not as the qualifier — is NOT detected. Round 19
#: measured six such escapes and this text exists because claiming otherwise was itself the
#: defect: a permissive heuristic over prose is a FALSE PASS by this file's own policy, and no
#: bounded regex closes a semantic class. So the mechanisms are three, and each is named:
#:   · THIS shape ban — the property in the vocabulary this document actually uses;
#:   · THE ROW LEDGER above — every spelling a review MEASURED, banned verbatim and pinned whole;
#:   · REVIEW — new prose is judged, not grepped.
#: Add a measured spelling as a ROW. Never widen this arm's claim to "the property cannot come
#: back in different words": a bounded token test does not close a semantic class, and saying it
#: does is the over-claim #13853 round 17/18/19/20 each reded in turn.
#:
#: ⛔ THE FALSE-RED DIRECTION IS ACCEPTED AND STATED: a TRUE sentence that does not use the word
#: `marker` ("after the `+`/`-` prefix") reds; reword it to name the marker. A false PASS on prose
#: that misdescribes the filter is the failure this cell exists to retire, so the false red is the
#: direction to accept.
#: ⛔ CASE-INSENSITIVE AND PLURAL-TOLERANT, deliberately (#13948 review round 24, P2 — measured).
#: As first compiled, `\bchar(?:acter)?\b` and `\bmarker\b` were case-SENSITIVE, so the arm was
#: wrong in BOTH directions: a re-capitalised restatement (`whose first non-space Character is
#: `#``) was a **false PASS** — the bypass this cell exists to close — while a sentence that DID
#: name the marker, capitalised (`… after the diff Marker is `#``), was a **false RED** whose
#: message told the author to reword it to name the marker it already named. `markers?` because the
#: plural is the same noun.
_FALSE_FILTER_PROPERTY_RE = re.compile(
    r"\bchar(?:acter)?\b(?:(?!\bmarkers?\b).){0,400}?"
    r"(?:is[^0-9A-Za-z#]+equal[^0-9A-Za-z#]+to|equals|is[^0-9A-Za-z#]+a|is)"
    r"[^0-9A-Za-z#]*#[`*_'\"]*", re.S | re.I)

#: The pipeline the document STATES for the `tortoise/` half — `grep -E '<include>' | grep -vE
#: '<exclude>'`, spelled with or without markdown's escaped pipe.
_FILTER_PIPELINE_RE = re.compile(r"grep -E '([^']+)' \\?\| grep -vE '([^']+)'")


def _false_filter_property_restatements(doc: str) -> list[str]:
    """Every restatement of the false property that THIS ARM'S PATTERN matches, still in `doc`.

    ⛔ Not "every re-wording" — the pattern is a bounded token test and the declared escapes at the
    pattern are restatements it does not match (#13853 review round 19/20).
    """
    seen: list[str] = []
    for haystack in (_fold_unicode(doc), _fold_unicode_cf_as_space(doc)):
        for match in _FALSE_FILTER_PROPERTY_RE.finditer(haystack):
            if match.group(0) not in seen:
                seen.append(match.group(0))
    return seen


def _fold_unicode_cf_as_space(text: str) -> str:
    """`text` in compatibility form with every format character (Cf) turned into a SPACE.

    ⛔ THE COMPANION TO `_fold_unicode`, AND NOT A SUBSTITUTE FOR IT (#13948 review round 35, P1 —
    MEASURED). Deleting a Cf is right when it sits INSIDE a token (`char\u200bacter` must stay
    `character`), and wrong when it sits WHERE A SPACE WAS: `character is\u200bequal to #` then
    reads `isequal`, the copula's own separator is gone, and the shape arm returned `[]` over a
    sentence a reader sees verbatim. Neither folding is correct alone, so both are graded and the
    matches unioned — deletion catches the inside-token case, substitution catches the
    in-place-of-a-space case.
    """
    return "".join(
        " " if unicodedata.category(ch) == "Cf" else ch
        for ch in unicodedata.normalize("NFKC", text)
    )


def _fold_unicode(text: str) -> str:
    """`text` in compatibility form with every format character (Cf) removed.

    ⛔ THE LOOKALIKE AXIS (#13948 review round 30, P2 — MEASURED), the same class as rounds 26-29
    one layer down: the folds compared CODEPOINTS, so a character that renders as the one the ban
    names was a different character to it.

    * A FORMAT character (Cf) INSIDE a token split the needle: `char\u200bacter` made the ledger
      row `… first non-space character is \`#\`` unmatchable while a reader sees it verbatim —
      MEASURED `[]` (escape) for both bans.
    * A COMPATIBILITY character was not folded at all: a fullwidth `\uff03` is `#` on screen and
      was invisible to the shape arm — MEASURED `[]`.

    NFKC normalises the compatibility forms (fullwidth, ligatures, superscripts) to their canonical
    spelling and Cf is dropped, so what a READER sees is what the arms grade. Applied to the
    document AND to every needle, and to the shape arm's haystack, so no arm is left on the raw
    codepoints the way rounds 26-29 each left one behind.

    ⛔ STILL OUT, declared: a CHARACTER SUBSTITUTION that changes the spelling (`chαracter` with a
    Greek alpha, a combining diacritic on a letter) is a different word, not a mark, and is the
    declared semantic residual — not something a normalisation can close.
    """
    return "".join(
        ch for ch in unicodedata.normalize("NFKC", text)
        if unicodedata.category(ch) != "Cf"
    )


def _norm_for_ban(text: str) -> str:
    """`text` with case folded and every run of whitespace or commas collapsed to one space.

    ⛔ THE BANS ARE EXACT-TEXT HUNTS, SO EACH IS ONLY AS WIDE AS ITS NORMALISATION — both axes
    were found by MEASUREMENT, one review round apart, and both are folded here.

    * CASE (#13948 review round 26 for the `_D5_HEAD_BOUND_VERDICTS` arm, round 27 for
      `_false_filter_descriptions`): a sentence-initial capital is the natural spelling for prose
      and a one-token edit, and it escaped BOTH bans — the latter over all five of its rows, three
      of which also escaped the shape arm beside it, so the cell reported GREEN over a restored
      false description.
    * LAYOUT (#13948 review round 28): the same sentence re-wrapped across a line break at the
      document's own prose width, or carrying one inserted comma, restored the retired text with
      the whole file still at `1 failed, 23 passed` (baseline). This is not hypothetical for this
      artifact — at `84b94ad74f`, the head the D5 cell grades, the document itself wraps a
      sentence mid-clause: `git show 84b94ad74f:docs/epics/…/05-verify.md | sed -n '131,132p'`.

    ⛔ SO THE FOLD IS TOTAL, NOT ENUMERATED: NFKC first, then Cf removed, then EVERYTHING that is
    not a letter or a digit is DROPPED on both sides. Case, line breaks, commas, and every other
    mark are the SAME axis — the layout of an unchanged sentence — and closing them one mark per
    review round is how this cell spent rounds 26-30.

    ⛔ WHY SPACING IS DROPPED AND NOT COLLAPSED TO A SPACE (#13948 review round 31, P2 —
    MEASURED): collapsing preserved the need for a space where the needle has one, so an invisible
    format character SITTING WHERE THE SPACE IS (`at\u00adthis`, a soft hyphen at a line break)
    removed the separator and the needle stopped matching — 28 measured escapes, mid-token and
    between words alike. With the spacing dropped from BOTH sides, whether the document has a
    space, a soft hyphen, or nothing there is the same. MEASURED: `[]` for every row against the
    real `05-verify.md` (no false red), and zero escapes over newline / tab / comma / em dash /
    NBSP / ZWSP / soft hyphen / LRM / BOM, inside a token and in place of the space.

    ⛔ WHAT IS STILL NOT CAUGHT, DECLARED rather than chased: a SEMANTIC escape — re-wording the
    noun, the copula or the verb, or reordering the clause — is outside what any normalisation
    can close, and it is the residual each detector's own docstring already names. This arm
    grades the document's TEXT; it is a tripwire for the retired spellings and their mechanical
    variations, never a proof that a claim is absent. Widening it into a parser was rejected: the
    claim is prose. If a future round finds another MECHANICAL escape it is a bug in this fold;
    a re-worded claim is the declared residual and belongs to the human-read arm.
    """
    return re.sub(r"[^0-9a-z]+", "", _fold_unicode(text).lower())


def _false_filter_descriptions(doc: str) -> list[str]:
    """Every retired description of the `#`-only filter still spelled in `doc`.

    A substring test, deliberately: this arm grades the document's TEXT and never the tree,
    so it needs no merge base and cannot be skipped in a shallow checkout (the failure mode
    that made the measurement-keyed cut of (c-i) assert nothing — #13807 review, F1).

    ⛔ CASE-INSENSITIVE, deliberately (#13948 review round 27, P2 — MEASURED). As a raw
    substring test, capitalising the first character of a ledger row — the natural spelling for
    a SENTENCE of prose, and a one-token edit — escaped it: all five rows went undetected and
    three also escaped the shape arm beside it, so the whole cell reported GREEN over a
    RE-INSERTED false description. It is the same false-PASS class this PR closed in
    `_d5_head_bound_verdicts`, one function away; the case axis has its own control below.
    """
    lowered = _norm_for_ban(doc)
    return [spelling for spelling in _FILTER_FALSE_DESCRIPTIONS if _norm_for_ban(spelling) in lowered]


def _document_filter_patterns(doc: str) -> list[tuple[str, str]]:
    """Every `grep -E … | grep -vE …` pair `05-verify.md` states, deduplicated.

    ⛔ THE PATTERNS ARE READ OUT OF THE DOCUMENT, NEVER MIRRORED HERE (#13853 review round 17,
    P2-1). This helper did not exist: the arm hardcoded the pair, so the document could state
    a DIFFERENT filter and the mechanism assertions stayed green — measured on the merged
    document, changing the D5 cell's `[[:space:]]*` to `[[:space:]]+` left that arm green while
    the document no longer described the filter its own examples run. ⛔ **the `466 passed` that
    stood here is DELETED for the reason the other one is (#13948 review, P2)**: it named no head
    and no population, and it cannot be reproduced — the file is a couple of dozen tests, so that
    total belongs to some other run. Re-derive the witness by mutation instead: after that edit,
    `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_docs_citation_refs.py -k comment_filter`
    reds `1 failed, 23 deselected` (measured at #13948's head). The document is the
    artifact under test, so the patterns it states are the ones executed.
    """
    return sorted(set(_FILTER_PIPELINE_RE.findall(doc)))


def _comment_filter(payload: str, include: str, exclude: str) -> str:
    """The document's two grep stages, executed on `payload` with the DOCUMENT's patterns.

    Running the REAL filters rather than a Python mirror is the point: the claim under test
    is about what `grep` does with those two expressions, and a re-spelled mirror would prove
    only that the mirror agrees with itself.
    """
    staged = subprocess.run(
        ["grep", "-E", include], input=payload,
        capture_output=True, text=True, check=False)
    return subprocess.run(
        ["grep", "-vE", exclude], input=staged.stdout,
        capture_output=True, text=True, check=False).stdout


#: The count command the D5 cell prescribes for the blank-line class, as the cell spells it.
#: The EXPRESSION is read out of the document and executed, never re-spelled here: the claim
#: under test is what `grep` does with the expression THAT SENTENCE carries (#13956 review, P2).
_D5_BLANK_COUNT_RE = re.compile(
    r"`git diff origin/main\.\.\.HEAD \\\| grep -cE '(?P<expr>[^']+)'` is the command "
    r"that counts the BLANK ones at YOUR head")


#: The COUNT READER: it walks every `grep` INVOCATION in the bundle and reads the expression out
#: of the words that follow it. `-`-anchored flag words are followed by a quoted expression, and
#: a flag that takes an argument CONSUMES the next word as its value — so a flag's own quoted
#: argument (`--exclude '*.md'`, `-A '2'`) is never mistaken for the expression beside it. The
#: block also carries the shape that makes a prescription BLANK-class.
#:
#: ⛔ IT IS A WORD WALK, NOT ONE REGEX OVER RAW TEXT (#13995 review, P2 — MEASURED). The reader
#: this replaces matched the whole invocation with a single pattern in which the flag run was a
#: character class and the expression was "the next quoted token", so a SEPARATE value belonging
#: to a flag was read as the expression: `grep -c --exclude '*.md' '^[+-]$'` was reported as a
#: count of `*.md`, and `grep -c -A '2' '^[+-]$'` as a count of `2`. That is not merely a miss —
#: the wrong expression is neither the blank class nor a shape the residual block below names,
#: so a SECOND blank-class prescription appended in that spelling left the arm GREEN, which is
#: the false PASS #13962 exists to retire. Parsing to a command/option tree is how the comparable
#: static analysers solve it (ShellCheck, ESLint and semgrep all dispatch on AST NODES rather than
#: on text, precisely because text cannot tell a command from text that looks like one, nor an
#: option's VALUE from an operand): the walk below consumes each argument-taking flag's value,
#: so the shapes it still does not reach are the ones stated at the foot of this block.
#:
#: ⛔ AND THE CLASS IS THE MATCH SET THAT SURVIVES THE FLAGS, NOT THE EXPRESSION ALONE
#: (#13995 review round 4, P2 — MEASURED on GNU grep 3.11). A `-`-anchored `c` does not by itself
#: say a command counts THIS class: `-v` INVERTS the match set, so `grep -vc '^[+-]$'` counts the
#: NON-blank lines (measured: on the 4-line probe `+`, `-`, blank, `++`, `-c` gives 2 and `-vc`
#: gives the 2 lines that are not `+`/`-` — same number, different class), and `-l`, `-L` and
#: `-q` WIN over `-c` entirely (`-lc` prints the FILENAME, `-Lc` prints the files WITHOUT a match,
#: `-qc` prints nothing — all measured). So the flags are read for what they do to the count: an
#: INVERTED count is SEEN (`_d5_count_prescriptions`) and deliberately NOT graded against the
#: positive class (`_d5_blank_class_prescriptions`), and a word carrying a count-SUPPRESSING flag
#: is not a count request at all (`_requests_a_count`). Grading a complement count as this class
#: would red a document that prescribes the blank count AND its complement — the false RED on a
#: correct document this file treats as the worse direction.
#: ⛔ THAT SENTENCE IS A SHAPE AND NOT A PROMISE ABOUT SPELLING (#13995 review, P2 — MEASURED).
#: Earlier cuts claimed EVERY count prescription in the bundle regardless of how it was prefixed
#: or worded, which is the false-PASS guarantee #13962 was filed against. That claim is FALSE in
#: both directions: the reader does not see `grep -cE ^[+-]$` (unquoted, `[]`), and it DOES read
#: `grep --count '^\+$'`, which is not `-c`-spelled at all. The shapes it does not read are listed
#: at the foot of this block; that list is a list, not the closure. Keying "exactly one" on the
#: COMMAND PREFIX was still a spelling rather than a property (#13956 review, cycle 5):
#: this row already prescribes `git diff origin/main...HEAD -- tortoise/` five times, so a
#: second BLANK-class command written that way left the count GREEN while the row carried two
#: prescriptions — the retired, narrower one among the ungraded ones — and, in the other
#: direction, it redded a legitimately different command of the same shape
#: (`grep -cE '^\+import '`), which is an over-block on a correct document. Refusing on the
#: CLASS (a count of the blank `+`/`-` lines) rather than on the prefix is what avoids that
#: over-block; the walk below carries the SPELLING, because a shape is the most a guard of this
#: kind can honestly claim.
#:
#:
#: ⛔ AND THE FLAGS AND THE QUOTE ARE PART OF THE MATCH — AN EARLIER CUT OF THIS BLOCK CALLED
#: ITSELF "PREFIX-INDEPENDENT" AND IT WAS NOT (#13962, MEASURED). The cut above read
#: `grep -c[A-Za-z]* '<expr>'` — ONE spelling of a count command — while claiming to read the
#: expression "whatever command prefix". SEVEN other spellings of the SAME prescription left
#: this arm GREEN, each measured by appending one line to the bundle and running the cell that
#: calls `_d5_blank_count_expression` (`1 passed` for every one): `grep -Ec '^\+$'` (flag
#: before the `c`), `grep -cE "^\+$"` (double-quoted
#: expression), `grep -c -E '^\+$'` (flags split), `grep --count '^\+$'` (long form),
#: `grep -c -- '^\+$'` (POSIX end-of-flags `--`), `grep -c --color=never '^\+$'` (a long flag
#: WITH a value) and `grep -c $'^\+$'` (ANSI-C quoting). In every one the row carried TWO
#: BLANK-class prescriptions — the retired, narrower one among the ungraded ones — while the arm
#: whose whole job is to refuse that reported the class as graded once.
#: ⛔ THE FIRST CUT OF THIS FIX READ FOUR OF THESE AND WAS ITSELF GUILTY OF THE SAME OVER-CLAIM
#: (#13995 review, P1 — MEASURED): its flag word was `-{1,2}[A-Za-z][\w-]*`, which cannot
#: express a bare `--` (a letter is required after the dashes) nor `--flag=value` (`=` is outside
#: `[\w-]`), and its quote could not express `$'…'` — so the last three mutants above were
#: invisible to it while its own paragraph called the claim "every count command spelled
#: `grep <flags> '<expr>'`". A flag word may also carry an ATTACHED quoted value
#: (`--include='*.py'`, `-e'^[+-]$'`), which is that flag's own argument and not the expression
#: beside it; the expression itself may be single-, double- or ANSI-C-quoted.
#:
#: ⛔ AND A NON-FLAG WORD IS THE EXPRESSION OR NOTHING (#13995 review round 2, P2 — MEASURED).
#: The round-1 wording said the run "keeps a non-flag token (a filename, a revision) out of the
#: run", which reads as *the token is skipped and the expression is still found*. It is not: a
#: word that does not begin with `-` ENDS the flag run, and the reader then takes THAT word as the
#: expression — quoted, or the command is unread. So an unquoted first operand (the expression
#: itself, or a filename written before it) makes the whole invocation UNSEEN rather than
#: parsed-around, which is why that spelling is listed below as unread: it returns `[]`, so a
#: second BLANK-class prescription written that way is invisible, exactly as #13962's original
#: mutants were.
#:
#: ⛔ WHAT IT STILL DOES NOT READ, stated because a guard may be narrow but may not describe
#: itself as complete — this file's own rule (the "Said plainly because a guard may be narrow"
#: sentence in `_typed_answers_to_tortoise_cmd`'s docstring, cited by FUNCTION NAME rather than by
#: line because the number is exactly what rots, and by the sentence's own opening words because
#: "closing sentence" was wrong: the docstring continues past it). Every
#: bullet is MEASURED against `_d5_count_prescriptions` — `[]` for all but the concatenation,
#: which is pinned at its FIRST FRAGMENT (`['^']`), as its own bullet says — in TWO groups that
#: the previous wording wrongly merged (#13995 review round 3, P2):
#:
#: A. SHAPES THAT REALLY ARE BLANK-CLASS PRESCRIPTIONS, and that this reader does not see — so
#:    a second one appended to the row leaves the arm GREEN, exactly as #13962's mutants did:
#:      * an UNQUOTED first operand — `grep -cE ^[+-]$` (and equally a bare filename before the
#:        expression, `grep -c FILE '^\+$'`, whose pattern is the word `FILE`);
#:      * flags written AFTER the expression — `grep '^\+$' -c` (the flag run read is empty, so
#:        nothing asks for a count);
#:      * a pattern supplied from a FILE — `grep -c -f /tmp/pats '^[+-]$'`. With `-f`/
#:        `--file` the quoted token is a FILE operand (measured `No such file or directory`), so
#:        the walk declines the invocation rather than invent a count of it. ⛔ The `-e` half of
#:        this bullet is GONE, not glossed (#13995, MEASURED): `grep -c -e'^[+-]$'`,
#:        `grep -ce'^[+-]$'`, `grep --count --regexp='^[+-]$'` and `grep -c -e 'foo' -e '^[+-]$'`
#:        are READ, and every `-e` argument is reported as its own prescription — a command with
#:        two `-e` flags counts the UNION of both patterns, so the second one hiding behind the
#:        first was exactly the false PASS this bullet used to certify as unreadable;
#:      * a count performed by another tool — `rg -c '^\+$'`, `awk`;
#:      * an expression built by CONCATENATION — `grep -cE '^'"[+-]"'$'` is read only up to its
#:        first quoted fragment (`^`), so a class constraint living in the tail is invisible.
#:        This is NOT uniform (`"^[+-]$"''` reads the whole class, because the class is inside
#:        the first quoted fragment) — which is why it is listed as a shape to check rather
#:        than dismissed.
#:
#: B. SHAPES THAT ARE **NOT A COUNT PRESCRIPTION AT ALL**, pinned `[]` so that a future widening
#:    cannot start treating one as a prescription — a maintainer who trusted the previous
#:    "every one is a second BLANK-class prescription" lead-in would have widened to catch them
#:    and introduced a false RED (#13995 review round 3, P2 — each MEASURED with `grep` on a
#:    4-line sample, non-zero exit and nothing counted):
#:      * `grep -c'^[+-]$'` and `grep -c, '^[+-]$'` — INVALID: `-c` takes no argument, so the
#:        attached text is parsed as more options (`grep: invalid option`);
#:      * `grep -ec 'x'` and `grep -e'c'` — here `c` belongs to `-e`'s pattern, not to a count
#:        flag. `_requests_a_count` scans a short cluster left to right and STOPS at a flag that
#:        takes an argument, so both are correctly not counts.
#: One shape is seen but NOT classifiable, which is a different thing and is said separately so
#: the lists above are not read as covering it: an expression held in a shell variable
#: (`grep -cE "$RE"` IS read — its expression arrives as `$RE`, which no class test can
#: classify). Each of these carries a second prescription this reader cannot grade, so "exactly
#: one BLANK-class count command" is a claim about count commands whose quoted expression the
#: reader can SEE — never a claim that the class is prescribed once in the document.
#:
#: ⛔ AND THE FLAG WORD IS A FLAG TOKEN, NOT A BAG OF CHARACTERS (#13995 review round 3, P2 —
#: MEASURED, and this one was a REGRESSION against `origin/main`). While the word was
#: `[^\s'\"]*` it admitted the CLOSING BACKTICK, `,`, `)` and `;`, so the character separating
#: the command from the next quoted token was swallowed INTO the flag word and the
#: quote-adjacency requirement was defeated: the prose `` `grep -c`, '^[+-]$' `` — a sentence
#: saying what the row does NOT mean — was read as a second BLANK-class prescription and redded
#: a correct document (`prescribes 2 BLANK-line count commands`). `grep -c[A-Za-z]* '…'`, the
#: reader this replaced, returned `[]` for it. The walk keeps that protection in three places: a
#: word ends at whitespace or at a character that cannot appear inside a `grep` argument in prose
#: (`` ` `` closes the code span; `;`/`|`/`&`/`(`/`)`/`<`/`>` are shell operators); a flag TOKEN
#: is `--`/`-` then alphanumerics (which still expresses `-c`, `-Ec`, `--count`,
#: `--color=never`, `-C3`, `-m2`); a QUOTED tail is an attached VALUE on a SHORT cluster that
#: CONTAINS an argument-taking flag, at ANY position in it (`-e'…'`, `-ce'…'`, `-ec'…'`), and on
#: every other cluster it is MORE FLAG CHARACTERS, because the shell has already removed the quotes
#: — an alphanumeric residue is read as one word (`grep -c'x'` is argv `-cx`, so it IS a count and
#: the quoted token after it is its expression) while a non-alphanumeric one is unreadable
#: (`grep -c'^\+$'` is argv `-c^\+$`, which grep rejects).
#: ⛔ ONE RULE COVERS BOTH QUOTINGS: A SHORT CLUSTER SPLITS AT ITS FIRST ARGUMENT-TAKING FLAG AND
#: THAT FLAG OWNS THE REST AS ITS VALUE (#14027 round 10, P1 — MEASURED, correcting this block).
#: `-e'…'`, `-ce'…'` and `-ec^[+-]$` are all "the flag, then its value"; `-ec^[+-]$` is `-e`
#: with the value `c^[+-]$` and IS a flag word, pinned as that tuple below. This block used to
#: describe TWO different tests (quoted: the cluster CONTAINS an argument-taking flag, `any(...)`;
#: unquoted: its LAST character takes one, `option[-1] in …`) and called `-ec^[+-]$` "not a flag
#: word at all" — refuted by this file's own pin. The split runs before both of those tests, so
#: neither decides anything for a cluster that carries an argument-taking flag, and the sentence
#: that named them as the model is what the pins now contradict.
#: `grep -c, '…'` is unreadable because `-c,` is not a flag cluster at all. On a
#: LONG option a quoted tail is MORE NAME with the
#: quotes removed (`--count'x'` is argv `--countx`), so the acceptance test refuses it, while a
#: tail that is a `=`-separated value (`--regexp='x'`) is read as that value. A QUOTED section
#: may hold spaces and still be one word, which is what makes `--label='x -c'` one flag word
#: rather than three.
_D5_GREP_INVOCATION_RE = re.compile(r"\bgrep(?=\s)")

#: GNU grep's short flags that TAKE AN ARGUMENT: `-e`/`-f` (pattern source), `-m` (max count),
#: `-A`/`-B`/`-C` (context), `-d` (directories action), `-D` (devices action). A `c` after one
#: of these belongs to THAT flag's argument, not to the count flag (`-ec` is `-e c`), so the
#: count scan stops there. `-D` was missing until #13995's round-3 verification measured
#: `_requests_a_count("-Dc")` returning True while the docstring said "the short flags that take
#: an argument" — the list is the whole set, not a sample.
_ARG_TAKING_SHORT_FLAGS = "efmABCdD"

#: GNU grep's LONG options that REQUIRE an argument, from `grep --help` (GNU grep 3.11). A long
#: option's value is the NEXT word when it is not attached with `=`, and it is never the
#: expression: `grep -c --exclude '*.md' '^[+-]$'` counts the blank lines (measured: `2` on a
#: 4-line probe), so reading `'*.md'` as its expression was a FALSE PASS (#13995 review, P2).
#: `--color` is deliberately ABSENT, and that is the ONE long option here whose argument is
#: optional: `grep --color always 'x'` takes `always` as the PATTERN and `'x'` as a FILE, exiting 2
#: on the missing file (measured: `printf 'always\n' | grep --color always` prints `always`), so
#: consuming the next word for it would swallow a real expression.
#: ⛔ `--context` IS PRESENT — a single measurement used to be generalised to both, and it was
#: wrong for this one (#13995, MEASURED): `grep -c --context 2 '^[+-]$'` prints `2` and exits 0,
#: i.e. the `2` IS its argument; with `--context` outside this set, the reader took `2` as the
#: EXPRESSION and the invocation went unseen — a false PASS on a real blank-class count.
_ARG_TAKING_LONG_FLAGS = frozenset({
    "--after-context", "--before-context", "--binary-files", "--context", "--devices",
    "--directories", "--exclude", "--exclude-dir", "--exclude-from", "--file",
    "--group-separator", "--include", "--label", "--max-count", "--regexp",
})

#: GNU grep's undocumented long-alias CANDIDATES, kept apart from the measured allowance so that
#: the allowance stays a MEASUREMENT of the grep on PATH rather than a declaration (#14138) —
#: `_comment_block_above` scopes a prose guard to the comment run above its anchor, so a description
#: sitting between a constant and its set would be read as that constant's (#14027 review, P3).
#:
#: ⛔ WHY THE SPELLING IS KNOWN AT ALL, AND WHY IT IS NO LONGER DECLARED — BOTH MEASURED.
#: `grep --help` never mentions `--unix-byte-offsets`, but GNU grep 3.11 ACCEPTED it — and every
#: prefix of it, `--u`/`--un`/`--uni`/`--unix` — as the obsolete `-u` (`warning:
#: --unix-byte-offsets (-u) is obsolete`), and `grep -c --unix-byte-offsets '^[+-]$'` really counted
#: the blank class (`2`). Declined as unknown, a SECOND blank-class prescription in that spelling
#: sat invisible beside the one that was read — the false PASS this cell exists to refuse (#14027
#: review round 2, P2).
#: ⛔ GNU grep 3.12 REMOVED it, and 3.12 is what `ubuntu-latest` (Ubuntu 26.04) now ships: there
#: `grep -c --unix-byte-offsets '^[+-]$'` is `grep: unrecognized option` and exits 2. A hardcoded
#: allowance then made this reader grade a command grep will not run — the false RED its own
#: acceptance rule refuses — and turned a fact about the local binary into a CI-only crash (#14138).
#: So the allowance is MEASURED against the grep that would run the command, and it is EMPTY
#: wherever that grep refuses the spelling.
#: `--fixed-regexp` is the other undocumented alias and stays OUT deliberately: it shares
#: `-F`'s identity, so admitting it would make `--fixed-` falsely ambiguous (measured: `grep -c
#: --fixed-` is ACCEPTED), and it retargets the match semantics rather than the arity. That is a
#: SEMANTIC exclusion no grep version decides, so it is not a candidate here — it is excluded even
#: where grep accepts it.
_D5_UNDOCUMENTED_ALIAS_CANDIDATES = ("--unix-byte-offsets",)


#: The probe the measurement below and the alias cell both feed to `grep -c`: two lines of the blank
#: class, plus lines that are not. `grep -c '^[+-]$'` on it prints `2`, which is what makes the
#: control run below decisive. ⛔ ONE FIXTURE, ONE LITERAL (#14138 review round 4, P3): the cell used
#: to spell the same bytes out again, so a change to one copy would have silently diverged from the
#: other while both still claimed to be the same measurement.
_D5_GREP_PROBE = "++\n+\nxx\n-\n\n"


def _d5_grep_verdict(ran: subprocess.CompletedProcess, name: str, grep: str = "grep") -> bool:
    """Read one probe run: True = `grep` RAN `name`, False = it REFUSED it, a THIRD code RAISES.

    ⛔ A THIRD EXIT CODE IS AN ERROR, NOT A REFUSAL (#14138): reading it as "refused" would
    silently empty the allowance, so a real prescription in the alias spelling would go unread.

    ⛔ ONE IMPLEMENTATION, TWO READERS — and they are different readers on purpose. The allowance
    probe below asks this question of the alias candidates, where a `False` removes a spelling from
    the option universe; the alias cell asks it of every spelling the alias answers to, where a
    `False` is the refusal arm of its own assertion. A second copy of the rule would let the two
    disagree about what a given exit code means, which is the drift this file bans by name (#14138
    review round 8, P2 — MEASURED: with a grep that answers a third code for a PREFIX spelling only,
    a two-way `if rc in (0, 1) … else` read it as a refusal and asserted "(measured: exit 2)" over a
    run that exited 3).
    """
    if ran.returncode in (0, 1):
        return True
    if ran.returncode == 2:
        return False
    raise AssertionError(
        f"`{grep} -c {name} '^[+-]$'` exited {ran.returncode}, which is neither a count (0/1) nor "
        f"grep's invalid-option code (2): {ran.stderr.strip()!r} — a NON-ANSWER cannot decide the "
        "reader's option universe, and reading it as 'refused' is the fail-open direction (#14138)")


def _d5_grep_runs_long_option(name: str, grep: str = "grep") -> bool:
    """Does `grep` RUN this long-option spelling? Measured, never remembered (#14138).

    ⛔ THE VERDICT COMES FROM THE BINARY THAT WOULD RUN THE DOCUMENT'S COMMAND (#14138 — MEASURED).
    `grep -c --unix-byte-offsets '^[+-]$'` exits 0/1 on GNU grep 3.11 and exits 2
    (`unrecognized option`) on GNU grep 3.12, which `ubuntu-latest` now ships. A LONG spelling is
    graded only when grep would run it, so the version split has to be READ FROM GREP rather than
    baked in: a baked-in table is wrong on one of the two platforms whichever way it is written.

    ⛔ A CONTROL RUN MAKES RC 2 ATTRIBUTABLE TO THIS OPTION (#14138 review round 3, P2 — MEASURED).
    grep exits 2 for ANY error, not just an invalid option: `grep -c --count '^[+-]$' <&-` is
    `grep: (standard input): Bad file descriptor`, rc 2, on a fully documented spelling, and
    `-f /nonexistent` is the same. Reading a bare rc 2 as "refused" would then EMPTY the allowance
    on a grep that is merely broken — the fail-open direction, and exactly what the paragraph below
    refuses. So a DOCUMENTED spelling that must count this class (`--count`: `2` on this probe) is
    run FIRST: if that run does not answer `2`, the probe itself did not work and NOTHING is
    concluded from the second run. Discriminating this way is locale-proof, unlike matching grep's
    translated `unrecognized option` text.

    ⛔ A THIRD EXIT CODE IS AN ERROR, NOT A REFUSAL: reading it as "refused" would silently empty
    the allowance, so a real prescription in the alias spelling would go unread.

    ⛔ STATED RESIDUAL — SHORT SPELLINGS ARE NOT MEASURED (#14138 review, P2). This function answers
    about LONG options only. `-u` is the SHORT spelling of the option grep 3.12 removed, and the
    cluster walk (`_d5_flag_word`, `_requests_a_count`) has no per-grep short-flag universe, so
    `grep -c -u '^[+-]$'` is still graded where a 3.12 grep would refuse it — the false-RED axis
    this function closes for `--unix-byte-offsets`, one spelling over. It is LATENT rather than
    live: no document in this tree carries that invocation (`rg -n 'grep -c -u|grep -cu' docs/
    tools/` finds nothing, and `unix-byte-offsets` occurs only in this file), and closing it needs
    a cluster-level grep oracle rather than one more candidate here — the measurement is recorded on
    #14138 (issuecomment-7073577176), where it outlives this fix.
    """

    def run(spelling: str) -> subprocess.CompletedProcess:
        return subprocess.run([grep, "-c", spelling, "^[+-]$"], input=_D5_GREP_PROBE,
                              capture_output=True, text=True, timeout=60)

    control = run("--count")
    if control.returncode not in (0, 1) or control.stdout.strip() != "2":
        raise AssertionError(
            f"the control probe `{grep} -c --count '^[+-]$'` answered rc {control.returncode}, "
            f"stdout {control.stdout.strip()!r}, stderr {control.stderr.strip()!r} — `--count` is a "
            "DOCUMENTED spelling and prints `2` on this probe wherever grep works, so this grep is "
            "broken or the probe is, and rc 2 on the spelling under test would then be an "
            "UNRELATED error read as a refusal (#14138)")
    ran = run(name)
    return _d5_grep_verdict(ran, name, grep)


def _d5_grade_alias_spelling(
    spelling: str, grep: str = "grep",
) -> tuple[subprocess.CompletedProcess, bool]:
    """Run one alias spelling on `grep` and return `(the run, its THREE-WAY verdict)`.

    ⛔ THE SEAM EXISTS SO THE RULE HAS A WITNESS AT ITS CALL SITE (#14303 review round 3, P3 —
    MEASURED). The alias cell classifies every spelling the alias answers to. Pinning
    `_d5_grep_verdict` on its own did NOT pin that cell's USE of it: replacing the loop's verdict
    with a two-way `ran.returncode in (0, 1)` branch left this whole file GREEN on GNU grep 3.11 AND
    under a 3.12 shim — the round-8 fail-open read, reinstated with nothing to red it, because no
    shipped grep answers a third code for a PREFIX spelling. Routing the loop through this function
    lets a stub grep drive it, which is what
    `test_a_prefix_spelling_that_does_not_answer_is_not_a_refusal` does.

    ⛔ AND IT IS NOT TRUE THAT "EVERY SPELLING GOES THROUGH THE UNIVERSE" (#14303 review round 10,
    P3 — MEASURED: the candidate tuple has ONE member and the alias cell probes six spellings). Only
    `--unix-byte-offsets` is measured by `_d5_grep_runs_long_option`; the four PREFIX spellings and
    `--fixed-regexp` reach the three-way rule through HERE and nowhere else.
    """
    ran = subprocess.run([grep, "-c", spelling, "^[+-]$"], input=_D5_GREP_PROBE,
                         capture_output=True, text=True, timeout=60)
    return ran, _d5_grep_verdict(ran, spelling, grep)


#: The undocumented aliases the grep ON PATH actually RUNS — the allowance, MEASURED from
#: `_D5_UNDOCUMENTED_ALIAS_CANDIDATES` (#14138). It is the single candidate on GNU grep <= 3.11 and
#: EMPTY on grep >= 3.12, which is why it cannot be a literal: the literal was right on exactly one
#: of the two platforms and red on the other.
#: ⛔ DERIVED LAZILY, NOT AT IMPORT (#14138 review round 2, P3 — MEASURED). A module-level probe made
#: this WHOLE file uncollectable on a box with no `grep` (`--collect-only` -> `FileNotFoundError:
#: 'grep'`), taking every citation guard in it down with the cells that need grep; and it ran
#: outside any pytest item, so a `grep` that blocked on PATH hung collection instead of failing it.
#: Deferred, the blast radius is the cells that grep, MEASURED with a `grep` on PATH that exits 3
#: for every call (so no probe can answer): **eight** cells fail, ONE skips, and the other **28**
#: still pass (37 cells at this head — re-measure rather than trusting these integers whenever a cell
#: is added to this file).
#: The eight are the SIX `test_the_d5_count_reader_*` cells that consult the option universe, plus
#: `test_the_comment_filter_is_not_described_by_a_property_its_input_cannot_have` and
#: `test_the_D5_row_types_no_verdict_about_the_head_being_merged` — and SIX of the eight report the
#: diagnostic from this file, while those last two fail on their OWN direct grep calls and carry
#: their own messages, so "inherits the probe" is true of six, not of eight.
#: ⛔ THE ONE SKIP IS `test_the_d5_count_reader_knows_every_long_option_grep_documents`, AND IT IS
#: THE PORTABILITY FIX, NOT A HOLE (#14303 review, P2 — MEASURED at this same hostile grep: it used
#: to be a ninth FAILURE, on its own direct `grep --help` through `check=True`). A grep that exits 3
#: for `--help` published no option list at all, so it has no documented universe to pin the option
#: set against, and `_d5_documented_long_options` raises `_D5NoDocumentedUniverse` for exactly that
#: case — the same shape BSD grep presents (usage on stderr, exit 2). ⛔ THE FLOOR IS NOT SKIPPED
#: WITH IT: a `--help` that SUCCEEDED and parsed too little is the derivation breaking, and that
#: still RAISES (see the docstring there, and the cell that reds it).
#: `test_a_prefix_spelling_that_does_not_answer_is_not_a_refusal` PASSES here on purpose: it
#: hands `_d5_grade_alias_spelling` a stub PATH of its own rather than reading the host grep, so it
#: is independent of the installed binary by construction.
#: `test_the_d5_count_reader_does_not_describe_itself_as_complete` still PASSES: it greps this file's
#: source TEXT and never reaches the universe. What the import-time probe did instead was abort
#: COLLECTION, so NONE of the file's cells ran — that is the difference the laziness buys, not a
#: count of 8.
#: A binary that is MISSING outright raises `OSError` from `subprocess` before this function can say
#: anything (pinned by
#: `test_the_undocumented_allowance_probe_tells_refusal_from_a_non_answer`). Cached because the probe
#: spawns a process and nothing within a session changes its answer (the `grep` parameter is part
#: of the cache key, so a stub's answer and the local binary's are cached separately).
@cache
def _d5_undocumented_long_options(grep: str = "grep") -> frozenset[str]:
    """The allowance for `grep`, MEASURED from the binary — never remembered from a version.

    ⛔ `grep` IS A PARAMETER, NOT A CLOSED-OVER LITERAL (#14303 review, P2 — MEASURED). Every
    consumer reads the default, so the live behaviour is unchanged; the parameter exists so a STUB
    can drive the derivation. Without it the only witness was the alias cell's
    `rederived == _d5_undocumented_long_options()`, and on GNU grep >= 3.12 — the platform CI runs —
    BOTH sides of that comparison are `frozenset()`. A mutant that hardcodes `return frozenset()`
    therefore leaves CI green (at head `35b7769e`, `-k "d5 or D5 or undocumented"` under the 3.12
    shim reported `10 passed`) and reds only on a <= 3.11 box, so
    the exact regression this derivation exists to prevent (a remembered table coming back, the
    #14027 false PASS of an unread blank-class prescription) was unguarded where it mattered most.
    `test_the_undocumented_allowance_probe_tells_refusal_from_a_non_answer` now drives it with a
    stub that RUNS the spelling and one that REFUSES it, which distinguishes "measured" from
    "remembered" on every platform.
    """
    return frozenset(
        alias for alias in _D5_UNDOCUMENTED_ALIAS_CANDIDATES
        if _d5_grep_runs_long_option(alias, grep))

#: Every long option `grep --help` documents (GNU grep 3.11 and 3.12 document the same set, pinned
#: against the binary by `test_the_d5_count_reader_knows_every_long_option_grep_documents`).
#: `_d5_all_long_options()` below is this set UNIONED with the measured allowance, and the union
#: exists for ONE purpose: whether a prefix is AMBIGUOUS.
#: `_resolved_long_option` may only resolve a prefix that names a single option in this whole set,
#: because that is the rule `getopt_long` applies — `--re` names `--regexp` in the arity set but is
#: refused by grep as ambiguous against `--recursive` (measured), and certifying it graded a
#: command grep will not run (#13995, MEASURED). Pinned against `grep --help` by
#: `test_the_d5_count_reader_knows_every_long_option_grep_documents`, so a grep that adds one
#: reds there rather than quietly making a new prefix ambiguous — and that cell asserts the union
#: equals `grep --help` ∪ the allowance, so an allowance member the universe lost reds by name.
_D5_DOCUMENTED_LONG_OPTIONS = frozenset({
    "--after-context", "--basic-regexp", "--before-context", "--binary", "--binary-files",
    "--byte-offset",
    "--color", "--colour", "--context", "--count", "--devices", "--directories",
    "--dereference-recursive", "--exclude", "--exclude-dir", "--exclude-from",
    "--extended-regexp", "--file", "--files-with-matches", "--files-without-match",
    "--fixed-strings", "--group-separator", "--help", "--ignore-case", "--include",
    "--initial-tab", "--invert-match", "--label", "--line-buffered", "--line-number",
    "--line-regexp", "--max-count", "--no-filename", "--no-group-separator", "--no-ignore-case",
    "--no-messages",
    "--null", "--null-data", "--only-matching", "--perl-regexp", "--quiet", "--recursive",
    "--regexp", "--silent", "--text", "--version", "--with-filename",
    "--word-regexp",
})

#: `grep --help`'s set plus the MEASURED undocumented allowance — composed rather than written out
#: because on a grep that refuses the alias the allowance is empty and this is exactly `grep
#: --help`'s set (#14138). ⛔ A FUNCTION, not a constant, for the lazy-derivation reason recorded on
#: `_d5_undocumented_long_options`; `_d5_long_option_candidates` looks it up by name at CALL time, so
#: a caller that replaces it sees the whole walk follow.
#: ⛔ `grep` IS A PARAMETER HERE TOO, AND THIS IS THE UNION'S OWN WITNESS (#14303 review, P2 —
#: MEASURED). Passing it through to the derivation is not decoration: without it the composition —
#: DOCUMENTED plus the measured allowance — could be replaced by `_D5_DOCUMENTED_LONG_OPTIONS` alone
#: and every cell stayed GREEN on the platform CI runs, because on grep >= 3.12 the allowance IS
#: empty, so dropping it is unobservable there. On that platform the union's only remaining content
#: is a set it would equal anyway. Driving both arms through a stub grep makes the composition
#: separable on EVERY platform, which is what
#: `test_the_undocumented_allowance_probe_tells_refusal_from_a_non_answer` now does.
def _d5_all_long_options(grep: str = "grep") -> frozenset[str]:
    return _D5_DOCUMENTED_LONG_OPTIONS | _d5_undocumented_long_options(grep)


#: The floor below which a parsed `grep --help` is not a universe at all. GNU grep 3.11 publishes
#: **48** long options (MEASURED), so this is a "the parser or grep's help layout broke" floor, not
#: a description of any one grep (#14303 review, P2 — the finding that the `--help` read was not
#: portable; #14306 review F1 for the inert-floor shape it guards against).
_D5_LONG_OPTION_FLOOR = 40


class _D5NoDocumentedUniverse(RuntimeError):
    """This grep published no parsable usage, so there is no option universe to pin against.

    ⛔ IT IS A DISTINCT EXIT, NOT A VERDICT, AND IT EXISTS SO THE FLOOR STAYS REACHABLE (#14303
    review, P2 — the portability residual from #14306; its review F1 found the trap the naive fix
    walks into). The cell used to read `grep --help` with `check=True`, which turns BSD grep's usage
    text on **stderr** with exit **2** into `CalledProcessError`: on such a box the cell errors
    before any assertion, and it takes the module's other citation guards down with it. The naive
    repair — `pytest.skip` whenever fewer than 40 options were parsed — moves the floor's failure
    mode from RED to GREEN, so a broken derivation would silently certify nothing. Discriminating
    on the EXIT CODE keeps both honest: `--help` FAILED and published no usage is a platform fact
    (this exception, which the cell skips); `--help` SUCCEEDED and still parsed too little is our
    derivation breaking (`AssertionError`).
    """


def _d5_documented_long_options(completed: subprocess.CompletedProcess) -> set[str]:
    """The long options `grep --help` published, read from WHICHEVER STREAM carried them.

    ⛔ `grep --help` IS ITSELF NOT PORTABLE (MEASURED via the #14306 handover, and it is the whole
    finding): GNU grep writes the option list to stdout and exits 0; BSD grep
    (`grep (BSD grep, GNU compatible) 2.6.0-FreeBSD`, macOS) writes a `usage:` line to **stderr**
    and exits **2**. Reading only stdout under `check=True` therefore makes this cell unrunnable on
    macOS rather than merely indifferent to it. Parsing the union of the two streams costs nothing
    on GNU grep (stderr is empty) and is what lets the same derivation answer there.

    ⛔ THE FLOOR AND THE EXIT CODE ANSWER DIFFERENT QUESTIONS, AND BOTH ARE LOAD-BEARING. A count
    alone cannot tell "this grep published no long options" from "our regex stopped matching"; the
    exit code can, so the floor never degenerates into an excuse.
    """
    documented = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]+)",
                                (completed.stdout or "") + (completed.stderr or "")))
    if len(documented) >= _D5_LONG_OPTION_FLOOR:
        return documented
    if completed.returncode != 0:
        raise _D5NoDocumentedUniverse(
            f"`grep --help` published only {len(documented)} long options and exited "
            f"{completed.returncode} — this grep answers its usage on another stream or not at "
            "all (BSD grep 2.6.0-FreeBSD writes usage to stderr and exits 2), so this host has no "
            "documented universe to pin the option set against (#14303 review, P2)")
    raise AssertionError(
        f"only {len(documented)} long options were parsed out of a `grep --help` that exited 0 — "
        "the derivation is broken, and a broken derivation must not be able to certify anything "
        f"(the floor is {_D5_LONG_OPTION_FLOOR}; #14027, #14303 review P2)")


def _d5_long_option_candidates(name: str) -> list[str]:
    """The long options `name` could be an abbreviation OF, one entry per GREP option.

    ⛔ MEASURED ON GNU grep 3.11, and the `--colour` ALIAS is the whole difficulty (#13995):
      * `--col`, `--colo`, `--colou` and `--colour` are all accepted, and `--col=always` colours
        the output — the `-u` spelling is an alias of `--color`, not a second option;
      * `--cou=always` is refused with `option '--count' doesn't allow an argument`, so a prefix
        that matches `--count` resolves to `--count` and the ALIAS is not a rival;
      * but `--colou` matches NOTHING once `--colour` is set aside, so the alias has to be a
        FALLBACK — otherwise a spelling grep runs (`2` printed) is declined and a real
        prescription can hide behind the one that was read (a false PASS, the defect this cell
        exists to refuse);
      * `--c`/`--co` are REFUSED and grep's own message lists the colour spellings among the
        possibilities, while `--re` is refused naming `--recursive` and `--regexp` — so ambiguity
        is counted over the distinct options minus the alias, which is what this returns.
    """
    if name in _d5_all_long_options():
        # ⛔ AN EXACT NAME IS UNAMBIGUOUS EVEN WHEN IT PREFIXES LONGER ONES (#13995, MEASURED):
        # `--exclude` IS an option while `--exclude-dir` and `--exclude-from` also start with it,
        # and getopt takes the exact match — counting the longer names as rivals declined
        # `grep -c --exclude '*.md' '^[+-]$'`, which grep runs and counts (measured: 2).
        return [name]
    candidates = [option for option in _d5_all_long_options()
                  if option != "--colour" and option.startswith(name)]
    if not candidates and "--colour".startswith(name):
        candidates = ["--colour"]
    return candidates


def _long_option_is_accepted(name: str) -> bool:
    """Will `grep` accept this long option SPELLING — as written, or as an abbreviation?

    ⛔ `grep` REFUSES an unknown name and an AMBIGUOUS prefix (`--re` names both `--regexp` and
    `--recursive`; measured: `option '--re' is ambiguous`). Grading the expression of a command
    grep will not run is a false RED on a document that merely mentions it, so the walk declines
    such an invocation instead (#13995, MEASURED).
    """
    return name == "--" or len(_d5_long_option_candidates(name)) == 1


def _resolved_long_option(option: str) -> str | None:
    """The long option `option` names, honouring GNU getopt_long UNAMBIGUOUS ABBREVIATION.

    ⛔ AN ABBREVIATION IS A SPELLING OF THE SAME FLAG (#13995, MEASURED). `grep -c --max-c 2
    '^[+-]$'` is a real count of the blank class (measured: prints `2`, exit 0), and while the
    arity test was exact membership its `2` was read as the EXPRESSION — a second BLANK-class
    prescription in that spelling left the arm GREEN. The rule is getopt's own: a prefix that
    names EXACTLY ONE member names that member; one that names several (`--ex`, `--d`) is
    ambiguous and answered `None` rather than guessed. The set searched is grep's WHOLE
    long-option universe — every name `grep --help` documents, PLUS whatever undocumented alias the
    grep ON PATH actually runs (`_d5_undocumented_long_options()`, MEASURED and empty on
    grep ≥ 3.12, which removed `-u`; #14138), — ⛔ NOT the `_ARG_TAKING_LONG_FLAGS` ∪
    `_PATTERN_SUPPLYING_LONG_FLAGS` pair, which answer a DIFFERENT question (what a resolved
    option's ARGUMENT is, in `_d5_argument_role`) and are too narrow to decide ambiguity: `--re`
    names `--regexp` among them and still names `--recursive` in grep's own terms. A sentence here
    used to name that pair as the searched universe (#13995 review, P3 — MEASURED); it stopped
    being true when the ambiguity fix landed one function over, and the guard it described as
    still-holding is the one the acceptance gate below now carries.

    ⛔ AND THE ARITY SET GETS NO SHORT-CIRCUIT (#14027 review round 7 — MEASURED): the line
    `if option in _ARG_TAKING_LONG_FLAGS: return option` used to sit here and was INERT — every
    member of that set is one exact word of `_d5_all_long_options()`, so the candidate rule below
    answers `[option]` for each of them (measured over all fifteen; deleting the line reds 0 of
    the 8 cells that touch this function). A member the arity set could reach but the universe
    could not would be exactly the drift `test_…knows_every_long_option_grep_documents` pins, so
    the shortcut could never be the thing that kept the two in step — and that subset relation is
    now ASSERTED there rather than inferred, so an arity entry outside grep's documented set reds
    by name instead of resolving through a branch that no longer exists.
    """
    # ⛔ AND THE PREFIX MUST BE UNAMBIGUOUS IN GREP'S OWN TERMS, not only among the options whose
    # arity this reader needs: `--re` names `--regexp` here but `--recursive` too, and grep
    # refuses it (measured). `_d5_long_option_candidates` is that test, alias handling included.
    candidates = _d5_long_option_candidates(option)
    return candidates[0] if len(candidates) == 1 else None


#: The long options that SUPPLY THE PATTERN (`-e`/`-f` long forms). Once one of them is present
#: the positional operands of the command are FILES, never an expression — `grep -c -f /tmp/pats
#: '^[+-]$'` fails with `No such file or directory` rather than counting anything (measured) — so
#: the walk declines such an invocation instead of reporting a count of a FILE OPERAND.
#: ⛔ THAT IS TRUE OF THE FILE-SUPPLYING HALF ONLY, AND THE SENTENCE USED TO SAY IT OF BOTH
#: (#14027 round 6, P3 — MEASURED): `--file` (and `-f`) make the invocation UNREAD, while
#: `--regexp` (and `-e`) make the POSITIONALS files and report the flag's OWN argument as the
#: prescription — `_d5_count_prescriptions("grep -c --regexp '^[+-]$'")` is `['^[+-]$']`, which
#: grep really counts (`2`). Declining `--regexp` would have been the false PASS #13962 retires.
_PATTERN_SUPPLYING_LONG_FLAGS = frozenset({"--regexp", "--file"})

#: GNU grep's short flags that SUPPRESS a count, or replace its output with something else —
#: measured on GNU grep 3.11: `-lc` prints the FILENAME, `-Lc` prints the files WITHOUT a match,
#: `-qc` prints nothing at all. They WIN over `-c` whatever the cluster order (`-cl` is the same
#: command as `-lc`), so a word carrying one of them is not a count request however the rest of
#: its cluster is spelled (#13995 review round 4, P2).
_D5_COUNT_SUPPRESSING_SHORT_FLAGS = frozenset("lLq")

#: The LONG spellings of the same suppression — `--files-with-matches` prints a FILENAME,
#: `--files-without-match` prints the files WITHOUT a match, `--quiet`/`--silent` print nothing
#: (measured on GNU grep 3.11). Without this set `grep -c --quiet '^[+-]$'` is GRADED as a
#: BLANK-class prescription, so a sentence mentioning one in prose is refused with "prescribes 2
#: BLANK-line count commands" — a false RED on a correct document, the direction this file calls
#: the worse one (#13995, MEASURED).
_D5_COUNT_SUPPRESSING_LONG_FLAGS = frozenset({
    "--files-with-matches", "--files-without-match", "--quiet", "--silent",
})


def _long_flag_matches(word: str, flag: str, rivals: tuple[str, ...] = ()) -> bool:
    """Is `word` the long flag `flag`, or an UNAMBIGUOUS ABBREVIATION of it?

    `rivals` states the prefixes that name a DIFFERENT option, so `--c`/`--co` (ambiguous between
    `--count`, `--color` and `--context`, and refused by grep — measured) are not read as counts
    while `--cou`/`--coun` are. The `=value` tail is stripped and a bare `--` is not a flag.
    """
    name = word.split("=", 1)[0]
    if name == "--":
        return False
    return flag.startswith(name) and not any(rival.startswith(name) for rival in rivals)

#: The characters that END a shell word in this document's prose: whitespace, plus the characters
#: that cannot appear inside a `grep` argument there — `` ` `` closes the code span, and
#: `;`/`|`/`&`/`(`/`)`/`<`/`>` are shell operators. Used by `_d5_words`, which is a walk rather
#: than `str.split` because a QUOTED section may hold spaces (`--label='x -c'` is ONE word).
_D5_WORD_END = frozenset(" \t\r\n`;|&()<>")


def _d5_words(text: str, start: int):
    """Yield `(word, end)` for consecutive shell-ish words of a command beginning at `start`.

    `word` keeps its quotes, so the caller can tell a quoted operand from a bare one; `end` is
    the index just past it. A quoted section is part of the word it is attached to and may
    contain spaces (`--label='x -c'`), which is why this is a walk and not `str.split`. A word
    ends at `_D5_WORD_END`: at whitespace the walk continues, at any other end character it
    STOPS, because that character is where the code span or the shell command ends.
    """
    i, n = start, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        if text[i] in _D5_WORD_END:
            return
        word: list[str] = []
        while i < n and text[i] not in _D5_WORD_END:
            if text[i] in "'\"":
                close = text.find(text[i], i + 1)
                if close < 0:
                    return  # an unterminated quote is not a word this reader can read
                word.append(text[i:close + 1])
                i = close + 1
                continue
            word.append(text[i])
            i += 1
        yield "".join(word), i


def _d5_flag_word(text: str) -> tuple[str, str] | None:
    r"""`(option, attached_value)` for a plausible `grep` flag TOKEN, or `None` if it is not one.

    `None` is what keeps prose out: while the flag word was a bag of characters it swallowed
    the closing backtick and the comma, so `` `grep -c`, '^[+-]$' `` became a prescription. A
    flag token here is `--`/`-` plus alphanumerics (which still expresses `-c`, `-Ec`,
    `--count`, `-C3`, `-m2`), optionally followed by an ATTACHED value — `=value` or a quoted
    section. ⛔ A QUOTED SECTION ON A CLUSTER THAT TAKES NO ARGUMENT IS MORE FLAG CHARACTERS, NOT
    A VALUE (#13995, MEASURED — the shell does exactly this): `grep -c'x' '^\+$'` is argv
    `-cx '^\+$'` and really counts, so it is READ; a NON-alphanumeric suffix is what stays
    unread, because grep answers `invalid option` to `` `-c'^\+$'` `` → `grep: invalid option --
    '^'`, rc 2. So `-e'…'` is an attached value while `-c'x'` is a flag character — the docstring
    used to say `grep -c'…'` is "not read at all", which was false and pinned a real count as
    unread (#14027 review round 2, P3). Whether grep would accept the unquoted residue elsewhere
    is grep's question, not this reader's.
    ⛔ ITS RESIDUAL IS A VALUE GRAMMAR THIS READER DOES NOT MODEL, AND IT SAYS SO (#14027 third
    review round, P3 — MEASURED): an argument-taking SHORT flag's attached value is not validated,
    so `grep -c -A, '^[+-]$'` — which grep refuses (`,: invalid context length argument`, rc 2) —
    is read and graded. NO SHAPE TEST CLOSES IT: grep runs `-A2` and `-m-1` and refuses `-A,` and
    `-Anever`, while `-e`/`-f` take arbitrary text, so the sound rule would be a per-flag VALUE
    grammar — a model this reader deliberately does not carry. Pinned as a KNOWN-UNGRADED spelling
    in `test_the_d5_count_reader_reads_an_UNQUOTED_pattern_and_refuses_an_ambiguous_prefix`, so the
    gap is visible rather than implied.
    """
    if text in ("-", "--"):
        return text, ""
    # grep is the judge of the NAME (`-mc`, `-Dc`, `-Ac` are all rejected by it, measured), so no
    # name is validated here. `_ARG_TAKING_SHORT_FLAGS` IS consulted, for one purpose: whether an
    # QUOTED section on a short cluster is its ATTACHED VALUE or more FLAG CHARACTERS — `-e'…'`
    # is a value, `-c'x'` is `-cx` (#13995, MEASURED, and the shell does the same). ⛔ THE ROWS
    # BELOW ARE PINNED, because a sentence about a set that nothing varies on is the
    # inert-narration defect this file bans: `grep -c'x' '^\+$'` becomes a READABLE count (`-cx`,
    # then the quoted token IS the expression — real grep counts it), which falsifies an "attached
    # value is inadmissible" rule that pinned a real count as unread. ⛔ THE WITNESS FOR THE
    # ALPHANUMERIC TEST IS `grep -c'-' '^[+-]$'`, NOT `grep -c'^\+$'` (#14027 review round 7, P2 —
    # MEASURED): the pair this comment used to nominate did not witness anything, because
    # `grep -c'^\+$'` has NO trailing operand at all, so it is `[]` for a cause that has nothing
    # to do with the test — deleting the condition leaves both nominated rows unchanged (measured).
    # `grep -c'-' '^[+-]$'` DOES vary: `[]` here, `['^[+-]$']` with the condition deleted, because
    # argv `-c-` is not a flag cluster and a non-alphanumeric suffix stays unread. ⛔ THE LONG
    # FORM'S QUOTED TAIL IS *ALSO* MORE NAME, AND THE SAME
    # REASON APPLIES (#14027 final review, P2 — MEASURED): the shell removes the quotes, so
    # `grep -c --count'x' '^[+-]$'` is argv `--countx` and grep refuses it (`unrecognized option
    # '--countx'`, rc 2). This sentence used to declare that spelling READ "because grep is the
    # judge of the name and not a table of ours" — but `_long_option_is_accepted` IS that table
    # and it is what declines `--count,`/`--countx` one branch below, so the declared residual was
    # a false PASS sitting beside the rule that refuses it. The name returned here is therefore
    # the WHOLE word with the quotes removed (`--count''` is argv `--count` and IS read), and the
    # acceptance test decides.
    if text.startswith("--"):
        match = re.fullmatch(r"(--[A-Za-z0-9][A-Za-z0-9-]*)(.*)", text, re.S)
    elif text.startswith("-"):
        match = re.fullmatch(r"(-[A-Za-z0-9]+)(.*)", text, re.S)
    else:
        return None
    if match is None:
        return None
    option, rest = match.group(1), match.group(2)
    # ⛔ A SHORT CLUSTER SPLITS AT ITS FIRST ARGUMENT-TAKING FLAG, AND THE TAIL IS THAT FLAG'S
    # VALUE (#14027 round 9, P2 — MEASURED, and this was a live false PASS). grep parses a cluster
    # left to right and the first flag that takes an argument OWNS the rest: `grep -c -Dread
    # '^[+-]$'` is `-c` then `-D` with the value `read`, and it prints `2` on the probe, so it
    # COUNTS the blank class. Treating the whole alphanumeric run as the name (`-cDread`) left its
    # last character looking like a flag still awaiting a separate word, so the quoted expression
    # was consumed as that value and the invocation came back UNREAD — a second blank-class
    # prescription in this spelling sat invisible beside the one that was read. Without this split
    # the reader also disagreed with its own `-A2` pin in spirit (there the tail IS the value)
    # purely because `A` happened to be the last character.
    if not option.startswith("--"):
        for split_at in range(1, len(option)):
            if option[split_at] in _ARG_TAKING_SHORT_FLAGS:
                value = option[split_at + 1:] + rest
                # ⛔ QUOTE REMOVAL IS OURS, AND ONLY WHERE THE SHELL DID IT (#14027 round 9, P5 —
                # MEASURED): `grep -c -ec'^[+-]$'` is argv `-e` + `c^[+-]$`, so the VALUE is
                # `c^[+-]$` — those quotes were the shell's, not the pattern's. Reporting
                # `c'^[+-]$'` handed back a string no argv can contain (verdict-neutral, the class
                # test rejects it either way, but a reported prescription must be an argument).
                # ⛔ A TAIL THAT STARTS WITH A QUOTE (`-ce'…'`) IS RETURNED WITH THAT QUOTE, AND
                # THAT IS THIS FUNCTION'S OWN DOING — NOT THE SHELL'S (#14027 round 11, P2 —
                # MEASURED). The sentence here used to say "the shell removed only the OUTER
                # quotes there, and that form is read by the quoted branch below"; both halves are
                # false. The shell removes EVERY quote character (`set -- -ce'^[+-]$'` gives argv
                # `-ce^[+-]$`, and `-e`'s value is `^[+-]$`), and the quoted branch below is not
                # reached at all: the split returns first, and that branch is reachable only when
                # nothing in the cluster takes an argument. The leading quote is therefore the one
                # shape this slice still hands back as a value that is not itself an argv element
                # — a declared residual, stripped downstream by `_d5_quoted_literal` and rejected
                # by the class test, so no verdict depends on it (`-ce'^[+-]$'` and `-ce^[+-]$`
                # both end as `['^[+-]$']`). ⛔ THE READER HAS NO BACKSLASH MODEL, SO THIS RULE IS
                # NARROWER THAN "quotes the shell removed": `grep -c -ec"it's"` is the word
                # `-ecit's`, whose value is `cit's`, and the quote-strip here answers `cits`
                # (verdict-neutral — neither string is the class — but not an argv element either).
                if value and value[0] not in "'\"":
                    value = value.replace("'", "").replace('"', "")
                return option[:split_at + 1], value
    if not rest:
        return option, ""
    if not (rest.startswith("=") or rest[0] in "'\""):
        # ⛔ AN UNQUOTED ATTACHED VALUE IS STILL A VALUE (#13995, MEASURED): `grep -c -e^[+-]$`
        # is `-e` with the pattern `^[+-]$`, and real grep counts the blank class with it. That
        # case never reaches here any more — the cluster SPLIT at the top of this function takes
        # `-e^[+-]$` apart as `-e` + `^[+-]$` (#14027 round 9, P2) — so a token that arrives with
        # an unquoted, non-`=` tail has NO argument-taking character in it, and is therefore not a
        # flag word at all: `-c^[+-]$` is argv `grep: invalid option`, rc 2 (measured).
        if option.startswith("--"):
            # ⛔ A LONG OPTION NEVER OWNS AN UNQUOTED VALUE, SO NOTHING IS ATTACHED HERE AND THE
            # TAIL IS PART OF THE NAME (#13995 review, P3 — MEASURED): getopt requires `=` for a
            # long option's argument, so an unquoted tail is not a value but MORE NAME, and a name
            # grep refuses must stay refused. ⛔ THE TAIL IS RETURNED, NOT DISCARDED (#14027 final
            # review, P3 — MEASURED): this used to `return option, None`, which threw `rest` away
            # so `grep -c --count, '^\+$'` was graded as `--count` — while real grep answers
            # `unrecognized option '--count,'`, rc 2. Grading the expression of a command grep
            # will not run is a false PASS here (the invocation counts nothing) and a false RED in
            # the other direction (a document that merely mentions it). `_long_option_is_accepted`
            # declines `--count,`/`--countx` because neither names an option; the earlier form of
            # this line returned `--count`, which that test ACCEPTS, so the tail was discarded and
            # never declined — the prose claimed a check the code did not run.
            return option + rest, ""
        # ⛔ THIS IS `None`, NOT A MEMBERSHIP TEST (#14027 round 10, P3 — MEASURED). The split
        # above iterates `range(1, len(option))` — every index of `option[1:]` — and the short
        # regex `(-[A-Za-z0-9]+)` guarantees `len(option) >= 2`, so `option[-1]` is always inside
        # `option[1:]`. A token that reaches this line therefore has NO argument-taking character
        # there, which makes `option[-1] in _ARG_TAKING_SHORT_FLAGS` unreachable-True: the
        # `(option, rest)` branch was dead. Measured, zero exceptions: instrumented over every
        # 1-3 character alphanumeric cluster, over 6,055,850 enumerated tokens, and over this
        # repo's own docs corpus the branch never fired, and
        # `_d5_flag_word` / `_d5_blank_class_prescriptions` / `_d5_count_prescriptions` are
        # unchanged by its removal. The file's own rule bans a member nothing can reach, and the
        # one spelling the test was written for (`-e^[+-]$`) is read by the split one branch up.
        # ⛔ NO TOKEN TOTAL IS STATED: the reach count is always a multiple of the number of
        # clusters with no argument-taking character in `option[1:]`, so a total-and-hits pair is
        # arithmetic that can silently stop agreeing — which the previous cut of this comment
        # demonstrated by getting it wrong (#14027, P3). ⛔ AND THE CORPUS SWEEP'S RESULT IS STATED
        # WITHOUT A REACH COUNT, FOR THE SAME REASON (#14027 review of this head, P3 — MEASURED):
        # the sweep needs a naive whitespace splitter while the reader's own `_d5_words` yields 0
        # by construction, so repeated sweeps of the same corpus answered anywhere from 927 to 947
        # depending on the tokeniser, and this line used to carry one such figure. What is
        # reproducible is the claim it supported — the branch never fires — so that is what is
        # stated.
        return None
    if option.startswith("--"):
        if rest.startswith("="):
            return option, rest  # `--regexp=^[+-]$` — for a LONG option the `=` IS the separator
        # ⛔ A QUOTED TAIL IS MORE NAME, NOT A VALUE — unless the quotes were hiding the `=` that
        # makes it an argument (#14027 final review, P2 — MEASURED): `--count'x'` is argv
        # `--countx` once the shell has removed the quotes, and grep refuses it. Returning it as
        # an ATTACHED value read the word as `--count` and graded a command grep will not run as
        # the blank class — the same false PASS the unquoted tail closes one branch above.
        # `--count''` collapses to `--count`, which grep runs and counts (measured 2), so this is
        # quote removal and not a blanket refusal.
        tail = rest.replace("'", "").replace('"', "")
        if tail.startswith("="):
            # …and a `=` that SURVIVES the quote removal is still the SEPARATOR (#14027 second
            # review round, P3 — MEASURED): `--context'=2'` is argv `--context=2`, which grep
            # runs and counts (`2` on the 4-line probe), and `--regexp'=^[+-]$'` is argv
            # `--regexp=^[+-]$`, i.e. the blank class. Appending that tail to the NAME made both
            # unread — a false RED on commands grep executes, and a REGRESSION this arm did not
            # have before the quoted-tail rule. Quote removal first, then the separator rule.
            return option, tail
        return option + tail, ""
    # ⛔ THE SHELL HAS ALREADY REMOVED THE QUOTES, SO A QUOTED SECTION ON A CLUSTER THAT TAKES NO
    # ARGUMENT IS MORE FLAG CHARACTERS (#13995, MEASURED). `grep -c'a' '^[+-]$'` is argv `-ca`
    # and really counts the BLANK class (`2` measured) — the quote does not make `a` a value, and
    # declining the word left that spelling an invisible second prescription. `grep -c'^[+-]$'`
    # is argv `-c^[+-]$`, which grep rejects (`invalid option`), so a NON-alphanumeric suffix is
    # still `None`. An argument-taking cluster keeps the attached-value reading (`-e'\'^[+-]$\''`).
    # ⛔ NO SECOND ARGUMENT-TAKING TEST HERE (#14027 round 10, P3 — MEASURED): the split above
    # has already returned for every short cluster carrying one, so this point is reachable ONLY
    # when nothing in `option[1:]` takes an argument. The guard that used to sit here
    # (`if not any(char in _ARG_TAKING_SHORT_FLAGS for char in option[1:])`) was therefore
    # constant-True — instrumented, it never once took the False branch — and the
    # `return option, rest` beneath it was unreachable. The file's own rule bans a member nothing
    # can reach, so the guard is gone and the branch states what it is.
    unquoted = option + rest.replace("'", "").replace('"', "")
    return (unquoted, "") if re.fullmatch(r"-[A-Za-z0-9]+", unquoted) else None


def _d5_needs_a_separate_value(option: str) -> bool:
    """Does this flag token still await its argument as the NEXT word?

    Only where the flag takes an argument and none is ATTACHED: a short cluster's LAST character
    (`-A` in `grep -c -A '2' '^[+-]$'`), or a required-argument long option written without `=`.
    """
    if option.startswith("--"):
        # An UNAMBIGUOUS ABBREVIATION awaits its argument exactly as the full spelling does:
        # `--max-c 2` is `--max-count 2` (measured), and reading that `2` as the expression was
        # the false PASS `_resolved_long_option` closes (#13995).
        return _resolved_long_option(option) in _ARG_TAKING_LONG_FLAGS
    return bool(option[1:]) and option[-1] in _ARG_TAKING_SHORT_FLAGS


def _d5_supplies_the_pattern(option: str) -> bool:
    """Does this flag token supply the PATTERN, so that every positional operand is a FILE?"""
    if option.startswith("--"):
        return _resolved_long_option(option) in _PATTERN_SUPPLYING_LONG_FLAGS
    for char in option[1:]:
        if char in "ef":
            return True
        if char in _ARG_TAKING_SHORT_FLAGS:
            return False  # the rest of this word is THAT flag's attached value
    return False


def _d5_quoted_literal(word: str) -> str | None:
    """The text of the FIRST quoted section of `word`, or `None` if it does not open with one.

    The expression must be quoted, so an unquoted operand is a declared residual rather than a
    guess. Only the first literal is read, which is why a CONCATENATED expression is read up to
    its first fragment. `$'…'` (ANSI-C quoting) reads as the same literal.
    """
    if word.startswith("$"):
        word = word[1:]
    if not word or word[0] not in "'\"":
        return None
    close = word.find(word[0], 1)
    return None if close < 0 else word[1:close]


def _d5_argument_role(option: str) -> str | None:
    """What this flag's ARGUMENT is: `pattern` (`-e`/`--regexp`), `file` (`-f`/`--file`), `other`.

    `other` is every argument-taking flag whose value is not a pattern (`-A`, `-m`,
    `--exclude`, …); `None` is a flag that takes no argument at all. The distinction is what
    lets a MATCHED argument be collected as a prescription instead of thrown away — a command
    with two `-e` flags counts the UNION of both patterns, so both are prescriptions
    (#13995, MEASURED).
    """
    if option.startswith("--"):
        resolved = _resolved_long_option(option)
        if resolved == "--regexp":
            return "pattern"
        if resolved == "--file":
            return "file"
        return "other" if resolved in _ARG_TAKING_LONG_FLAGS else None
    for index, char in enumerate(option[1:]):
        if char in _ARG_TAKING_SHORT_FLAGS:
            if option[2 + index:]:
                return "other"  # the rest of this word is that flag's ATTACHED value
            return "pattern" if char == "e" else "file" if char == "f" else "other"
    return None


def _d5_command_expression(text: str, start: int) -> list[tuple[str, str]] | None:
    """`(flags, expression)` for EVERY prescription the `grep` at `start` carries, or `None`.

    ⛔ A COMMAND MAY CARRY SEVERAL PATTERNS, AND EVERY ONE OF THEM IS A PRESCRIPTION
    (#13995, MEASURED). `grep -c -e 'decoy' -e '^[+-]$'` counts the union of both patterns, so
    the blank class IS counted; while the reader returned the FIRST `-e` pattern only — or, worse,
    declined the command because a pattern was supplied — a second BLANK-class prescription
    written as a second `-e` left the arm GREEN. Each `-e`/`--regexp` argument is reported now,
    and the positional operand is still a FILE (so it is not reported).

    ⛔ A FLAG'S VALUE IS CONSUMED, NEVER READ AS THE EXPRESSION (#13995 review, P2 — MEASURED).
    `grep -c --exclude '*.md' '^[+-]$'` counts the blank lines (measured: `2` on the 4-line
    probe); the quoted token `'*.md'` is `--exclude`'s GLOB. The raw-text reader this replaces
    reported `*.md` as the expression, so the command was neither the blank class nor a shape the
    residual list names — a SECOND blank-class prescription appended that way left the arm GREEN,
    the false PASS #13962 exists to retire. An argument-taking flag therefore consumes the next
    word, and the expression is the first non-flag word after that.

    `None` means the invocation is UNREAD, which the constant's residual list names: a flag word
    that is not a plausible `grep` option; a non-flag word with no quote (an unquoted operand —
    the expression itself, or a filename before it); a quoted operand when a flag already supplies
    the pattern FROM A FILE (`-f`/`--file` make the positionals FILES and leave nothing to report);
    and an invocation that ends while a flag still awaits its value.
    ⛔ `-e`/`--regexp` IS NOT ONE OF THESE CAUSES (#14027 round 6, P3 — MEASURED): it also makes the
    positionals FILES, but its OWN argument is returned as a prescription —
    `grep -c -e 'decoy' '^[+-]$'` yields `['decoy']` — so the invocation is read, not unread.
    """
    flags: list[str] = []
    patterns: list[str] = []
    supplies_the_pattern = False
    awaiting_role: str | None = None
    for word, _end in _d5_words(text, start):
        if awaiting_role is not None:
            if awaiting_role == "pattern":
                # ⛔ AN UNQUOTED `-e` VALUE IS STILL THE PATTERN (#13995, MEASURED):
                # `grep -c -e ^[+-]$ …` counts the blank class, and requiring a quote here made
                # that a second prescription the arm could not see. The quote requirement stays
                # for a POSITIONAL operand, where an unquoted word is the declared residual.
                literal = _d5_quoted_literal(word)
                patterns.append(literal if literal is not None else word)
            awaiting_role = None
            continue  # this word IS that flag's value, not the expression
        if word.startswith("-"):
            parts = _d5_flag_word(word)
            if parts is None:
                return None
            option, attached = parts
            if option.startswith("--") and not _long_option_is_accepted(option):
                return None  # grep refuses this spelling, so there is no command to grade
            flags.append(option)
            supplies_the_pattern = supplies_the_pattern or _d5_supplies_the_pattern(option)
            role = _d5_argument_role(option)
            if role in ("pattern", "file") and attached:
                # an ATTACHED pattern: `-e'^[+-]$'`, `--regexp='^[+-]$'`
                # ⛔ `=` SEPARATES A LONG OPTION'S ARGUMENT, NOT A SHORT ONE'S (#14027 final
                # review, P2 — MEASURED): getopt gives a short option the REST OF THE WORD as its
                # value, so in `grep -c -e=^[+-]$` the pattern IS `=^[+-]$` and grep counts 0 of
                # the blank class (`printf '+\n-\nxx\n\n' | grep -c -e=^[+-]$` → 0, rc 1),
                # while `grep -c -e=a` on a file holding `=a` prints 1 — the `=` is not a
                # separator. Stripping it unconditionally graded a command that counts ZERO of
                # this class AS the class: the false PASS #13962 exists to refuse, and one this
                # reader INTRODUCED (before it, `literal` stayed `None`, `patterns` stayed empty
                # and the invocation was UNREAD, i.e. fail-closed).
                raw = (attached[1:] if option.startswith("--") and attached.startswith("=")
                       else attached)
                literal = _d5_quoted_literal(raw)
                if role == "pattern":
                    # …and an UNQUOTED attached value is the pattern too: `--regexp=^[+-]$`
                    # counts the blank class (measured), `-e^[+-]$` likewise (#13995).
                    patterns.append(literal if literal is not None else raw)
                awaiting_role = None
            else:
                # ⛔ ONLY A FLAG WHOSE ARGUMENT IS STILL MISSING AWAITS THE NEXT WORD, AND IT IS
                # `not attached` THAT ESTABLISHES IT (#14027 round 11, P3 — MEASURED). `-A2` is
                # split into `('-A', '2')` before it reaches here, so `option`'s last character IS
                # an argument-taking one and `_d5_needs_a_separate_value("-A")` answers True; what
                # keeps the expression from being eaten is the non-empty `attached`, which
                # short-circuits the condition. A comment attributing it to the last-character
                # test invites a fixer to drop `not attached` and reopen the false PASS.
                awaiting_role = (role if role is not None and not attached
                                 and _d5_needs_a_separate_value(option) else None)
            continue
        literal = _d5_quoted_literal(word)
        if literal is None or supplies_the_pattern:
            break  # an unquoted operand, or a FILE operand beside an `-e`/`-f` pattern
        patterns.append(literal)
        break
    if not patterns:
        return None
    return [(" ".join(flags), literal) for literal in patterns]


def _short_flag_head(word: str) -> str:
    """The part of a short-flag cluster that is FLAGS — up to the first flag that takes an
    argument (`-A2`: only `A` is a flag, `2` is its argument; `-ec`: `c` is `-e`'s pattern).

    Shared by `_requests_a_count` and `_suppresses_the_count` so the two cannot disagree about
    where a word stops being a run of flags (#13995 review round 4, P2).
    """
    head = []
    for char in word[1:]:
        if char in _ARG_TAKING_SHORT_FLAGS:
            break  # the remainder of this word is that flag's argument
        head.append(char)
    return "".join(head)


def _suppresses_the_count(word: str) -> bool:
    """Does this ONE grep flag word suppress the count (`-l`, `-L`, `-q` in its FLAG part)?

    Only the part before an argument-taking flag is examined: `-A2l` carries an `l` inside `-A`'s
    argument, which is not a flag at all. Long words (`--count`, `--color=never`, `--`) cannot
    carry a short cluster and are answered `False`.

    ⛔ THE ONE PLACE A SUPPRESS LETTER IS STILL INVISIBLE IS A DECLARED RESIDUAL, AND IT IS NOT
    THE SPELLING IT LOOKS LIKE (#13995 review, P2 — MEASURED). During `#13995`'s round 4 this
    docstring said a value ATTACHED to a short flag is read as part of the cluster, so a path
    holding an `l`/`L`/`q` makes this answer `True` — that is false of a flag that TAKES an
    argument: `_suppresses_the_count("-f/tmp/lpats")` is `False`, because the scan stops at `f`
    and the `l` is inside that flag's value. The only word on which the mechanism holds is a
    suppress letter inside a value attached to a flag that takes NO argument (`-c/lpats` →
    `True`), i.e. `grep: invalid option`, a command that cannot run. So the value of an
    argument-taking flag is excluded here CORRECTLY, and there is no ungraded-valid-spelling to
    declare.
    """
    if word.startswith("--"):
        # ⛔ UNIQUENESS ACROSS THE SET, NOT MEMBERSHIP OF ONE NAME (#13995, MEASURED). `--quie`
        # and `--files-with-match` suppress in grep, so they must suppress here; `--files` matches
        # BOTH `--files-with-matches` and `--files-without-match` and grep refuses it as AMBIGUOUS
        # (measured), so neither may be certified.
        # ⛔ THE CERTIFICATION IS THE LENGTH TEST ALONE (#13995 review, MEASURED). An
        # `and not "--file".startswith(name)` clause stood here, on the prose that "`--f` also
        # names `--file`" — and it was INERT: every name it could see (`--f`, `--fi`, `--fil`,
        # `--file`) already matches BOTH `--files-*` members, so `len(candidates) == 1` is false and
        # the `and` is never consulted. `--f` is still refused here, by the length test, because
        # grep refuses it (measured: `option '--f' is ambiguous; possibilities: '--fixed-regexp'
        # '--file' '--files-with-matches' '--files-without-match'`), and `--file` is itself no
        # suppressor: it refuses by that same LENGTH test, because it also matches BOTH
        # `--files-*` members (measured: `'--files-with-matches'.startswith('--file')` is True,
        # and likewise `--files-without-match`) — not because it matches nothing. Pinned in the
        # long-spelling cell, so the refusal cannot be lost.
        name = word.split("=", 1)[0]
        candidates = [flag for flag in _D5_COUNT_SUPPRESSING_LONG_FLAGS if flag.startswith(name)]
        return len(candidates) == 1
    return (
        word.startswith("-")
        and any(ch in _D5_COUNT_SUPPRESSING_SHORT_FLAGS for ch in _short_flag_head(word))
    )


def _inverts_the_match_set(flags: str) -> bool:
    """Does this run of grep flags INVERT the match set (`-v` / `--invert-match`)?

    A count under `-v` counts the COMPLEMENT of the expression, so the expression is not the class
    that was counted — `grep -vc '^[+-]$'` counts the NON-blank lines. It is still a count of the
    same SHAPE, and a document may legitimately prescribe both, so this is answered separately
    from `_requests_a_count` rather than folding the two into one predicate: the inversion is
    carried to `_d5_blank_class_prescriptions`, which declines to grade it against the positive
    class (#13995 review round 4, P2 — MEASURED).

    ⛔ THE LONG SPELLING IS MATCHED WITH NO RIVAL LIST, AND NOTHING OBSERVABLE IS LOST BY THAT
    (#13995 review — MEASURED). `--i` and `--in` ARE ambiguous in grep (`grep --i` prints
    `option '--i' is ambiguous; possibilities: '--include' '--ignore-case' '--initial-tab'
    '--invert-match'`, exit 2), but neither can reach this predicate:
    `_long_option_is_accepted` REFUSES both — `--i` names FOUR options in grep's whole set and
    `--in` three (`grep --in` measured: `'--include' '--initial-tab' '--invert-match'`) — so
    `_d5_command_expression` declines the invocation before any flag is read
    (`grep -c --i '^[+-]$'` is pinned as `[]` in the complement cell, with the reason). ⛔ THE
    MECHANISM IS NAMED BECAUSE IT MOVED (#13995, MEASURED): while `_resolved_long_option` alone
    judged this, it read `--i` as `--include`, an ARITY option, and the decline came from the next
    word being taken as its argument — a different cause with the same `[]`, and a claim that
    `_resolved_long_option` returns `'--include'` here is now simply false (it returns `None`;
    the prefix is ambiguous). The prefixes grep DOES accept (`--inv`, `--inver`, `--invert`) are
    read as the inversion and pinned, so a rival list added "for symmetry" with
    `_requests_a_count` would be a no-op WHOSE DIFFERENCE NO PIN CAN SEE (measured: adding one
    leaves all eight `test_the_d5_*` cells green) — which is why the asymmetry is recorded here
    rather than left to be re-derived.
    """
    for word in flags.split():
        if _long_flag_matches(word, "--invert-match"):
            return True
        if word.startswith("--") or not word.startswith("-"):
            continue
        if "v" in _short_flag_head(word):
            return True
    return False


#: GNU grep's flags that keep the COUNT shape and change WHAT THE EXPRESSION MATCHES: `-F` reads
#: the pattern as LITERAL TEXT and `-z` ends a record at a NUL byte instead of a newline. MEASURED
#: on GNU grep 3.11, on a probe diff whose two
#: blank lines are exactly `+` and `-`: `grep -c '^[+-]$'` prints `2`, while `-Fc`, `-zc`,
#: `--fixed` and `--null-d` print `0` — none of them counts this class. So grading one of them as
#: "exactly the lines that are one `+` or one `-`" is the `-v` defect one axis over: a command is
#: graded on WHAT IT COUNTS, never on the literal it prints beside the count.
#: ⛔ `--fixed-regexp` IS `--fixed-strings` UNDER ANOTHER NAME, AND IS DELIBERATELY NOT A MEMBER
#: (#13995 review, P2 — MEASURED). grep ACCEPTS it (`grep -c --fixed-regexp '^[+-]$'` -> rc 1,
#: prints 0), but `grep --help` does not document it, so it is absent from `_d5_all_long_options()`
#: — deliberately, because LISTING it there would make `--fixed-` falsely ambiguous (grep accepts
#: `--fixed-`, measured) and decline a command grep runs. The consequence is that
#: `_long_option_is_accepted` declines every alias spelling (`--fixed-regexp`, `--fixed-r`,
#: `--fixed-re`), so a member here would be UNREACHABLE: this line used to carry one and nothing
#: witnessed it — dropping it left the suite green, which is the inert-table-member defect
#: (agent-infra#3442). The residual is that those spellings are UNREAD, and ALL THREE are pinned
#: — `--fixed-regexp`, `--fixed-r` and `--fixed-re` — in
#: `test_the_d5_count_reader_does_not_grade_a_command_whose_pattern_is_NOT_A_REGEX`'s
#: `alias_spelling` loop, and `--fixed-regexp` again as a control in
#: `test_the_d5_count_reader_covers_the_undocumented_alias_grep_ACCEPTS` — the second witness is
#: REAL and MEASURED (making every long spelling readable reds both), and this paragraph is where a
#: previous version said "NOWHERE ELSE" and was wrong (#14027 round 8, P3). What that paragraph got
#: right, and what stays: the LONG-SPELLING cell is NOT a witness — it asserts nothing about an
#: alias (measured: `--fixed-regexp` occurs there only inside grep's own ambiguity message), which
#: is the `_D5_RETIRED_OVERCLAIMS` defect this file bans (#13962). It is FAIL-CLOSED: `-F` prints
#: `0` on the blank-class probe, so declining the alias can never grade a non-class as this class.
#: ⛔ THE NEIGHBOURS THAT ARE **NOT** HERE, measured on the same probe: `-i`, `-w`, `-x`, `-E`, `-G`
#: and `-P` all print `2`, so they leave this class intact; `--null` is `-Z` (a NUL after the FILE
#: name) and prints `2`, so it is a RIVAL of the `--null-data` prefix rather than a shortening of it.
#:
#: ⛔ AND THE RESIDUAL THIS TABLE DOES NOT COVER — `-m`/`--max-count` CAPS THE COUNT (#14027 round 4,
#: P3 — MEASURED). It changes nothing about what the pattern selects, only how much of the class is
#: counted: on a probe holding `+`, `-`, `+import os`, ` a`, `grep -c '^[+-]$'` prints `2` while
#: `grep -c -m1 '^[+-]$'` and `grep -c --max-count=1 '^[+-]$'` print `1`. Such a command is therefore
#: GRADED as this class, with a number smaller than the class; the cell's `-m 2` rows agree with the
#: class only because that cap is at or above its size. This is FAIL-CLOSED in the direction that
#: matters here — the executed row is bound by `_D5_BLANK_COUNT_RE`, so a capped command cannot make
#: a wrong count look right — and the cost is a false RED on a document that legitimately prescribes
#: a CAPPED second count. ⛔ AND A CROSS-FLAG CONFLICT IS GRADED TOO (#14027 round 9, P3 —
#: MEASURED): grep refuses `-cEG`/`-cEP`/`-cGP` (`conflicting matchers specified`, rc 2) and every
#: value in such a cluster is valid, so it sits outside the invalid-value cluster below and is
#: declared nowhere else — the same bounded false RED (the executed row is still
#: `_D5_BLANK_COUNT_RE`), closable by refusing an invocation that carries two of `-E`/`-G`/`-P`.
#: `egrep -c` is blind for the same reason (another tool spelling): measured
#: `2` on the probe, and this reader only knows `grep`.
#:
#: ⛔ AND A CLUSTER WHOSE ARGUMENT-TAKING MEMBER HAS AN INVALID VALUE IS GRADED, NOT DECLINED
#: (#14027 round 8, P3 — MEASURED, and the same on `origin/main`, so this is a DECLARATION of
#: pre-existing behaviour rather than a regression here). `_d5_flag_word` does not validate a
#: short flag's value — grep is the judge of the NAME — so the word is read as a count request and
#: its tail is never checked: `grep -cmw '^[+-]$'` is `grep: invalid max count` (rc 2) yet is
#: graded, and so are `grep -cDE` (`unknown devices method`) and `grep -cAc` (`invalid context
#: length argument`). This is the FALSE-RED direction — a document that merely MENTIONS such a
#: spelling would red the arm — and it is bounded: the executed row is still `_D5_BLANK_COUNT_RE`,
#: so nothing here can make a wrong count look right. Closing it means validating `m`/`A`/`B`/`C`
#: as numeric and `d`/`D` against their allowed sets; until then the classification above matches
#: the code. It was already true on `origin/main` at `d4a8dddc4e` — ⛔ the earlier cut of this
#: sentence cited `af3e8b0ffa`, which is THIS branch's first commit and therefore carries the
#: reader under test; it cannot witness what `origin/main` does (#14027 round 9, P3 — MEASURED:
#: `git merge-base --is-ancestor af3e8b0ffa origin/main` is rc 1, `d4a8dddc4e` is rc 0, and
#: `origin/main`'s own `_d5_count_prescriptions` answers `['^[+-]$']` for all three spellings).
_D5_RETARGETING_SHORT_FLAGS = frozenset("Fz")
_D5_RETARGETING_LONG_FLAGS = frozenset({"--fixed-strings", "--null-data"})
_D5_RETARGETING_LONG_RIVALS = ("--null",)


def _retargets_the_matched_text(flags: str) -> bool:
    """Does this run of grep flags make the EXPRESSION select a DIFFERENT set (`-F`, `-z`)?

    Answered separately from `_inverts_the_match_set` and from `_requests_a_count`, because the
    command IS a count and the expression IS the class literal — what changed is what `grep` reads
    that literal AS: with `-F` it is text, with `-z` the whole diff is one record.

    Long spellings are resolved by the same UNAMBIGUOUS-PREFIX rule `_resolved_long_option` uses,
    over this predicate's own name set, with `--null` declared a rival of `--null-data` — both
    measured: `--null-d` prints `0` on the probe and `--null` prints `2`. The BOUNDARY is stated
    rather than implied: the rival list reaches only as far as the one measured prefix that would
    otherwise be a false MATCH, so `--n` (a prefix of `--null`) is NOT retargeting while `--f`/`--fi`
    ARE, even though grep refuses both (measured: `option '--n' is ambiguous`, `option '--fi' is
    ambiguous`, rc 2). That over-inclusion is currently UNREACHABLE, and measurably so:
    `_long_option_is_accepted` refuses both spellings, so `_d5_command_expression` declines the
    invocation before any flag is judged (`grep -c --fi '^[+-]$'` and `grep -c --f '^[+-]$'` are
    both pinned as `[]`, not merely ungraded). ⛔ THE REASON MOVED (#13995): it used to be
    `_resolved_long_option` reading both as `--file` and eating the next word as its argument,
    which is no longer what happens (it returns `None` — the prefix is ambiguous). It is kept as
    the safe direction for a future change to `_long_option_is_accepted`, which would otherwise
    silently grade an ambiguous prefix as this class.

    ⛔ AND THE TABLE CARRIES NO MEMBER AN INVOCATION CANNOT REACH (#13995 review, P2 — MEASURED):
    `--fixed-regexp` used to be one, and it was UNWITNESSED — the acceptance gate declines every
    alias spelling before this predicate is asked, so dropping it left the suite green. Its reason
    is stated on the table's header rather than encoded as a member nothing can exercise, and
    `test_the_d5_count_reader_does_not_grade_a_command_whose_pattern_is_NOT_A_REGEX` pins the
    invariant that keeps a dead member from coming back: every name in the table must be one
    `_long_option_is_accepted` accepts.
    """
    for word in flags.split():
        if word.startswith("--"):
            name = word.split("=", 1)[0]
            if name == "--":
                continue
            if any(flag.startswith(name) and not any(
                    rival.startswith(name) for rival in _D5_RETARGETING_LONG_RIVALS)
                   for flag in _D5_RETARGETING_LONG_FLAGS):
                return True
            continue
        if word.startswith("-") and any(
                char in _D5_RETARGETING_SHORT_FLAGS for char in _short_flag_head(word)):
            return True
    return False


def _requests_a_count(flags: str) -> bool:
    r"""Does this run of grep flags ask for a COUNT?

    Word by word, because the flag run is not always ONE word: the measured spellings split it
    as `-c -E`, `-c --` and `-c --color=never`. A whole-run test (`-` then `[\w-]*c[\w-]*`
    anchored at both ends) reads the single-word forms and NOT the split one — the defect this
    function exists for, one size smaller.

    ⛔ A SHORT CLUSTER IS SCANNED LEFT TO RIGHT AND STOPS AT A FLAG THAT TAKES AN ARGUMENT
    (#13995 review round 3, P2 — MEASURED). "a short word containing `c`" was false in the
    other direction: `-ec` is `-e` with the pattern `c`, not a count, and reading it as one
    would pair the NEXT quoted token to a command that counts nothing. Once a flag that takes
    an argument is seen — the WHOLE set lives on `_ARG_TAKING_SHORT_FLAGS`, not enumerated here
    so this sentence cannot go stale the way a list does — the rest of the word is its argument
    (`-A2`), so the scan ends there. `c` before such a flag is still a count (`-cA2`, `-ce`).
    A long flag is a count only
    as the one long spelling, `--count` — which is why `--` (end of flags) and `--color=never`
    are correctly not counts.

    ⛔ BUT "HAS A `c`" IS NOT THE SAME AS "COUNTS THIS CLASS", IN TWO DIRECTIONS
    (#13995 review round 4, P2 — MEASURED on GNU grep 3.11). A flag can leave the count's SHAPE
    intact while changing WHAT is counted, or can suppress the count outright:

      * `-v` INVERTS the match set, so `grep -vc '^[+-]$'` counts the NON-blank lines. That IS a
        count and is answered `True` here — the inversion travels with the flags to
        `_d5_blank_class_prescriptions`, which refuses to grade it against the positive class
        (`grep -vc '^[+-]$'` counts a DIFFERENT class, not a second prescription of this one).
      * `-l`, `-L` and `-q` WIN over `-c`: `-lc` prints the FILENAME, `-Lc` prints the files
        WITHOUT a match, `-qc` prints nothing. None of them counts anything, so a word carrying
        one — the whole set lives on `_D5_COUNT_SUPPRESSING_SHORT_FLAGS` — is answered `False`
        however its cluster is ordered (`-cl` is the same command as `-lc`).
    """
    words = flags.split()
    # The suppressor check runs over the WHOLE run, not word by word in order: `--count -q` is
    # `-q` winning over a long-spelled count, so an early `return True` at `--count` would read a
    # command that prints nothing as a count (#13995 review round 4, P2).
    if any(_suppresses_the_count(word) for word in words):
        return False
    for word in words:
        if _long_flag_matches(word, "--count", rivals=("--color", "--context")):
            return True
        if word.startswith("--") or not word.startswith("-"):
            continue
        if "c" in _short_flag_head(word):
            return True
    return False


def _d5_count_commands(bundle: str) -> list[tuple[str, str]]:
    """`(flags, expression)` for every prescription `_d5_command_expression` reads, once
    `_requests_a_count` has admitted the flags.

    Split out so the two consumers below cannot disagree about what was seen: the FLAGS have to
    travel with the expression, because a count under `-v` is a count of a different class and is
    not decidable from the expression alone (#13995 review round 4, P2). ONE INVOCATION MAY
    CONTRIBUTE SEVERAL ENTRIES — a command with two `-e` patterns counts the union of them, and
    each pattern is a prescription in its own right (#13995, MEASURED).
    """
    commands: list[tuple[str, str]] = []
    for match in _D5_GREP_INVOCATION_RE.finditer(bundle):
        found = _d5_command_expression(bundle, match.end())
        if found is None:
            continue
        commands.extend(pair for pair in found if _requests_a_count(pair[0]))
    return commands


def _d5_split_top_level(body: str, sep: str) -> list[str] | None:
    """Split `body` on `sep` where it is NOT inside a `[...]` class or a `(...)` group.

    `None` when a group or a class is left open — an unbalanced body is not a spelling this
    reader may interpret.
    """
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_class = False
    escaped = False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        elif char == sep and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    if depth or in_class or escaped:
        return None
    parts.append("".join(current))
    return parts


def _d5_class_members(char_class: str) -> set[str] | None:
    r"""The single characters a `[...]` class matches, or `None` if it is not a plain set of them.

    A negated class, a RANGE (`[a-z]`, and `[+-z]` where the hyphen sits between two members) and
    an incomplete escape are all answered `None` rather than interpreted.

    ⛔ `[\-+]` IS REFUSED AS A DESCENDING RANGE, AND THAT IS NOT THE CLAIM "backslash is not an
    escape here" (#13995 review round 12, P2; stated precisely by #14026 review, P2 — MEASURED).
    `[\-+]` is the range `\`–`+` (`grep: Invalid range end`, rc 2), and this helper used to read it
    as the members `{-, +}` by consuming `\-` as an escaped hyphen.

    ⛔ BUT THE ESCAPE MACHINERY SURVIVES, AND POSIX HAS NO SUCH THING — so this helper really does
    over-accept, deliberately, with the cost stated (#14026 review, P2 — MEASURED). A backslash
    inside a bracket is an ORDINARY MEMBER: `^[+\-]$` is the class `{+,-,\}` and counts `3` on a
    probe carrying a lone `\` line, where this class counts `2` — on a `\`-free probe that
    carries both signs they agree, not only on the 4-line one. So the escape model, answering
    `{+,-}`, grades a SUPERSET as the class. That over-acceptance is the DECLARED superset at
    `_d5_counts_the_blank_class` and pinned as ACCEPTED in the spellings cell, and its cost is one
    impossible diff line (no line of a unified diff is a bare `\`: every diff BODY line carries a
    `+`/`-`/space prefix, and the header lines — `diff --git`, `index`, `---`/`+++`, `@@` — and
    the `\ No newline at end of file` marker are all multi-character). Removing the machinery would
    be stricter and would reverse a landed decision, so it is a reopen rather than a tidy-up —
    which is why the case for keeping it rests on what the CODE does and on that decision, rather
    than on POSIX's reading.
    """
    content = char_class[1:-1]
    if not content or content[0] == "^":
        return None
    members: set[str] = set()
    escaped = False
    for index, char in enumerate(content):
        if escaped:
            members.add(char)
            escaped = False
            continue
        if char == "\\":
            # ⛔ POSIX BRACKET EXPRESSIONS HAVE NO BACKSLASH ESCAPES, AND GNU grep ERRORS HERE
            # (#13995 review round 12, P2 — MEASURED). `[\-+]` reads as the DESCENDING RANGE
            # `\`–`+` — `grep -cE '^[\-+]$'` → `grep: Invalid range end`, rc 2, in BRE and ERE —
            # so consuming `\-` as an escaped member (which this helper did) graded a command that
            # cannot run as a BLANK-class prescription. The rule, exactly as the escape branch
            # below, the `if escaped:` consumer at the top of the loop and the two `return None`
            # branches implement it (#14026 review, P2 — MEASURED: this comment used to merge three
            # different spellings into one sentence, and then two of them into a second one):
            #   * an escaped `-` with any character after it is refused as a RANGE. grep agrees
            #     where that range DESCENDS (`grep -cE` refuses `^[\-+]$` and `^[+\-+]$` with
            #     `Invalid range end`, rc 2) — and DISAGREES for a range whose endpoints are
            #     EQUAL or that ASCENDS, both of which grep RUNS: `^[+\-\]$` is `{+}` plus the
            #     range `\`..`\`, and `^[+\-z]$` is `{+}` plus `\`..`z`; each exits 0 and counts
            #     the `+` and the `\` lines of the 6-line probe (`2`, measured). This helper
            #     answers `None` to all of them, an over-refusal in the safe direction rather
            #     than agreement with grep;
            #   * an escaped `-` in the LAST position is consumed as the member `-`, which is
            #     what keeps `[+\-]` accepted — it also matches a lone `\`, so it counts `3` on
            #     a probe carrying one where this class counts `2`;
            #   * a backslash before any OTHER character is consumed as that character, which is
            #     what keeps `[-\+]` accepted; there the ESCAPED character is `+` and the leading
            #     hyphen is unescaped, so it reaches the same backslash-bearing superset (`3`);
            #   * the UNESCAPED `[-+]` is the plain class, no `\` member at all (counts `2` on
            #     that probe), and it is kept as the class itself — not as a superset.
            # What the superset costs is stated in the docstring above: one impossible diff line.
            if index + 2 < len(content) and content[index + 1] == "-":
                return None  # a `-` between members is a RANGE, escaped or not
            escaped = True
            continue
        if char == "-" and 0 < index < len(content) - 1:
            return None  # an unescaped `-` between members is a RANGE, not a member
        members.add(char)
    return None if escaped else members


def _d5_counts_the_blank_class(expr: str) -> bool:
    r"""Does `expr` count exactly the lines that are one `+` or one `-`?

    ⛔ A BOUNDED EQUIVALENCE, NOT A REGEX-EQUIVALENCE DECISION (#13995 review, P2 — MEASURED).
    The test this replaces was a SPELLING LIST, `\^(?:\\[+-]|\[[-+]+\])\$`, so `^(\+|-)$` — the
    same class as the row's own `^[+-]$`, measured `2` on the 4-line probe — was read as an
    expression of some OTHER class. Two consequences, both measured: a second blank-class
    prescription spelled `^(\+|-)$` left the arm GREEN (a false PASS), and putting one in place
    of the row's own expression tripped the ZERO assertion, whose message blames the count READER
    for a spelling the CLASS TEST was the one to reject (a false RED with a misattributed cause).

    ⛔ AND THIS SENTENCE USED TO NAME A SECOND SPELLING BESIDE IT, ON A PREMISE THAT IS FALSE
    (#14026 review, P2 — MEASURED). It read that `^[+\-]$` and `^(\+|-)$` were "the same class,
    measured `2` on the 4-line probe" and were read as expressions of some OTHER class. Only
    `^(\+|-)$` is this class; `^[+\-]$` is NOT — a POSIX bracket has no escapes, so its members
    are `{+,-,\}` and it counts `3` on a probe carrying a lone `\` line, which is the DECLARED
    SUPERSET documented in the NEXT paragraph. On a `\`-free probe that carries both signs they
    agree, not only on the 4-line one. So the retired list REFUSING `^[+\-]$` was reading it as
    what it is — NOT this class — rather than mis-reading it; that `main` now accepts the spelling
    as a DECLARED
    SUPERSET is a deliberate widening (stated below), not a claim that it IS the class. The
    sentence therefore used a true statement about one spelling to carry a false one about the
    other — contradicting this docstring's own next paragraph, in the paragraph that justifies the
    widening.

    Accepted here, each measured — SPELLINGS CHECKED, NOT A GRAMMAR: `^[+-]$`, `^[-+]$`,
    `^[+\-]$`, and the alternation forms `^(\+|-)$` / `^(-|\+)$` (PLAIN groups, each counting `2`
    on the `x`/`+`/`-`/empty probe). ⛔ A BACKSLASH-BEARING BRACKET BUILT FROM THAT TABLE IS A
    DECLARED SUPERSET, not an equivalence (#13995 review rounds 13 and 16, P3, MEASURED): a POSIX
    bracket has no escapes, so the `\` is a MEMBER, and `^[+\-]$` counts `3` on a probe that
    carries a `\` line where the class counts 2. Accepted because it is a spelling a document
    plausibly writes, with the cost (one impossible diff line) stated rather than implied. Two
    backslashes can still pass (`^[\+\-]$` counts `3` — the same superset, not a separate case).
    The REAL rule, stated exactly as the code implements it (#13995 review rounds 17-20, MEASURED):
    a bracket is accepted IFF `_d5_class_members` returns exactly `{"+", "-"}`. That helper answers
    `None` — a refusal — for a NEGATED class, for a RANGE (an unescaped `-` with members on both
    sides, so `^[\-+]$` is refused, and grep refuses it too: `Invalid range end`, rc 2), for an
    escaped `-` in that same between-members position, and for an INCOMPLETE ESCAPE (a `\` that
    ends the content, which `_d5_class_members("[+\\]") is None` states directly — note that
    `[+-\\]` is refused by the RANGE rule before the trailing `\` is even reached). An escaped `\`
    is a MEMBER, which is why `^[\\+-]$` is refused as well: its set is `{+,-,\}`, not `{+,-}`.
    ⛔ AND `^[-+\]$` IS REFUSED BY TWO STAGES, NOT ONE (#13995 review round 21, P2 — MEASURED on
    GNU grep 3.11, which runs it and counts `3`): its class is never CLOSED, so
    `_d5_split_top_level` answers `None` before the helper is called, and the helper would answer
    `None` for the trailing `\` on its own. A pin on the expression therefore witnesses the
    COMPOSITION, which is why the helper has its own row below.
    Those refusals are the safe direction, and the only guarantee offered is one-sided and
    measured: an ACCEPTED spelling counts `2` or `3` (3 when the bracket carries a literal `\`),
    never more — which is what makes the superset cost one impossible diff line. Three spellings are NOT among them, all measured: `^[\-+]$` (the
    DESCENDING range `\`–`+`: `grep: Invalid range end`, rc 2, and this test now refuses it too),
    `^(?:\+|-)$` (POSIX ERE has no non-capturing group: `warning: ? at start of expression`,
    counts `1` under `-E` and `0` under BRE) and `^+$`/`^-$` (one sign, half the class). What it
    does NOT decide is stated at
    the assertion that uses it: a regex equivalent to this class but spelled differently
    (`^[+]$|^[-]$`, a lookahead, a POSIX class) is not recognised — a declared residual, not a
    claim of closure. ⛔ A LONE SIGN IS NOT THIS CLASS, AND IS REJECTED (#13995 review round 10,
    P2 — MEASURED): this test used to accept `^-$` and `^+$` as "one `+` or one `-`", which is
    false in both directions — `^-$` counts ONLY the removed half (measured `0` on a diff whose
    only blank line is added), and `^+$` names ONE shape, but WHICH shape depends on the dialect
    (#13995 review round 13, P3, MEASURED, correcting the sentence round 11 wrote): under `-E`,
    the dialect this arm grades, it warns `+ at start of expression` and matches the EMPTY line
    (`grep -nE '^+$'` -> the blank line, count 1); under BRE it matches a lone `+` line instead.
    Either way one shape, never the class. Accepting them let a row re-spell its way out of
    half the class
    while this arm still called it graded; arm (5) of this cell caught it, which is why this is a
    second guard's weakness rather than an open hole — and why it is closed here rather than left
    to that one. The same reasoning retires `^(?:\+|-)$`: POSIX ERE has no non-capturing group, so
    GNU grep warns `? at start of expression` and counts ONE sign (`1`, where the class counts 2).
    """
    if not (expr.startswith("^") and expr.endswith("$")):
        return False
    body = expr[1:-1]
    alternatives = _d5_split_top_level(body, "|")
    if alternatives is None:
        return False
    grouped = False
    if len(alternatives) == 1 and body.startswith("(") and body.endswith(")"):
        inner = body[1:-1]
        # ⛔ NO `?:` STRIPPING: POSIX ERE has no non-capturing group, so `^(?:\+|-)$` is not the
        # two-sign alternation it looks like — GNU grep warns `? at start of expression` and
        # counts ONE sign (measured 1 on `x`/`+`/`-`/empty; the class counts 2). Left unstripped,
        # the body is `?:\+|-` and the sign loop refuses it below.
        nested = _d5_split_top_level(inner, "|")
        alternatives = nested if nested is not None else alternatives
        grouped = True
    if len(alternatives) == 1:
        alternative = alternatives[0]
        if alternative.startswith("[") and alternative.endswith("]"):
            return _d5_class_members(alternative) == {"+", "-"}
        # A single anchor with no bracket and no two-branch alternation names ONE line shape —
        # `^-$`, `^\+$`, `^(\-)$` — never both halves of this class.
        return False
    # AN UNGROUPED ALTERNATION IS NOT A CLASS (#13995 review round 14, P2, MEASURED). ERE binds
    # the anchors to each BRANCH, so `^\+|-$` is `(^\+)|(-$)`: starts with `+` OR ends with `-`.
    # Measured on `x`/`+`/`-`/empty/`++`/`+-`/`-+`/`--`/`a`/`\`/`1`/`~` it matches FIVE lines
    # (`+ - ++ +- --`) where the class has two — and 22 two- and three-branch spellings WERE
    # accepted here before this guard (4 at k=2, 18 at k=3; the count grows with the branch count:
    # 64 at k=4, 210 at k=5), none of them the class, while the retired spelling-list regex refused
    # all of them — so this was a widening onto a WRONG class, not a residual. A multi-branch body
    # must therefore be a GROUP; only the `( ... )` branch above may set `grouped`.
    if len(alternatives) > 1 and not grouped:
        return False
    # A BARE `+` IS NOT A BRANCH OF THIS CLASS (#13995 review round 13, P2, MEASURED). The
    # blanket `alternative.replace("\\", "")` read `^(+|-)$` as the two signs, but under `-E`
    # (the dialect this arm grades) that expression WARNS `+ at start of expression` and matches
    # the `-` line AND the EMPTY line: 2 on the probe, never a lone `+`, so it is half the class
    # plus a shape the class excludes; `^(-|+)$` does not even run (`Unmatched ( or \(`, rc 2).
    # Accept the escaped forms and a leading bare `-` (grep reads that literally: `^(-|\+)$` -> 2),
    # refuse everything else.
    signs = set()
    for alternative in alternatives:
        if alternative == "\\+":
            sign = "+"
        elif alternative in ("-", "\\-"):
            sign = "-"
        else:
            return False
        signs.add(sign)
    return signs == {"+", "-"}


def _d5_blank_class_prescriptions(bundle: str) -> list[str]:
    """The expressions of the count commands that count THIS class — positive match set only.

    A `-v`-bearing count is deliberately EXCLUDED even when its expression is the BLANK class: it
    counts the complement, i.e. a different class, so it is not a second prescription of this
    one. Grading it would red a correct document that prescribes the blank count and its
    complement together — with "so this arm would execute one of them while a reader follows
    another", a sentence that is false in that case — and a false RED on a correct document is
    the failure mode this file treats as the worse one (#13995 review round 4, P2 — MEASURED).

    ⛔ THE EXCLUSION HAS A RESIDUAL AND IT IS STATED, NOT IMPLIED: because the test is on the
    FLAGS, an inverted command whose expression is NOT the blank-class literal is unread even when
    it would count the blank lines (`grep -vc '^[+-][^+-]'`). That is a shape this arm does not
    read — pinned as `[]` in
    `test_the_d5_count_reader_does_not_grade_a_complement_or_suppressed_count_as_this_class` —
    never a claim that the class is prescribed once in the document. A guard may be narrow, but may
    not describe itself as complete (this file's own rule).

    ⛔ AND THE SAME FIX ONE AXIS OVER: a command whose FLAGS make the expression select a different
    set is not graded either (#13995 review — MEASURED). `grep -Fc '^[+-]$'` counts the LITERAL text
    (`0` on the 4-line probe) and `grep -zc '^[+-]$'` counts nothing (`0`), while
    `-Ec`/`-ic`/`-wc`/`-xc` print `2` — so `_retargets_the_matched_text` is the second reason this
    filter reads the FLAGS and not the expression alone, and why the expression-only test cannot
    answer this question. Its residual is stated at that helper: another flag that changes what the
    pattern selects, and is not in its table, is graded as this class until it is added and pinned —
    ⛔ AND ONE SUCH FLAG IS KNOWN AND DECLARED THERE RATHER THAN LEFT IMPLIED: `-m`/`--max-count`
    changes the SHAPE OF THE COUNT (measured `1` where the class has `2`) without changing what the
    pattern selects, so a capped command is graded with a number below the class; `egrep -c`, which
    really counts this class (`2` measured), is blind for the same reason (#14027 round 4, P3).
    """
    return [
        expr for flags, expr in _d5_count_commands(bundle)
        if not _inverts_the_match_set(flags)
        and not _retargets_the_matched_text(flags)
        and _d5_counts_the_blank_class(expr)
    ]


def _d5_count_prescriptions(bundle: str) -> list[str]:
    r"""The EXPRESSION of every `grep` count command in `bundle` whose `-`-anchored flag words
    are followed by a quoted expression.

    ⛔ THAT IS A SHAPE, NOT A CLAIM ABOUT SPELLING, and the sentence it replaces claimed the other
    thing (banned by `test_the_d5_count_reader_does_not_describe_itself_as_complete` —
    #13995 review, P2). The count commands this does NOT read are named in
    `_D5_GREP_INVOCATION_RE`'s residual block above — an unquoted first operand; flags written
    after the expression; a
    pattern supplied from a FILE (`-f`/`--file`); another tool; a concatenation past
    its first fragment — and EACH OF THOSE IS PINNED in
    `test_the_d5_count_reader_sees_one_command_however_it_is_spelled`, as `[]` for all but the
    concatenation, which is pinned at its first fragment. So "every" here is bounded by that
    list, and that list is a list rather than the closure.

    ⛔ THE `-e`/`--regexp` HALF OF THAT BULLET IS GONE, NOT GLOSSED (#14027 review round 5, P3 —
    MEASURED). This paragraph said "a pattern supplied on a flag (`-e`/`-f`/`--regexp`/`--file`)"
    and that every item is pinned `[]`; at this head `-e`/`--regexp` are READ, and the very test it
    names pins `grep -c -e 'decoy' -e '^[+-]$'` as `["decoy", "^[+-]$"]` — the bullet contradicted
    the pin it cited. Only the FILE-supplying half is a residual: `-f`/`--file` name a file whose
    contents the document does not carry.

    The count flag is what makes a `grep` a prescription to be counted, so the filter is
    `_requests_a_count`, never a spelling of the flag run. Measured control: each of #13962's
    SEVEN mutants returns exactly one expression here and exactly one BLANK-class expression from
    `_d5_blank_class_prescriptions`, and the two filter stages in the same cell (`grep -E`,
    `grep -vE`) return none.

    ⛔ THIS IS THE SEEN SET, NOT THE GRADED SET, and that is deliberate: a `-v` count is seen here
    (`grep -vc '^[+-]$'` IS a count command) and excluded by `_d5_blank_class_prescriptions`,
    because it counts the complement rather than this class. Keeping it visible is what lets the
    exclusion be falsifiable — the cell pins both halves (#13995 review round 4, P2).
    """
    return [expr for _flags, expr in _d5_count_commands(bundle)]


def _d5_blank_count_expression() -> str:
    """The blank-line count expression as `05-verify.md` states it.

    Loud on zero or many — and MANY is checked TWICE, because "a second command" has two
    spellings that fail differently: a second copy of the SENTENCE arm (5) grades (the
    anchored pattern), and a second BLANK-CLASS PRESCRIPTION worded or prefixed differently
    (the expression-shaped count). Zero would make every assertion below vacuous — an inert
    arm of exactly the kind (3) refuses for the ledger rows — and MANY would silently grade
    the first of two prescriptions, which is how a cell ends up asserting one command while
    its reader follows another.
    """
    bundle = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
              / "05-verify.md").read_text(encoding="utf-8")
    prescriptions = _d5_count_prescriptions(bundle)
    # ⛔ NOT `[e for e in prescriptions if fullmatch(e)]`: the EXPRESSION alone does not say what
    # was counted. `grep -vc '^[+-]$'` is a count of the COMPLEMENT, so it must not be graded
    # against the positive class — the filter owns the flags too (#13995 review round 4, P2).
    blank_prescriptions = _d5_blank_class_prescriptions(bundle)
    # ⛔ ZERO AND MANY ARE TWO DIFFERENT DEFECTS AND GET TWO DIFFERENT MESSAGES (#13962). The
    # single `== 1` this replaces rendered the ZERO case as "prescribes 0 BLANK-line count
    # commands ([] of […]), so this arm would execute one of them while a reader follows
    # another" — a sentence about picking between two prescriptions, printed over an EMPTY list,
    # on the head where the sentence's expression had merely been re-spelled (`^-$`). A fixer
    # reading it is told to look for a second command that does not exist.
    assert blank_prescriptions, (
        "this arm SEES no BLANK-line count command at this head "
        f"(count commands read: {prescriptions!r}), so it executes nothing — and FIVE different "
        "stages can be the reason, so check all five before editing anything: the `05-verify.md` "
        "row may still PRESCRIBE one in a spelling the count reader cannot see (an unquoted "
        "expression; a pattern supplied from a FILE, `-f`/`--file`) — ⛔ NOT `-e`/`--regexp`, which "
        "this reader DOES read and grade (#14027 round 6, P3 — MEASURED: "
        "`_d5_blank_class_prescriptions('grep -c -e \'^[+-]$\'')` is `['^[+-]$']`); or the reader "
        "DID read it and "
        "`_d5_counts_the_blank_class` rejected the spelling — measured on a row spelled "
        "`^(\\+|-)$`, which counts the same class and is graded as this one now — which is a "
        "class-test gap and not a reader gap; or the row prescribes the COMPLEMENT "
        "(`grep -vc '^[+-] …'`) — read, and refused BY DESIGN by `_inverts_the_match_set`; or the "
        "row asks for a count whose output `grep` SUPPRESSES OR REPLACES (`-q` prints nothing, "
        "`-l`/`-L` print filenames) — also refused by design, by `_requests_a_count`'s "
        "`_D5_COUNT_SUPPRESSING_SHORT_FLAGS` scan; or the row's command carries a flag that makes "
        "the expression select a DIFFERENT set (`-F` reads it as literal text, `-z` ends a record "
        "at a NUL) — read and refused by design by `_retargets_the_matched_text`. Only the first "
        "two are gaps; the last three are the arm working. Naming only the reader was a false "
        "attribution, and naming fewer than all five is the same defect one round later "
        "(#13962, #13995 reviews)")
    assert len(blank_prescriptions) == 1, (
        f"05-verify.md prescribes {len(blank_prescriptions)} BLANK-line count commands "
        f"({blank_prescriptions!r} of {prescriptions!r}), so this arm would execute one of "
        "them while a reader follows another")
    found = _D5_BLANK_COUNT_RE.findall(bundle)
    assert len(found) == 1, (
        "05-verify.md no longer states exactly one `git diff … | grep -cE '<expr>'` command "
        f"bound to the BLANK-line class (found {found!r}), so this arm executes nothing")
    # ⛔ THE GATE ABOVE COUNTS PRESCRIPTIONS ANYWHERE IN THE BUNDLE, IN EVERY SPELLING THE
    # READER READS (#13962 — the SEVEN mutants on the `grep -c[A-Za-z]* '…'` reader it replaced);
    # the residual block on `_D5_GREP_INVOCATION_RE` names the spellings it does NOT, so that is
    # a shape and not a promise about spelling. THIS ONE BINDS
    # EXECUTED EXPRESSION TO THE CLASS (#13956 review, P2 — MEASURED). The two are NOT
    # equivalent, and the gap is a fail-open on the exact defect this cell exists to retire: a
    # second, correctly-spelled BLANK-class prescription anywhere in the bundle keeps
    # `len(blank_prescriptions) == 1` GREEN while the SENTENCE prescribes a WIDER command, and
    # `found[0]` — the expression actually executed below — is then never checked against the
    # class at all. Measured at `280c1899c7`: widen the sentence to `^[+-]` and append a
    # `^\+$` decoy, and the 447 tests that `tests/test_docs_citation_refs.py` +
    # `tests/test_7949_capstone_measure.py` collect (24 + 423, re-measured with
    # `--collect-only -q` at that head) stayed green, while the row's own sentence
    # ("… and ONLY that class: it does not count the sign-opening one (`++i`, `--counter`,
    # `+- 2)`)") became FALSE. With this assertion the same two-edit mutant reds HERE.
    assert _d5_counts_the_blank_class(found[0]), (
        f"05-verify.md's BLANK-line sentence prescribes {found[0]!r} at this head, which is not "
        "the BLANK class the row names — so the document tells the reader to count a wider "
        "class than the one it claims to count. A decoy BLANK-class prescription elsewhere in "
        "the bundle keeps the count above green, which is why the EXECUTED expression has to "
        "be bound to the class here (#13956 review, P2). NOTE the residual: the class test is a "
        "BOUNDED equivalence (see `_d5_counts_the_blank_class`), so an equivalent spelling it "
        "does not recognise (`^[+]$|^[-]$`, a lookahead) reds this arm — a declared residual, "
        "not a fact about the document")
    return found[0]


def _blank_line_count(diff: str) -> int:
    """The document's blank-line count command, executed on `diff`.

    Same discipline as `_comment_filter`, and the reason this arm is not a text ban: the
    text ban for this class cannot see a VALUE change under a stable spelling — the rot this
    cell records for constants ("a symbol can keep its NAME and change its VALUE"). Running
    the expression the sentence carries is what reds when the instrument narrows while the
    wording stays.
    """
    out = subprocess.run(
        ["grep", "-cE", _d5_blank_count_expression()], input=diff,
        capture_output=True, text=True, check=False)
    assert out.returncode in (0, 1), (
        f"grep refused the document's own expression: {out.stderr!r}")
    return int(out.stdout.strip() or 0)


def test_the_d5_count_reader_sees_one_command_however_it_is_spelled():
    """#13962 — the count arm must read a COMMAND, not one SPELLING of one.

    MEASURED on the pre-fix reader, which was `re.compile(r"grep -c[A-Za-z]* '([^']+)'")`
    (if you are tempted to narrow it back, run this test first): each of the SEVEN MUTANTS in
    the loop below was appended to `05-verify.md` on its own and the cell that calls
    `_d5_blank_count_expression()` was run — `1 passed` every time. Each mutant is a SECOND
    prescription for the same BLANK class, and the arm whose entire job is to refuse a second
    prescription reported the class as graded once, so the row told its reader to run one
    command while it carried two. (The loop's LAST entry is not a mutant: it is the spelling
    that already worked before the fix, kept as the floor so a future narrowing cannot pass by
    breaking the case that was never broken.)

    ⛔ THE LAST THREE MUTANTS WERE MISSED BY THE FIRST CUT OF THIS FIX AND FOUND BY REVIEW
    (#13995 review, P1 — MEASURED, and the reader was widened in the same commit that added
    them here): its flag word required a LETTER after the dashes, so a bare `--` and a
    `--flag=value` were unreadable, and its quote could not express `$'…'`. All three are
    POSIX-idiomatic spellings of the row's own command, i.e. the shape a future hardening pass
    would actually write — which is why they are pinned rather than just described.

    The control is the mutation itself, not a list of regexes: what is asserted is the outcome
    the arm needs — one BLANK-class expression seen per spelling, and none from the two
    non-count `grep` stages the same cell prescribes.

    ⛔ THE EXPRESSION IN THESE FIXTURES IS `^[+-]$`, THE CLASS THE ROW ACTUALLY CARRIES — NOT A
    SINGLE SIGN BETWEEN ANCHORS (#13995 review round 10, P2 — MEASURED). One sign is HALF the
    class (`^-$` counts only removed lines), and a bare `+` between anchors is not a regex grep
    accepts at all. The class test now says so, so a fixture spelled that way would assert "this
    is a BLANK-class count" over an expression that is not one; these fixtures are about the
    FLAG/spelling axis, so they carry an expression that really is the class.
    """
    for spelling in (
        "grep -Ec '^[+-]$'",           # flag BEFORE the `c`
        'grep -cE "^[+-]$"',           # double-quoted expression
        "grep -c -E '^[+-]$'",         # flags split across two words
        "grep --count '^[+-]$'",       # the long form
        "grep -c -- '^[+-]$'",         # POSIX end-of-flags `--`
        "grep -c --color=never '^[+-]$'",  # a long flag WITH a value
        "grep -c $'^[+-]$'",           # ANSI-C quoting
        "grep -c '^[+-]$'",            # the spelling that already worked, kept as the floor
    ):
        seen = _d5_count_prescriptions(f"a prescription: `{spelling}`\n")
        assert len(seen) == 1 and _d5_counts_the_blank_class(seen[0]), (
            f"{spelling!r} is ONE BLANK-class count command and the reader must see exactly "
            f"one BLANK-class expression for it — #13962's mutants each stayed green while "
            f"the bundle carried two prescriptions. Seen: {seen!r}")

    # …and the reader is a COUNT reader, not a `grep` reader: the two filter stages this same
    # cell prescribes are not prescriptions to be counted, and admitting them would red a
    # correct document. `grep -cE '^\+import '` is the other direction the reader is
    # deliberately NOT keyed on a command prefix to protect — it IS a count, it is just not
    # this class, so it is seen and then excluded by the class filter.
    assert _d5_count_prescriptions(
        "`grep -E '^[+-][^+-]'` and `grep -vE '^[+-][[:space:]]*#'`") == []
    assert _d5_count_prescriptions("`grep -cE '^\\+import '") == ["^\\+import "]
    # `-C3` is CONTEXT, not count — a short flag containing no `c` — and it must not be read as
    # a prescription at all (#13995 review measured it too, on the pre-widening regex).
    assert _d5_count_prescriptions("`grep -C3 '^\\+$'`") == []
    # ⛔ …AND A COUNT WHOSE OUTPUT IS SUPPRESSED IS NOT A COUNT REQUEST EITHER (#13995 review
    # round 8, P3 — MEASURED at this head). `-q` prints nothing, `-l`/`-L` print filenames, so
    # `_requests_a_count` declines the whole word BY DESIGN — which makes this one of the causes a
    # ZERO-graded class can have, and one the ZERO message names so a fixer does not go
    # widening the reader for a spelling that is correctly refused.
    for suppressing in ("grep -qc '^\\+$'", "grep -lc '^\\+$'", "grep -Lc '^\\+$'"):
        assert _d5_count_prescriptions(f"`{suppressing}`") == [], (
            f"{suppressing!r} asks for a count whose output `grep` suppresses or replaces, so "
            f"it must NOT be read as a count request (by design — the ZERO message names it)")
    # …and the residual is pinned as a residual, so the comment's "does not read" list cannot
    # silently become false in the other direction: an expression in a shell variable IS read
    # (the command is seen) and is NOT classifiable, which is the sentence the comment now
    # makes. `rg -c` is not read at all.
    assert _d5_count_prescriptions('`grep -cE "$RE"`') == ["$RE"]
    assert _d5_count_prescriptions("`rg -c '^\\+$'`") == []

    # ⛔ A FLAG'S OWN QUOTED ARGUMENT IS PART OF THE FLAG WORD, NOT THE EXPRESSION
    # (#13995 review round 2, P2 — MEASURED). Before this round `--include='*.py'` broke the
    # match entirely, so the expression beside it went unseen; it is read now, and the two
    # spellings below are the control.
    for with_flag_value in (
        "grep -c --include='*.py' '^\\+$'",
        "grep -c --color='never' '^\\+$'",
    ):
        seen = _d5_count_prescriptions(f"a prescription: `{with_flag_value}`\n")
        assert seen == ["^\\+$"], (
            f"{with_flag_value!r} carries its expression AFTER a flag's own quoted argument, "
            f"and the reader must still find it — a flag's value is not the pattern "
            f"(#13995 review round 2). Seen: {seen!r}")

    # ⛔ A FLAG'S SEPARATE VALUE IS CONSUMED, NOT READ AS THE EXPRESSION (#13995 review, P2 —
    # MEASURED). This is the finding the word walk replaces the raw-text regex for. On the PRE-FIX
    # reader the four spellings below gave `['*.md']`, `['2']`, `[]` and `['2']` — the flag's VALUE
    # reported as the count's expression (or, for the bare `2`, the command unread) — all four
    # measured `2` on the 4-line probe, i.e. real blank-line counts. A wrong expression is a FALSE
    # PASS, not a miss: it is neither the blank class nor a shape the residual list names, so a
    # second BLANK-class prescription appended in that spelling left the arm GREEN.
    for separate_value in (
        "grep -c --exclude '*.md' '^[+-]$'",   # a long flag's separate GLOB
        "grep -c -A '2' '^[+-]$'",            # a context flag's separate NUM, quoted
        "grep -c -m 2 '^[+-]$'",              # …same, bare
        "grep -c -m '2' '^[+-]$'",            # …same, quoted
    ):
        seen = _d5_count_prescriptions(f"a prescription: `{separate_value}`\n")
        assert seen == ["^[+-]$"], (
            f"{separate_value!r} counts the BLANK lines (measured `2` on the 4-line probe) and "
            f"its expression is the quoted token AFTER the flag's own value — reading the value "
            f"instead was a false PASS, not a miss (#13995 review, P2). Seen: {seen!r}")
    # …and the shape that must STAY declined, so consuming a flag's value cannot be widened into
    # a guess: `-f`/`--file` supply the pattern FROM A FILE, so the positional operands are FILES
    # — measured `No such file or directory` rather than a count.
    for pattern_from_a_file in (
        "grep -c -f/tmp/pats '^[+-]$'",
        "grep -c -f /tmp/pats '^[+-]$'",
        "grep -c --file /tmp/pats '^[+-]$'",
    ):
        assert _d5_count_prescriptions(f"a prescription: `{pattern_from_a_file}`\n") == [], (
            f"{pattern_from_a_file!r} takes its PATTERN from a file, so no positional word in "
            "it is an expression — reading one would report a count of a FILE operand "
            "(#13995 review, P2)")
    # ⛔ BUT AN `-e` ARGUMENT *IS* A PATTERN, AND EVERY ONE OF THEM IS A PRESCRIPTION (#13995,
    # MEASURED). This pair used to be pinned `[]` above, on the reasoning that a pattern-supplying
    # flag means "no expression here". That is true of `-f` (which supplies a FILE) and false of
    # `-e`: `grep -c -e 'decoy' -e '^[+-]$'` counts the UNION of both patterns, so the blank class
    # IS counted, and a second BLANK-class prescription written as a second `-e` left the arm GREEN
    # while this pin certified the shape as unreadable. Both patterns are reported now.
    # ⛔ `grep -c'x' '^\+$'` IS A COUNT, AND THAT CORRECTS A PIN (#13995, MEASURED). The shell
    # removes the quotes before grep runs, so the command is argv `-cx '^\+$'` = `-c -x` and it
    # really counts. It was pinned `[]` on the reasoning that "`x` is not a value" — true, and
    # beside the point: the quote makes `x` MORE FLAG CHARACTERS, not a value, so the operand IS
    # the pattern. ⛔ THE WITNESS FOR THE ALPHANUMERIC TEST IS THE ROW BELOW IT, NOT THIS ONE, AND
    # NOT `grep -c'^\+$'` (#14027 review round 7, P2 — MEASURED). This row is unchanged when that
    # test is deleted (it becomes readable for the `-cx` reason, whatever the suffix test says), and
    # `grep -c'^\+$'` has no trailing operand at all, so its `[]` has a different cause. The mutant
    # that reds is `grep -c'-' '^[+-]$'` → `['^[+-]$']`: argv `-c-` is not a flag cluster.
    assert _d5_count_prescriptions("a command: `grep -c'x' '^\\+$'`\n") == ["^\\+$"], (
        "`grep -c'x'` is argv `-cx` — `-c` plus the flag `x` — so the quoted token after it IS the "
        "pattern and the command counts (measured). Reading the quote as an attached VALUE would "
        "decline a real prescription (#13995, MEASURED)")
    assert _d5_count_prescriptions("a command: `grep -c'-' '^[+-]$'`\n") == [], (
        "a NON-alphanumeric quoted suffix is not a flag character, so `-c'-'` is argv `-c-` — which "
        "grep REFUSES (`invalid option`, rc 2) — and the quoted token after it is no expression. "
        "THIS ROW IS THE WITNESS FOR THE ALPHANUMERIC TEST ITSELF: delete the test at "
        "`_d5_flag_word`'s quoted-suffix branch and this row goes `[]` → `['^[+-]$']`, while the "
        "rows the comment used to nominate do not move at all (#14027 review round 7, P2 — "
        "MEASURED)")

    assert _d5_count_prescriptions(
        "a prescription: `grep -c -e 'decoy' -e '^[+-]$'`\n") == ["decoy", "^[+-]$"], (
        "a command with two `-e` patterns counts the union of them, so BOTH are prescriptions: "
        "reporting only the first — or declining the command — lets the second hide behind the "
        "first (#13995, MEASURED)")
    assert _d5_count_prescriptions("a prescription: `grep -c -e'^[+-]$'`\n") == ["^[+-]$"], (
        "an ATTACHED `-e` argument is the pattern itself (`-e'^[+-]$'`), so it is a prescription "
        "and MUST be graded — declining it was a false PASS (#13995, MEASURED)")
    assert _d5_count_prescriptions("a prescription: `grep -c --regexp='^[+-]$'`\n") == ["^[+-]$"], (
        "the long spelling of the same attachment takes the same route (#13995, MEASURED)")

    # ⛔ AND THE RESIDUALS THE COMMENT DECLARES ARE PINNED AS RESIDUALS (#13995 review round 2,
    # P2 — MEASURED; split into the comment's two groups in round 3, and SHRUNK in round 6 when
    # the `-e`-supplied patterns became readable — the bullet went with the pin, in the same
    # commit, rather than staying beside code that contradicts it). GROUP A is real BLANK-class
    # prescriptions the reader does not see, so a second one appended to the row leaves the arm
    # green; reading these two cannot be done without parsing a shell, and a guess there reports
    # a false RED on a correct document — the worse direction. Pinning `[]` is what makes the
    # comment's list falsifiable: a future widening that starts reading one of them reds HERE.
    for residual in (
        "grep -cE ^\\+$",                 # unquoted expression
        "grep '^\\+$' -c",                # flags AFTER the expression
    ):
        assert _d5_count_prescriptions(f"a prescription: `{residual}`\n") == [], (
            f"{residual!r} is now READ by the word walk. That may be an improvement — "
            "but the comment on `_D5_GREP_INVOCATION_RE` lists it as a shape the reader does not "
            "read, so delete the bullet (or narrow the reader again) in the same commit instead "
            "of leaving a false statement beside the code (#13995 review round 2, P2)")

    # ⛔ GROUP B is NOT a count prescription at all — and the pins say so with a MEASUREMENT, not
    # with a label, because round 2's comment called `grep -c'^\+$'` "the same, with no
    # separating space" and that is false: `-c` takes no argument, so `grep -c'^[+-]$'` exits 2
    # with `grep: invalid option` and counts nothing (#13995 review round 3, P2). They are pinned
    # `[]` so a future widening cannot start certifying an invalid command as a prescription.
    for not_a_prescription in (
        "grep -c'^\\+$'",                # `-c` takes no argument -> invalid option, exit 2
        "grep -c, '^\\+$'",              # …same, the `,` is parsed as another option
        "grep -c -f /tmp/pats '^\\+$'",   # `-f` supplies the PATTERN; the quoted token is a FILE
        "grep -ec '^\\+$'",              # `-e` takes `c` as ITS pattern; this is not a count
        "grep -e'c' '^\\+$'",            # …same, attached
    ):
        assert _d5_count_prescriptions(f"a command: `{not_a_prescription}`\n") == [], (
            f"{not_a_prescription!r} is not a count of anything and must not be read as a "
            "prescription — widen this and a correct document can red (#13995 review round 3)")

    # ⛔ AND THE ARG-TAKING SET IS A SET, NOT A SAMPLE — EVERY MEMBER HAS ITS OWN ROW
    # (#13995 review, P2 — MEASURED). `_ARG_TAKING_SHORT_FLAGS` narrates a repair that a round-3
    # verification measured (`-D` added, because `_requests_a_count("-Dc")` answered True) and
    # says its list is "the whole set, not a sample". Measured at `53f7535e657` by deleting one
    # letter at a time and re-running THIS cell: only `m` and `A` were pinned (by the
    # separate-value rows further down), while `B`, `C`, `d` and `D` left the cell GREEN with
    # `grep -Dc '^\+$'` graded a BLANK-class prescription again — the exact round-3 defect, a
    # false RED on a correct document; and `e`/`f` left it GREEN because the command is never
    # graded either way (`_d5_supplies_the_pattern` declines it on a literal `ef` before the set
    # is consulted), so their membership had no witness at all. Real grep counts none of the
    # eight clusters (measured on GNU grep 3.11, on the 4-line `+`/`-`/`+import os`/` a` probe):
    # `-Dc` ⇒ `grep: unknown devices method`, rc 2; `-mc` ⇒ `invalid max count`, rc 2;
    # `-Ac`/`-Bc`/`-Cc` ⇒ `invalid context length argument`, rc 2; `-dc` ⇒ `invalid argument 'c'
    # for '--directories'`, rc 1; `-fc` ⇒ `c: No such file or directory` (that is `-f c`, a
    # pattern FILE), rc 2; `-ec` ⇒ the pattern `c`, with `'^\+$'` as a FILE, rc 2.
    #
    # ⛔ `pinned_members` IS WRITTEN OUT, NOT DERIVED FROM THE CONSTANT, ON PURPOSE. A table that
    # iterates its own subject cannot witness an OMISSION — the direction this row exists for
    # (agent-infra#3442 rule 3: an assertion whose value cannot vary under the mutation it claims
    # to guard). The set-equality row below then makes an ADDITION equally impossible without a
    # witness, which is the other half of the same claim: a letter that does NOT take an argument
    # would stop the cluster scan early and hide a real `c` after it (`grep -xc` is `-x -c`, a
    # count), so it may not arrive silently either.
    pinned_members = ("e", "f", "m", "A", "B", "C", "d", "D")
    for arg_taking in pinned_members:
        assert _short_flag_head(f"-{arg_taking}c") == "", (
            f"`-{arg_taking}c` must END the cluster scan at `{arg_taking}` — the rest of that "
            "word is its ARGUMENT, not a run of flags (`-A2`, `-fc`, `-Dc`) — so "
            f"`_ARG_TAKING_SHORT_FLAGS` must still carry `{arg_taking}`. Drop that letter and "
            "this word reads as a count request again, which is the round-3 defect (#13995 "
            "review, P2)")
        assert _d5_count_prescriptions(f"a command: `grep -{arg_taking}c '^\\+$'`\n") == [], (
            f"`-{arg_taking}c` is `-{arg_taking} c` — a VALUE, not the count flag — so this "
            "command counts nothing (measured on GNU grep 3.11: it exits 1/2 rejecting the "
            "argument). Reading it as a BLANK-class prescription is a false RED on a correct "
            "document, and no row pinned that before #13995's review (#13995 review, P2)")
    assert set(_ARG_TAKING_SHORT_FLAGS) == set(pinned_members), (
        "`_ARG_TAKING_SHORT_FLAGS` must be exactly the short flags MEASURED to take an "
        f"argument ({sorted(pinned_members)}). A letter added without its own row above stops "
        "the cluster scan early, so a real `c` after it goes unread (a false PASS); a letter "
        "removed puts a flag's VALUE back into the flag run (a false RED). Take the "
        "measurement against `grep --help`, then extend `pinned_members` in the same commit "
        "(#13995 review, P2)")

    # ⛔ AND PROSE THAT MERELY NAMES THE COUNT COMMAND IS NOT A PRESCRIPTION — the regression
    # round 3 caught, measured against the reader it replaced. While the flag word was a bag of
    # characters (`[^\s'\"]*`) it swallowed the CLOSING BACKTICK, `,`, `)` and `;`, so the quote
    # ADJACENCY rule was defeated and a sentence saying what the row does NOT mean was read as a
    # second BLANK-class prescription — a FALSE RED on a correct document (`prescribes 2
    # BLANK-line count commands`), where `grep -c[A-Za-z]* '…'` returned `[]`. These five shapes
    # are that regression's control.
    for prose in (
        "`grep -c`, '^\\+$'",
        "(`grep -c`) '^\\+$'",
        "`grep -c`; '^\\+$'",
        "grep -c, '^\\+$'",
        '`grep -c`, "^\\+$"',
    ):
        assert _d5_count_prescriptions(f"⚠️ a count like {prose} is not what this row means.\n") == [], (
            f"{prose!r} names a command inside prose and carries no expression of its own; "
            "reading the NEXT quoted token as its pattern is a false RED on a correct document "
            "(#13995 review round 3, P2)")

    # …and CONCATENATION is the one residual that is neither read nor unread: the first quoted
    # fragment IS read, and everything after it is not — which is what makes a class constraint
    # in the tail invisible. Asserted with its fragment so the bullet's wording stays exact
    # ("read only up to its first quoted fragment") rather than collapsing into either bucket.
    assert _d5_count_prescriptions("a prescription: `grep -cE '^'\"[+-]\"'$'`\n") == ["^"], (
        "a concatenated expression must be read as its FIRST fragment only — if it is now read "
        "whole, the comment's concatenation bullet is stale; if it is not read at all, the "
        "fragment claim is stale (#13995 review rounds 1-2)")

    # ⛔ THE BLANK-CLASS TEST IS A BOUNDED EQUIVALENCE, NOT A SPELLING LIST (#13995 review, P2 —
    # MEASURED). Each spelling below counts the SAME class on the 4-line probe — `2`, which is what
    # makes them controls here — although `^[+\-]$` reaches that `2` as a DECLARED SUPERSET rather
    # than as an equivalence: it also matches a lone `\`, so on a probe carrying one it counts `3`
    # where the class counts `2` (see `_d5_counts_the_blank_class`). Stated rather than implied,
    # because "the SAME class" on its own was false for that row (#14026 review, P2 — MEASURED).
    # The old spelling list, `\^(?:\\[+-]|\[[-+]+\])\$`, carried `^[+-]$` and `^[-+]$` — the two
    # controls — and REJECTED `^(\+|-)$`, which IS this class: measured on the pre-fix reader that
    # cost in both directions, as a SECOND prescription it left the arm GREEN (false PASS) and in
    # place of the row's own expression it tripped the ZERO assertion whose message blamed the
    # count READER (false RED, misattributed). ⛔ IT ALSO REJECTED `^[+\-]$`, AND THAT ONE WAS NOT A
    # DEFECT (#14026 review, P2 — MEASURED): `^[+\-]$` is not this class (it is the declared
    # superset above), so the list's refusal was the CORRECT class verdict, and `main` accepts the
    # spelling now as a deliberate widening rather than because the list mis-read it. The two the
    # list already carried are kept as controls, so a narrowing that loses them reds here too.
    for equivalent_class in (
        "grep -cE '^[+-]$'",           # the form the row itself uses
        "grep -cE '^[+\\-]$'",         # the hyphen escaped inside the class
        "grep -cE '^[-+]$'",           # members in the other order
        "grep -cE '^(\\+|-)$'",       # alternation inside a plain group
        # ⛔ NOT `^(?:\+|-)$` (#13995 review round 11, P2 — MEASURED): POSIX ERE has no
        # non-capturing group, so GNU grep warns `? at start of expression` and the expression
        # counts ONE sign (1 on `x`/`+`/`-`/empty, where the class counts 2). It is pinned as a
        # REJECTION in the `half_class` loop below, not as an accepted spelling here.
    ):
        assert _d5_blank_class_prescriptions(
            f"a prescription: `{equivalent_class}`\n") == [
                _d5_count_prescriptions(f"a prescription: `{equivalent_class}`\n")[0]], (
            f"{equivalent_class!r} counts the BLANK lines and must be graded as this class — "
            "a spelling list read it as some OTHER class, which left a second prescription green "
            "and misattributed the ZERO message (#13995 review, P2)")
    # ⛔ THE TWO REFUSALS THE RULE NAMES ARE PINNED TOO, or the paragraph can go false beside
    # green code — which is how it survived a round (#13995 review round 20, P3 — MEASURED):
    # `^[-+\]$` ends in a `\` (incomplete escape) and `^[\\+-]$` carries a literal `\` member;
    # grep runs both and counts `3`, and this test refuses both.
    assert not _d5_counts_the_blank_class("^[-+\\]$"), (
        "a bracket whose content ENDS in a backslash is an incomplete escape: refused, although "
        "grep runs it and counts 3 (measured)")
    assert not _d5_counts_the_blank_class("^[\\\\+-]$"), (
        "an escaped backslash is a MEMBER, so this bracket's set is `{+,-,\\}`, not `{+,-}`: "
        "refused, although grep runs it and counts 3 (measured)")
    # …and the HELPER's own verdicts, because a pinned EXPRESSION witnesses the whole composition:
    # `^[-+\]$` never reaches the helper (its class is unclosed, so the splitter refuses it first),
    # so the trailing-backslash rule needs a row that calls the helper directly
    # (#13995 review round 21, P2 — MEASURED: with `return None if escaped else members` changed to
    # `return members`, every d5 test stayed GREEN because only the expression was pinned).
    assert _d5_class_members("[+\\]") is None, (
        "a bracket whose content ends in a backslash is an INCOMPLETE ESCAPE and the helper must "
        "answer None on its own: `[+\\]` has no `-` for the RANGE rule to refuse, so dropping "
        "`return None if escaped else members` is visible HERE and nowhere else — `^[-+\\]$` never "
        "reaches the helper at all (the splitter refuses its unclosed class first), which is why "
        "an expression-level pin cannot witness this rule (#13995 review round 21, P2)")
    assert _d5_class_members("[+\\-]") == {"+", "-"}, (
        "and the ordinary escaped-hyphen spelling is a plain set: `[+\\-]` is `{+,-}`")
    assert _d5_class_members("[-\\+]") == {"+", "-"}, (
        "and the spelling whose ESCAPED character is the `+` is the same plain set: `[-\\+]` "
        "consumes an escaped `+` over an UNESCAPED leading hyphen, so the bullet list in "
        "`_d5_class_members` is witnessed here too — `grep -cE '^[-\\+]$'` counts 3 on a "
        "`\\`-carrying probe (measured)")

    # ⛔ THE DECLARED SUPERSET IS PINNED, because the docstring makes a claim about it
    # (#13995 review round 18, P2 — MEASURED). Refusing a bracket that carries TWO backslashes
    # left all eight d5 tests GREEN while the paragraph above became false beside green code: the
    # sentence "two backslashes can still pass" had no witness.
    assert _d5_counts_the_blank_class("^[+\\-]$"), (
        "a backslash-bearing bracket is ACCEPTED as a DECLARED SUPERSET — it counts the two class "
        "lines plus a literal `\\` (3, measured). If this reads False, the superset paragraph in "
        "`_d5_counts_the_blank_class` is false and must be corrected in the same commit")
    assert _d5_counts_the_blank_class("^[\\+\\-]$"), (
        "two backslashes do not change the superset: `^[\\+\\-]$` is still accepted and still "
        "counts 3 (`grep -cE` rc 0, measured) — the sentence says so, so it must be witnessed")

    # …and the residual the class test DECLARES: regexes equivalent to this class but spelled
    # outside the measured forms are not recognised. Pinned so a widening reds HERE and the
    # docstring at `_d5_counts_the_blank_class` is corrected in the same commit.
    for unrecognised_spelling in (
        "grep -cE '^[+]$|^[-]$'",       # two anchored alternatives, one per line-half
        "grep -cE '^(?=[+-])[+-]$'",    # a lookahead does the work, the class is wider
    ):
        assert _d5_blank_class_prescriptions(
            f"a prescription: `{unrecognised_spelling}`\n") == [], (
            f"{unrecognised_spelling!r} is now GRADED as the blank class. That may be an "
            "improvement — but the class test describes itself as a BOUNDED equivalence and "
            "names this as a residual, so update that docstring in the same commit instead of "
            "leaving a false statement beside the code (#13995 review, P2)")
    # ⛔ AND A LONE SIGN IS NOT THIS CLASS (#13995 review round 10, P2 — MEASURED). `^-$` counts
    # ONLY the removed half — 0 on a diff whose only blank line is added — and `^+$` matches only
    # `+` lines (`+` is literal there, measured 1 on `x`/`+`/`-`/empty where the class counts 2).
    # Grading either as this
    # class let a row re-spell its way out of half of it while the arm still called it graded,
    # which is a false PASS in the direction that matters: the arm cannot tell a narrower
    # instrument from the one the sentence names. The third row is the alternation spelling of the
    # same half-class (`?` unsupported in ERE → one sign, measured), so the rejection is not a
    # special case for the literal form.
    for half_class in ("^-$", "^+$", "^(\\-)$", "^(?:\\+|-)$", "^[-]$", "^[\\-+]$",
                       "^(+|-)$", "^(-|+)$", "^\\+|-$", "^-|\\+$",
                       # ⛔ AND A THREE-BRANCH BODY, so the guard cannot be narrowed to `== 2`
                       # without a row going RED (#13995 review round 15, P2 — MEASURED: with the
                       # guard rewritten as `len(alternatives) == 2`, every d5 test still passed and
                       # these two were graded as the class, on a `grep -cE` count of 6).
                       "^\\+|-|-$", "^-|\\-|\\+$",
                       # ⛔ AND A FOUR-BRANCH BODY (#13995 review round 16, P3 — MEASURED: with the
                       # guard rewritten as `1 < len(alternatives) < 4` every d5 test still passed
                       # and this one was graded as the class on a `grep -cE` count of 6).
                       "^\\+|-|\\+|-$",
                       # ⛔ AND A RANGE, because the docstring's rule names it. WHICH branch each
                       # row witnesses, measured one at a time on copies (#13995 review rounds
                       # 20-21, P3): `^[+-+-+]$` is the witness for the PLAIN-HYPHEN rule (delete
                       # that rule and it becomes the only newly-accepted row), while the escaped
                       # `-` branch is witnessed by `^[\-+]$` above (delete it and `^[\-+]$` is
                       # the only one newly accepted).
                       "^[+-+-+]$"):
        assert not _d5_counts_the_blank_class(half_class), (
            f"{half_class!r} must NOT be graded as the blank class: none of these counts exactly "
            "the two class lines (`^-$` counts only the removed half, measured 0 on a diff whose "
            "only blank line is added; `^+$` names one shape — the EMPTY line under `-E`; "
            "`^(?:\\+|-)$` loses its group to POSIX ERE; `^\\+|-$` and its siblings are UNGROUPED "
            "alternations, where ERE binds `^` to one branch and `$` to another and five or more "
            "lines match). "
            "Grading it would let the "
            "row's instrument narrow while this arm reported the class as graded "
            "(#13995 review rounds 10-11, P2)")

    # ⛔ …AND FOUR SPELLINGS THAT LOOK EQUIVALENT ARE **NOT** THIS CLASS, EACH MEASURED (#13995
    # review round 11, P2; #14027). The predicate graded all of these as the class while the
    # docstring called them accepted equivalences — "state what you measured" failed in CODE
    # this time, not in prose. Probe for every number below: a file holding `+`, `-`, a lone
    # `\`, a blank line, `++` and `--`. ⛔ `^[+\-]$` and `^[\+\-]$` USED to be in this loop and
    # are NOT any more: they carry a literal `\` member and count 3, and the class test ACCEPTS
    # them as a DECLARED SUPERSET (cost: one impossible diff line) rather than reding a document
    # that writes a plausible spelling — the decision and its cost are stated and pinned at
    # `_d5_counts_the_blank_class`.
    for not_the_class, why in (
        ("^[\\-]$",
         "names `{\\, -}`, i.e. the lone backslash and the hyphen — 2 on the probe for the "
         "WRONG reason, so grading it as the class is false in both directions"),
        ("^[\\-+]$",
         "is not an expression grep will run at all: `grep: Invalid range end`, rc 2 — `-` "
         "between two members is a RANGE and `\\`(0x5C)..`+`(0x2B) is reversed — so a row "
         "spelled this way counts NOTHING, not this class"),
        # ⛔ A DOUBLED BACKSLASH IS NOT AN ESCAPED SIGN (#14027 second review round, P3 —
        # MEASURED). The alternation branch used to strip EVERY backslash from each alternative,
        # so both rows below were graded as this class; in a POSIX ERE `\\` is a literal
        # backslash and the `+` after it is a QUANTIFIER. ⛔ ON THE PROBE DECLARED ABOVE THE
        # DIFFERENCE IS MEMBERSHIP, NOT THE COUNT, and the two rows say which (#14027 — a review
        # round measured `1`/`0` on the four-line probe and wrote "for every number below" beside
        # a six-line one, so the numbers are re-measured on the declared probe here).
        ("^(\\\\+|-)$",
         "matches the `\\` line and the `-` line and NEVER the `+` line (measured: `grep -nE` "
         "reports lines 2 and 3 of the declared probe), so its count is 2 — the SAME number as "
         "this class, for two different lines. Grading it as the class reports an instrument "
         "that counts the wrong two lines"),
        ("^(\\\\+|\\\\-)$",
         "counts 1, not 2: both branches are literal-backslash runs, so on the declared probe it "
         "matches only the lone `\\` line and no `+` or `-` line at all (measured -> 1, rc 0) — "
         "the class is graded onto a command that counts none of it, which is the false PASS "
         "#13962 exists to retire"),
    ):
        assert not _d5_counts_the_blank_class(not_the_class), (
            f"{not_the_class!r} must NOT be graded as the blank class: it {why}. Grading it "
            "reports the row's instrument as the one the sentence names while grep counts "
            "something else — the false PASS #13962 exists to retire (#13995 review round 11, "
            "P2)")
        assert _d5_blank_class_prescriptions(
            f"a prescription: `grep -cE '{not_the_class}'`\n") == [], (
            f"a prescription spelled {not_the_class!r} must not be GRADED as a BLANK-class "
            "count — as a second prescription it would leave the `== 1` arm GREEN while the row "
            "carried an instrument that does not count this class (#13995 review round 11, P2)")


def test_the_d5_count_reader_knows_every_long_option_grep_documents():
    r"""`_d5_all_long_options()` matches `grep --help`, and not through the argument-taking oracle.

    ⛔ WHY THIS EXISTS (#13995, final-head review P1/P3 — MEASURED). `_resolved_long_option` and
    `_long_option_is_accepted` need to know whether a prefix is AMBIGUOUS *in grep's own terms*,
    and the answer is only correct against grep's whole DOCUMENTED set (`grep --help`) — not
    against the smaller set whose arity this reader happens to need. ⛔ GREP HAS TWO UNDOCUMENTED
    ALIASES (measured on GNU grep 3.11), and only one of them may belong here: `--fixed-regexp`
    (`grep --f` lists it among the possibilities, measured, while `grep --help` does not mention it)
    is deliberately ABSENT, because it shares `-F`'s identity — including it would make `--fixed-`
    falsely ambiguous (measured: `grep -c --fixed-` is ACCEPTED) and it counts nothing of this
    class; `--unix-byte-offsets` is in `_d5_undocumented_long_options()` **where the grep on PATH runs
    it** — the allowance is MEASURED, not declared, and it is EMPTY on grep >= 3.12, which removed
    the option (#14138). Measured on GNU grep 3.11:
      * `grep -c --context 2 '^[+-]$'` prints `2` and exits 0, so `--context` DOES take a separate
        word — it was excluded on a `--color` measurement, and the reader then took the `2` as the
        EXPRESSION and the invocation vanished (false PASS);
      * `grep --re` is REFUSED (`option '--re' is ambiguous; possibilities: '--recursive'
        '--regexp'`) although `--regexp` is the only rival inside the arity set, so certifying
        `--re` graded a command grep will not run.
    Two specifiers cannot both be right, so this test derives the set from `grep --help` itself.
    Non-vacuity: adding a long option to grep, or dropping one from `_d5_all_long_options()`, reds
    here by name; the derived set also has to be big enough to be a real universe (≥ 40), so a
    parser that silently matched nothing cannot pass.

    ⛔ `grep --help` IS NOT PORTABLE, AND THE FLOOR IS NOT A SKIP (#14303 review, P2 — MEASURED via
    the #14306 handover). BSD grep 2.6.0-FreeBSD publishes a `usage:` line on **stderr** and exits
    **2**, so the `check=True` form this cell used raised `CalledProcessError` there — the cell could
    not run on macOS at all, and the module's other citation guards lost the run with it. The read
    now comes from whichever stream carried the list (`_d5_documented_long_options`), and the two
    cases the count alone confuses are separated by EXIT CODE: a `--help` that FAILED published no
    usage and there is nothing to pin (skipped, as the platform fact it is); a `--help` that
    SUCCEEDED and still parsed fewer than 40 is this derivation breaking, and that RAISES. The floor
    therefore still fires — witnessed by
    `test_the_documented_universe_floor_REDS_a_broken_derivation_and_skips_only_a_silent_grep`.
    """
    completed = subprocess.run(["grep", "--help"], capture_output=True, text=True, timeout=60)
    try:
        documented = _d5_documented_long_options(completed)
    except _D5NoDocumentedUniverse as no_universe:
        pytest.skip(str(no_universe))
    universe = _d5_all_long_options()
    allowance = _d5_undocumented_long_options()
    # ⛔ THE UNDOCUMENTED ALLOWANCE IS A MEASUREMENT, AND ITS CANDIDATES ARE PINNED AS GENUINELY
    # UNDOCUMENTED (#14027 review round 2, P2; #14138). `grep --help` is silent about
    # `--unix-byte-offsets`, so the universe is `documented` PLUS the MEASURED allowance, and a
    # spelling `grep --help` DOES document may never be a candidate: it would be a second name for a
    # documented option, and the union below would not move because the name is already there — i.e.
    # a documented candidate is silent on BOTH platforms unless it is refused here by name
    # (#14138 review, P3 — measured: adding `--colour` to the candidates left every cell green).
    assert not (set(_D5_UNDOCUMENTED_ALIAS_CANDIDATES) & documented), (
        f"{sorted(set(_D5_UNDOCUMENTED_ALIAS_CANDIDATES) & documented)} is declared an undocumented "
        "candidate while `grep --help` DOCUMENTS it — a candidate is by definition a spelling the "
        "help text omits, and admitting a documented one is invisible to the union assertion below "
        "(#14138)")
    assert documented | allowance == set(universe), (
        f"`_d5_all_long_options()` has drifted from `grep --help` + the MEASURED undocumented "
        f"allowance: missing "
        f"{sorted((documented | allowance) - universe)}, extra "
        f"{sorted(set(universe) - documented - allowance)}. "
        f"The ambiguity test is only as correct as "
        "this universe (#13995)")
    # ⛔ EVERY ARITY ENTRY IS IN THAT UNIVERSE, AND IT IS PINNED (#14027 review round 7, P3 —
    # MEASURED). `_resolved_long_option` used to short-circuit on `_ARG_TAKING_LONG_FLAGS`
    # membership before consulting the universe, which is inert only BECAUSE this holds: a member
    # of the arity set that grep does not document would have been resolved by the short-circuit
    # and not by the universe. With the subset pinned, the short-circuit's removal is safe and a
    # future member added outside grep's set reds HERE rather than silently changing resolution.
    assert universe >= _ARG_TAKING_LONG_FLAGS, (
        f"`_ARG_TAKING_LONG_FLAGS` names spellings outside the universe grep documents: "
        f"{sorted(_ARG_TAKING_LONG_FLAGS - universe)} — `_resolved_long_option` "
        "resolves every arity entry through `_d5_long_option_candidates`, which can only answer "
        "about documented names (#14027 review round 7, P3 — MEASURED)")


def test_the_documented_universe_floor_REDS_a_broken_derivation_and_skips_only_a_silent_grep():
    """The `>= 40` floor is REACHABLE, and it is not the skip it can be confused with.

    ⛔ THIS IS THE GUARD THE FIRST CUT OF THE PORTABILITY FIX KILLED (#14303 review, P2 — the
    #14306 handover; its own review F1 for the mechanism). `pytest.skip` RAISES, so a cell that
    skips on `len(documented) < 40` makes every assertion below that line unreachable and moves the
    floor's failure mode from RED to GREEN — the inert-enforcer shape. The two arms below are the
    mutant that distinguishes them, and neither runs a real grep, so both fire on every platform:

      * a `--help` that SUCCEEDED carrying exactly the floor's worth of long options IS a universe;
      * a `--help` that SUCCEEDED one short of it is THIS DERIVATION breaking and must RAISE;
      * a `--help` that FAILED (rc 2, usage on stderr — BSD grep's shape) published no universe at
        all, which is the ONLY case the skip is allowed to excuse.

    The third arm is also what pins the fix's actual claim: the option list is read from whichever
    stream carried it, so a usage line on stderr is parsed and counted rather than lost.
    """
    def reported(returncode: int, text: str) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["grep", "--help"], returncode=returncode,
            stdout=text if returncode == 0 else "", stderr="" if returncode == 0 else text)

    def usage(count: int) -> str:
        return "Usage: grep [OPTION]... PATTERNS [FILE]...\n" + "\n".join(
            f"  --opt-{'a' * n} <x>" for n in range(1, count + 1))

    assert len(_d5_documented_long_options(reported(0, usage(_D5_LONG_OPTION_FLOOR)))) == (
        _D5_LONG_OPTION_FLOOR), (
        "a successful `grep --help` carrying exactly the floor's worth of long options must be a "
        "universe, or the floor is not a floor (#14303 review, P2)")
    with pytest.raises(AssertionError, match="broken derivation must not be able to certify"):
        _d5_documented_long_options(reported(0, usage(_D5_LONG_OPTION_FLOOR - 1)))
    # ⛔ THE STDERR HALF, READ RATHER THAN TOLERATED: a FAILED `--help` whose usage still yields the
    # floor is a universe, which is the whole point of dropping `check=True`. This arm is the
    # mutant-killer for "parse `stdout` only": with `completed.stdout` alone it raises.
    assert len(_d5_documented_long_options(
        reported(2, usage(_D5_LONG_OPTION_FLOOR)))) == _D5_LONG_OPTION_FLOOR, (
        "a `--help` that exited non-zero but PUBLISHED a full option list on stderr must still be "
        "read — BSD grep's usage goes to stderr, and ignoring that stream is what made this cell "
        "unrunnable on macOS (#14303 review, P2)")
    with pytest.raises(_D5NoDocumentedUniverse, match="exited 2"):
        _d5_documented_long_options(reported(
            2, "usage: grep [-abcdDEFGHhIiJLlMmnOoqRSsUVvwxZ] pattern [file ...]\n"))


def test_the_d5_count_reader_covers_the_undocumented_alias_grep_ACCEPTS(monkeypatch):
    r"""The allowance tracks the grep that would RUN the command — measured, never hardcoded.

    ⛔ WHY THIS CELL ASSERTS A CONSEQUENCE AND NOT ONE PLATFORM'S ANSWER (#14138, MEASURED). GNU grep
    3.11 ACCEPTED `--unix-byte-offsets` (`-u`), accepted every prefix of it
    (`--u`/`--un`/`--uni`/`--unix`), and `grep -c --unix-byte-offsets '^[+-]$'` really counted the
    blank class (`2`). GNU grep **3.12 REMOVED the option**: it answers `grep: unrecognized option
    '--unix-byte-offsets'` and exits 2, and Ubuntu 26.04 — what `ubuntu-latest` now provisions —
    ships 3.12. A hardcoded answer to "does grep run it" is therefore wrong on one of the two
    platforms whichever way it is written, and that is what reddened `test (a)` on main
    `70fbd1a27dd5`.

    ⛔ AND IT IS NOT A SKIP, because the platform is a REAL INPUT to the code under test: this
    reader decides whether a `grep` invocation in a document is one it may grade, so BOTH grep
    answers are consequences it owes (#13995, #14027 round 2 P2 — MEASURED):
      * where grep RUNS the spelling, it is a spelling of `-u` that counts this class, so the
        reader must READ it — declining it left a SECOND blank-class prescription invisible beside
        the one that was read: the false PASS this cell exists to refuse;
      * where grep REFUSES the spelling, the command is one grep will not run, so the reader must
        DECLINE it — grading that expression is the false RED `_long_option_is_accepted` is written
        to refuse.
    So the live platform is pinned against the measured allowance, and BOTH snapshots are pinned
    hermetically, which is what keeps either consequence from being vacuous on whichever grep is
    installed. `--fixed-regexp` stays OUT on BOTH platforms: that exclusion is SEMANTIC (`-F` counts
    `0` of this class, and admitting the alias would make `--fixed-` falsely ambiguous), so no grep
    version decides it.
    """
    alias_spellings = ("--unix-byte-offsets", "--u", "--un", "--uni", "--unix")

    # ⛔ (1) THE LIVE PLATFORM. `runs` is grep's own answer, never this file's memory of a version,
    # and the import-time allowance must be exactly what it re-derives to — a stale allowance is how
    # a hardcoded table would come back (#14138). ⛔ THIS COMPARES THE SNAPSHOT, NOT THE HELPER'S
    # HONESTY (#14138 review, P3 — measured: a helper mutated to answer `True` unconditionally leaves
    # this cell GREEN where the grep under it accepts the spelling, because the comparison would then
    # be a helper against itself). The helper's three arms are pinned independently, by stub greps,
    # in `test_the_undocumented_allowance_probe_tells_refusal_from_a_non_answer`; what this pair of
    # assertions refuses is the other failure — a CONSTANT that no longer matches the live binary.
    runs = _d5_grep_runs_long_option("--unix-byte-offsets")
    assert runs == (
        _d5_grade_alias_spelling("--unix-byte-offsets")[0].returncode in (0, 1)), (
        "`_d5_grep_runs_long_option` disagrees with a raw probe of the SAME binary — one of the "
        "two reads a refused spelling as a count, or the reverse (#14138)")
    rederived = frozenset(
        alias for alias in _D5_UNDOCUMENTED_ALIAS_CANDIDATES if _d5_grep_runs_long_option(alias))
    assert rederived == _d5_undocumented_long_options(), (
        f"the allowance is {sorted(_d5_undocumented_long_options())} but this grep re-derives "
        f"{sorted(rederived)} "
        "— the universe must follow the binary, and grep has no undocumented alias this reader "
        "may add without a candidate entry and this cell (#14138)")
    # ⛔ AND `allowance ⊆ candidates` IS NOT A SECOND GUARD: `rederived` is built by FILTERING the
    # candidate tuple, so the assertion above already entails it and no mutant can red it first
    # (#14138 mutation review, P3 — MEASURED: an allowance containing a non-candidate reds the row
    # above, never here). Deleting a row that can never be the one that fires is rule 3, not a
    # weakening — the membership claim it made is in the message above, by name.

    # Every spelling the alias answers to, on the LIVE grep: READ where grep runs it (and it counts
    # this class `2`), DECLINED where grep REFUSES it — and a THIRD exit code is NEITHER, which is
    # why the verdict comes from `_d5_grep_verdict` rather than a local two-way branch. Only
    # `--unix-byte-offsets` itself passes through the allowance probe; the four PREFIX spellings
    # reach this loop without ever meeting its three-way rule, so a two-way branch here would read a
    # non-answer as a refusal and report it as one — the message interpolates whatever code was
    # actually seen, so it never says `exit 2` unless the run really exited 2.
    # ⛔ AND THE LOOP'S USE OF THE SEAM HAS ITS OWN WITNESS (#14303 review round 4, P3 — MEASURED).
    # Pinning `_d5_grade_alias_spelling` with a stub does NOT pin what THIS loop calls: replacing the
    # loop body with a local `subprocess.run` plus a two-way `rc in (0, 1)` left this file GREEN on
    # both platforms (`36 passed`) — the exact fail-open read the seam exists to prevent, reinstated
    # at the call site with nothing reding, because a stub can only reach the helper. Recording the
    # seam's calls proves the loop actually routes through it: a loop-local two-way records NOTHING.
    seam_calls: list[str] = []
    real_grade = _d5_grade_alias_spelling

    def recording_grade(spelling: str, grep: str = "grep"):
        seam_calls.append(spelling)
        return real_grade(spelling, grep)

    monkeypatch.setitem(globals(), "_d5_grade_alias_spelling", recording_grade)
    for spelling in alias_spellings:
        ran, accepted = _d5_grade_alias_spelling(spelling)
        read = _d5_count_prescriptions(f"a count: `grep -c {spelling} '^[+-]$'`\n")
        if accepted:
            assert ran.stdout.strip() == "2", (
                f"`grep -c {spelling} '^[+-]$'` printed {ran.stdout.strip()!r} on the probe — the "
                "alias must count THIS class for the false-PASS argument to hold, so an accepted "
                "spelling that counts something else needs its own treatment here (#14027)")
            assert read == ["^[+-]$"], (
                f"{spelling!r} is the obsolete `-u` and THIS grep runs it (measured: `2` printed) "
                "— declining it leaves a real blank-class prescription unread (#14027, P2)")
        else:
            assert read == [], (
                f"{spelling!r} is REFUSED by this grep (measured: exit {ran.returncode}) and the "
                "reader still graded it — grading the expression of a command grep will not run is "
                "a false RED on a document that merely mentions it (#13995, MEASURED)")

    assert seam_calls == list(alias_spellings), (
        "the alias loop did not classify every spelling through `_d5_grade_alias_spelling` — a "
        "loop-local two-way `rc in (0, 1)` reads a NON-ANSWER as a refusal and reinstates the "
        f"fail-open read the seam exists to prevent (#14303 round 4): seam called for {seam_calls!r}")

    hidden = ("row: `grep -c '^[+-]$'`\n"
              "and: `grep -c --unix-byte-offsets '^[+-]$'`\n")
    expected_hidden = ["^[+-]$", "^[+-]$"] if runs else ["^[+-]$"]
    assert _d5_blank_class_prescriptions(hidden) == expected_hidden, (
        "a document carrying the row's command AND the alias spelling prescribes the blank class "
        f"{'TWICE (both really count it), so the `exactly one` arm must RED on it' if runs else 'ONCE (this grep refuses the alias, so only the row counts), so the `exactly one` arm must stay GREEN'}"
        " — and seeing ONE where both run is the false PASS this cell exists to refuse (seeing two "
        "where only the row counts would be a false RED) (#14027 review round 2, P2 — MEASURED)")

    # ⛔ (2) BOTH SNAPSHOTS, HERMETICALLY — so each consequence is exercised on EVERY platform
    # rather than only on the one the runner happens to ship. ⛔ EXACTLY ONE NAME IS PATCHED, and
    # it is the only input this walk reads: `_d5_long_option_candidates` resolves
    # `_d5_all_long_options` by NAME from this module's namespace at CALL time (the `#:` block above
    # that function states the contract; it has no docstring), so replacing it replaces the whole
    # universe the walk sees. Patching the allowance as well pinned NOTHING — `_d5_all_long_options`
    # is replaced whole, so its call to `_d5_undocumented_long_options` cannot be reached inside
    # this window (MEASURED: a raising sentinel in that slot leaves this file green). ⛔ AND IT IS
    # NOT THE SOLE CALLER of `_d5_undocumented_long_options`, which is why the dead patch was dead
    # for a LOCAL reason and not a global one: the module calls it from `_resolved_long_option`'s
    # prose path, from this cell, and from the stub cell — several callers, none of them reachable
    # from inside this window. A second input that no assertion can attribute to the behaviour it
    # claims to guard is dead weight (agent-infra#3442 rule 3), so it is gone rather than explained
    # away.
    #   (#14303 review, P3 — the sentence that stood here called that function the "SOLE caller"
    #   and cited "its own docstring" for a `#:` comment block, both of which the source contradicts
    #   one screen away; the measurement it justified was right and is kept.)
    snapshots = (
        (_D5_DOCUMENTED_LONG_OPTIONS | {"--unix-byte-offsets"}, True),
        (frozenset(_D5_DOCUMENTED_LONG_OPTIONS), False),
    )
    for universe, accepted in snapshots:
        monkeypatch.setitem(globals(), "_d5_all_long_options", lambda universe=universe: universe)
        for spelling in alias_spellings:
            read = _d5_count_prescriptions(f"a count: `grep -c {spelling} '^[+-]$'`\n")
            assert read == (["^[+-]$"] if accepted else []), (
                f"with `--unix-byte-offsets` {'IN' if accepted else 'OUT of'} the universe, "
                f"`grep -c {spelling}` must be {'READ' if accepted else 'DECLINED'} — the "
                f"{'reading' if accepted else 'declining'} arm is the one that keeps a real "
                "prescription visible (or a command grep will not run out of the count) (#14138)")
        assert _d5_blank_class_prescriptions(hidden) == (
            ["^[+-]$", "^[+-]$"] if accepted else ["^[+-]$"]), (
            "the two-prescription row must follow the same snapshot: TWO where the alias counts "
            "the class, ONE where grep would not run it (#14027, #14138)")
    monkeypatch.undo()

    # `--fixed-regexp` is the CONTROL, and it is excluded on both platforms for a reason no grep
    # version decides: it is `-F` (a LITERAL pattern), which counts `0` of this class on the same
    # probe (measured on 3.11 AND 3.12), so declining it cannot hide a prescription — and listing it
    # would make `--fixed-` falsely ambiguous against `--fixed-strings`, which grep ACCEPTS.
    # ⛔ THE COUNTING FACT IS ASSERTED ONLY WHERE grep RUNS THE SPELLING (#14138 review round 9, P2
    # — MEASURED). Requiring `rc in (0, 1)` in the SAME `and` made acceptance a hard PLATFORM
    # REQUIREMENT: a grep that DROPS this obsolete alias — the way GNU 3.12 dropped
    # `--unix-byte-offsets` — reds this cell while the reader is untouched and still CORRECT,
    # because declining a spelling grep will not run cannot hide anything, so the exclusion stays
    # fail-closed there. That is the #13995 false RED this cell exists to end, one alias over.
    # Reproduced with a stub that refuses `--fixed-regexp` alone (rc 2, everything else delegated):
    # one cell red. The exit-code rule is READ FROM `_d5_grep_verdict` rather than restated, so a
    # third code still RAISES here instead of deciding.
    fixed_regexp, fixed_accepted = _d5_grade_alias_spelling("--fixed-regexp")
    if fixed_accepted:
        assert fixed_regexp.stdout.strip() == "0", (
            f"`grep -c --fixed-regexp '^[+-]$'` runs on this grep and printed "
            f"{fixed_regexp.stdout.strip()!r} — the exclusion of this alias rests on it counting "
            "NOTHING of this class, so if that changed, declining it could hide a blank-class "
            "prescription and the exclusion needs its own treatment (#14027, MEASURED)")
    assert "--fixed-regexp" not in _d5_all_long_options(), (
        "`--fixed-regexp` would make `--fixed-` falsely ambiguous against `--fixed-strings`, and "
        "grep ACCEPTS `--fixed-` (measured) — the alias must stay out of the universe, which means "
        "it must also stay out of `_D5_UNDOCUMENTED_ALIAS_CANDIDATES`: the candidate list is the "
        "only door into the measured allowance, so a candidate entry for it reds HERE first "
        "(#14027; #14138 mutation review, P3 — the separate candidate-membership row was deleted "
        "because this one always fires before it)")
    assert _d5_long_option_candidates("--fixed-") == ["--fixed-strings"], (
        "`--fixed-` abbreviates `--fixed-strings` and nothing else in the universe — the reason "
        "`--fixed-regexp` may not be added (#14027)")
    # ⛔ THIS ROW WITNESSES MEMBERSHIP, NOT THE COUNTING FACT (#14138 review round 9, P3). The
    # reader declines `--fixed-regexp` because it is absent from the measured universe, so the row
    # answers `[]` whether or not the alias counts this class — crediting it to the `counts 0`
    # measurement (as the previous wording did) named a witness that cannot observe the claim. The
    # counting fact is witnessed above, and only where grep runs the spelling.
    assert _d5_count_prescriptions("a count: `grep -c --fixed-regexp '^[+-]$'`\n") == [], (
        "`--fixed-regexp` is not in the measured option universe, so the reader DECLINES this "
        "unknown spelling — this is the candidate-membership door, and it must stay closed "
        "(#14027)")


def test_the_undocumented_allowance_probe_tells_refusal_from_a_non_answer(tmp_path: Path):
    """`_d5_grep_runs_long_option` reads grep's verdict, and refuses to read anything else.

    ⛔ THE FAIL-OPEN ARM (#14138). The allowance is what keeps a prescription in the alias spelling
    visible, so a probe that answered `False` whenever it was confused would EMPTY it — a second
    blank-class prescription goes unread and nothing reds. grep exits 2 for an invalid option and
    0/1 for a real count, so this cell drives SEVEN stub greps: one that COUNTS, one that REFUSES
    the alias (and answers `--count` normally), one that answers a THIRD code, one that is BROKEN
    for every spelling, one that answers `--count` with rc 0 and NO OUTPUT, one that PRINTS the
    count but still fails the run (rc 2 with stdout `2`), and one that answers a REAL count and
    exits 1 — grep RAN the spelling and selected nothing, which is an ANSWER, not a refusal.
    The `broken`, `silent` and `counts_then_fails` stubs must raise on the CONTROL run — a bare
    rc 2 is grep's generic error code, a wrapper that swallows stdin prints nothing, and a wrapper
    that counts and then fails is not a refusal either — because reading any of them as "refused"
    empties the allowance just the same.
    """

    def stub(name: str, body: str) -> str:
        path = tmp_path / name
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    # `--count` is the control probe's spelling, so every stub that is meant to reach the SECOND run
    # has to answer it normally — otherwise it is testing the control, not the verdict.
    answers_count = 'if [ "$2" = "--count" ]; then echo 2; exit 0; fi\n'
    accepts = stub("grep-accepts", "cat >/dev/null; echo 2; exit 0")
    refuses = stub("grep-refuses", "cat >/dev/null; " + answers_count
                   + 'echo "grep: unrecognized option" >&2; exit 2')
    ran_zero = stub("grep-ran-zero", "cat >/dev/null; " + answers_count + "echo 0; exit 1")
    confused = stub("grep-confused", "cat >/dev/null; " + answers_count + "exit 3")
    broken = stub("grep-broken", 'cat >/dev/null; echo "grep: (standard input): Bad file '
                                 'descriptor" >&2; exit 2')
    silent = stub("grep-silent", "cat >/dev/null; exit 0")
    counts_then_fails = stub("grep-counts-then-fails", "cat >/dev/null; echo 2; exit 2")
    assert _d5_grep_runs_long_option("--unix-byte-offsets", accepts) is True, (
        "a stub grep that counts (`2`, exit 0) must read as ACCEPTED — anything else empties the "
        "allowance on a grep that runs the spelling (#14138)")
    assert _d5_grep_runs_long_option("--unix-byte-offsets", refuses) is False, (
        "a stub grep that answers `unrecognized option`, exit 2, must read as REFUSED — that is "
        "the GNU grep 3.12 case this allowance exists to follow (#14138)")
    # ⛔ rc 1 IS AN ANSWER (#14303 review round 10, P3 — MEASURED). grep exits 1 when it RAN the
    # spelling and selected no lines; only exit 2 means "I will not run that option". Nothing
    # witnessed that distinction: reading rc 1 as `refused` left this file at `34 passed`, because
    # the one place a real rc 1 is consumed — the `--fixed-regexp` control, which prints `0` and
    # exits 1 on GNU grep 3.11 — has its assertion gated on the very arm being flipped. The
    # fail-open consequence is concrete: an accepted spelling that STOPS counting the class would
    # then be classified "refused", so the `--fixed-regexp` counting guard would stop executing and
    # the wrong retargeted-alias class `_d5_grep_verdict`'s docstring forbids could ship unseen.
    assert _d5_grep_runs_long_option("--unix-byte-offsets", ran_zero) is True, (
        "a stub grep that prints `0` and exits 1 RAN the spelling and selected nothing — that is "
        "ACCEPTED, the same as exit 0. Classifying it as `refused` is the fail-open direction: the "
        "allowance would drop a spelling grep really runs, and every consumer gated on this arm "
        "(#14138)")
    # ⛔ AND `MEASURED` MUST BE DISTINGUISHABLE FROM `REMEMBERED` ON THE PLATFORM CI RUNS (#14303
    # review, P2 — MEASURED). On GNU grep >= 3.12 the live allowance is `frozenset()`, so the alias
    # cell's `rederived == _d5_undocumented_long_options()` is two EMPTY sets compared and no mutant
    # of the derivation can red it there. Driving the derivation through its `grep` parameter is what
    # makes the two outcomes separable on every platform: a grep that RUNS the spelling must yield
    # it, and a grep that REFUSES it must yield the empty set. Delete either row and a hardcoded
    # `frozenset()` (or a constant that still carries the 3.11-era spelling) survives CI invisibly.
    assert _d5_undocumented_long_options(accepts) == {"--unix-byte-offsets"}, (
        "a grep that RUNS `--unix-byte-offsets` must produce it in the measured allowance — an "
        "empty allowance on such a box leaves a second blank-class prescription unread (#14138)")
    assert _d5_undocumented_long_options(refuses) == frozenset(), (
        "a grep that REFUSES `--unix-byte-offsets` must produce an EMPTY allowance — anything else "
        "grades the expression of a command grep will not run (#14138)")
    # ⛔ AND THE COMPOSITION IS ITS OWN CLAIM, NOT A CONSEQUENCE OF THE DERIVATION (#14303 review,
    # P2 — MEASURED). `_d5_all_long_options()` is `_D5_DOCUMENTED_LONG_OPTIONS | allowance`, and
    # the two rows above pin only the right-hand side. The composition mutant — `return
    # _D5_DOCUMENTED_LONG_OPTIONS`, dropping the allowance — left every cell GREEN on grep >= 3.12,
    # because there the allowance IS empty, so the union equals the left-hand side anyway: the
    # platform CI runs is exactly the one that could not see it. These two arms make the union
    # observable on EVERY platform, which is what threading `grep` through `_d5_all_long_options`
    # was for.
    assert _d5_all_long_options(accepts) == _D5_DOCUMENTED_LONG_OPTIONS | {"--unix-byte-offsets"}, (
        "a grep that RUNS `--unix-byte-offsets` must have it in the WHOLE universe, not only in the "
        "allowance — a union that drops the measured half is how the documented-alias regression "
        "came back unobserved on grep >= 3.12 (#14303 review, P2)")
    assert _d5_all_long_options(refuses) == _D5_DOCUMENTED_LONG_OPTIONS, (
        "a grep that REFUSES `--unix-byte-offsets` must compose to EXACTLY `grep --help`'s set — "
        "the allowance is empty there, and a union that still carried the spelling would grade a "
        "command this grep will not run (#14303 review, P2)")
    with pytest.raises(AssertionError, match="neither a count"):
        _d5_grep_runs_long_option("--unix-byte-offsets", confused)
    # ⛔ AND A GREP THAT IS BROKEN FOR *EVERY* SPELLING IS NOT A GREP THAT REFUSED THIS ONE (#14138
    # review round 3, P2 — MEASURED). rc 2 is grep's generic error code: `grep -c --count '^[+-]$'
    # <&-` is `Bad file descriptor`, rc 2, on a fully DOCUMENTED option. A bare rc 2 read as
    # "refused" empties the allowance on such a box, so the control run has to catch it first.
    with pytest.raises(AssertionError, match="control probe"):
        _d5_grep_runs_long_option("--unix-byte-offsets", broken)
    # ⛔ AND A PROBE THAT ANSWERS rc 0 WITH NO COUNT IS NOT A WORKING PROBE EITHER (#14138 review
    # round 5, P3 — MEASURED). The control run requires stdout `2` as well as rc 0/1: a wrapper that
    # swallows stdin exits 0 and prints nothing, and a control that read only the RETURN CODE would
    # wave the second run through — whose rc 2 on such a grep would empty the allowance. Removing the
    # `stdout` half of the control leaves every other stub green, which is why this arm exists.
    with pytest.raises(AssertionError, match="control probe"):
        _d5_grep_runs_long_option("--unix-byte-offsets", silent)
    # ⛔ AND THE CONTROL'S EXIT-CODE HALF HAS ITS OWN WITNESS (#14138 mutation review, P3 — MEASURED).
    # Without this stub the `returncode not in (0, 1)` clause of the control is INERT: every other
    # stub that gets past the stdout check answers rc 0/1, so deleting the rc clause left the suite
    # green. This one prints the count and still exits 2, which only the rc clause catches —
    # a grep that reports a count and then fails is not a grep that refused the spelling.
    with pytest.raises(AssertionError, match="control probe"):
        _d5_grep_runs_long_option("--unix-byte-offsets", counts_then_fails)
    # …and a binary that is not there at all is a NON-ANSWER too, which is why it is pinned rather
    # than left to `subprocess` to raise something nobody counted on: the allowance must never be
    # emptied by a probe that could not run (#14138 review, P3).
    with pytest.raises(OSError):
        _d5_grep_runs_long_option("--unix-byte-offsets", str(tmp_path / "grep-that-is-not-here"))


def test_a_prefix_spelling_that_does_not_answer_is_not_a_refusal(tmp_path: Path):
    """The alias cell's THREE-WAY rule, witnessed where it is actually used (#14303 round 3, P3).

    ⛔ THE GAP THIS CLOSES, MEASURED. `_d5_grep_verdict`'s own docstring records the round-8 P2: a
    two-way `if rc in (0, 1) … else` read a THIRD exit code as a REFUSAL and asserted
    "(measured: exit 2)" over a run that never exited 2. Pinning the helper did not pin the alias
    cell's use of it — replacing the loop's verdict with that two-way branch left the whole file
    GREEN on GNU grep 3.11 AND under the 3.12 shim, because no shipped grep answers a third code for
    a PREFIX spelling. So the fix for a P2 was itself unwitnessed.

    This stub answers a third code for `--u` ONLY, the smallest input that separates the two forms:
    the three-way rule RAISES, the two-way one reads a refusal and would then assert
    `"--u" is REFUSED by this grep (measured: exit 3)` — a false RED over a probe that merely
    failed. The second half drives a DIFFERENT spelling through the same stub and requires it to be
    graded normally, so the raise above is attributable to the third code and not to a stub that is
    simply broken.
    """
    stub = tmp_path / "grep-nonanswer-for-one-prefix"
    stub.write_text(
        "#!/bin/sh\n"
        'if [ "$2" = "--u" ]; then echo "grep: input/output error" >&2; exit 3; fi\n'
        "cat >/dev/null; echo 2; exit 0\n",
        encoding="utf-8")
    stub.chmod(0o755)
    with pytest.raises(AssertionError, match="neither a count"):
        _d5_grade_alias_spelling("--u", str(stub))
    ran, accepted = _d5_grade_alias_spelling("--uni", str(stub))
    assert (ran.returncode, ran.stdout.strip(), accepted) == (0, "2", True), (
        "the same stub must be graded normally for a spelling it answers — otherwise the raise "
        f"above proves nothing about the third code (#14138): got rc={ran.returncode} "
        f"stdout={ran.stdout.strip()!r} accepted={accepted}")


def test_the_allowance_is_not_derived_at_import(tmp_path: Path):
    """The lazy derivation is a CONTRACT, and nothing else pinned it (#14303 review, P3 — MEASURED).

    ⛔ WHY THIS CELL EXISTS. `_d5_undocumented_long_options` is deliberately lazy: an IMPORT-TIME
    probe made this WHOLE file uncollectable on a box with no `grep`, taking every citation guard in
    it down with the cells that need grep, and a `grep` that blocked on PATH hung COLLECTION instead
    of failing one cell (the consequence is recorded on that function). No cell asserted it, so it
    could be silently undone: before this cell and the collect-only cell below existed, prepending a
    module-level `_D5_ALL = _d5_all_long_options()` left every cell green on a box that HAS grep, and
    only a grep-less box ever noticed — which is exactly the platform this file is least often run
    on. At THIS head the hoist is visible, and the two child-process cells are what made it so
    (MEASURED, and stated WITHOUT a count because a cell added to this file falsifies one: the raising
    hoist reds this cell and the collect-only one; the hoist that swallows its error reds arm (a)
    alone).

    ⛔ MEASURED IN CHILD INTERPRETERS, not in-process: this module's own import has already happened
    by the time any cell runs, so an in-process check could never observe it. TWO children, because
    the two observable consequences are different and either one alone is escapable:

      (a) **the spawn.** The child's `PATH` puts a `grep` that RECORDS that it ran first. An
          import-time probe that RAISES is caught by (b), but one that SWALLOWS its error — a
          module-level `try: … except Exception: pass` — leaves `currsize == 0` exactly as a lazy
          file does (a cache does not record a raising call) while genuinely spawning grep at
          import. That is the shape that HANGS collection behind a blocking `grep`, so the marker
          is what decides: it must not exist after the import. Inferring "no spawn" from "the child
          did not raise" is what the previous round's cell did, and it could not see this shape.
      (b) **the no-grep box.** A `PATH` with no `grep` at all — the box the docstring describes —
          must import successfully AND leave the allowance underived (`currsize == 0`).

    (a) fails a spawn, (b) fails a raise or a derivation; neither alone is sufficient.

    ⛔ (b) IS KEPT FOR ITS INPUT, NOT AS A SECOND WITNESS. No mutant reaches it alone: the raising
    hoist reds (a) first, so (b) has no independently attributable row (#14303 review, P2 — MEASURED).
    It stays because its INPUT is different — no `grep` on PATH at all, against a `grep` that answers
    wrongly — and that is the box the historical failure happened on. Saying so here is cheaper than
    letting a later reader count two witnesses where there is one.
    """
    source = str(Path(__file__).resolve())
    import_probe = (
        "import importlib.util, sys\n"
        f"source = {source!r}\n"
        "spec = importlib.util.spec_from_file_location('d5_import_probe', source)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['d5_import_probe'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "print('IMPORT-OK', mod._d5_undocumented_long_options.cache_info().currsize)\n"
    )

    # (a) A `grep` that RECORDS its own invocation. The ordinary PATH stays behind it so the stub's
    # own `sh` and `touch` still resolve, and nothing else can shadow the recording grep.
    # ⛔ THE MARKER PATH IS QUOTED. Unquoted, a `tmp_path` containing a space makes `touch` fail, the
    # marker is never created, and this guard then passes VACUOUSLY while grep really was spawned
    # (#14303 review, P2 — reproduced by hand with a spaced path). pytest's basetemp is pinned under
    # `/tmp/tt_<hex>` today, so it is unreachable in this suite; that makes the quoting hardening,
    # not a live fix, which is why it is stated rather than silently tightened.
    spy_dir = tmp_path / "spy-bin"
    spy_dir.mkdir()
    marker = tmp_path / "grep-was-spawned"
    spy = spy_dir / "grep"
    spy.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n', encoding="utf-8")
    spy.chmod(0o755)
    spy_run = subprocess.run(
        [sys.executable, "-c", import_probe],
        capture_output=True, text=True, timeout=300,
        env={**os.environ,
             "PATH": spy_dir.as_posix() + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert spy_run.returncode == 0, (
        "importing this module with a `grep` on PATH must SUCCEED (#14138): "
        f"rc={spy_run.returncode} stderr={spy_run.stderr.strip()[-500:]!r}")
    assert not marker.exists(), (
        "`grep` was SPAWNED AT IMPORT TIME — the derivation must stay lazy, or a `grep` that blocks "
        "on PATH hangs COLLECTION of this whole file instead of failing one cell (#14138); the "
        f"recording stub left {marker.name} behind, so a probe ran during import")

    # (b) …and a box with NO `grep` at all must still import, with the allowance underived.
    grep_free = tmp_path / "no-grep-on-this-path"
    grep_free.mkdir()
    done = subprocess.run(
        [sys.executable, "-c", import_probe],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "PATH": str(grep_free)},
    )
    assert done.returncode == 0 and "IMPORT-OK" in done.stdout, (
        "importing this module with NO `grep` on PATH must SUCCEED — an import-time probe aborts "
        "COLLECTION of this whole file, so 0 of its citation guards run (#14138): "
        f"rc={done.returncode} stderr={done.stderr.strip()[-500:]!r}")
    assert done.stdout.strip().endswith("IMPORT-OK 0"), (
        "the allowance was DERIVED AT IMPORT: the derivation must stay lazy so a box without `grep` "
        f"still collects this file (#14138): child said {done.stdout.strip()!r}")


def test_the_d5_alias_cells_still_COLLECT_when_grep_cannot_answer(tmp_path):
    r"""A grep that is a NON-ANSWER for every spelling must cost its cells, not the WHOLE FILE.

    ⛔ THE COMPLEMENT of `test_the_allowance_is_not_derived_at_import`, and both are needed (#14138).
    That cell removes `grep` from `PATH` entirely, so the failure it pins is an eager probe that
    RAISES. This one leaves a `grep` ON the path that exits 3 for every call — the "non-answer" the
    allowance probe refuses to read as a refusal — so the failure it pins is an eager probe that
    aborts collection where grep is PRESENT but broken. Different hosts, and the second is the shape
    that made this file uncollectable once already.

    ⛔ WHY IT CANNOT BE AN IN-PROCESS ASSERTION. Before the two child-process cells existed, an eager
    `_D5_ALL_LONG_OPTIONS_EAGER = _D5_DOCUMENTED_LONG_OPTIONS | _d5_undocumented_long_options()`
    left the whole suite green on a working grep — nothing in-process could observe it — and only a
    collection of the file on a hostile host did (MEASURED: `no tests collected, 1 error` for the
    eager form, against a full collection for the deferred one). At this head the hoist reds this
    cell and the import-time one by name; the point of the paragraph is why the observation has to
    leave the process, not that the guard is still missing.

    ⛔ MEASURED BY COMPARING, NOT BY ASSERTING A COUNT. The cell count is not written down here: a
    later test added to this file would silently falsify it. The same `--collect-only` run is made
    twice — once on the real PATH, once behind the hostile stub — and the two must AGREE. The floor
    keeps a parser that collected nothing from certifying agreement with itself.

    ⛔ NOT A MUTANT-DRIVEN ROW, and stated so rather than passed off as covered: no edit to this
    file changes what a NORMAL grep answers, so the only mutant it reddens is the hoist above.
    """
    stub = tmp_path / "grep"
    stub.write_text("#!/bin/sh\ncat >/dev/null\nexit 3\n", encoding="utf-8")
    stub.chmod(0o755)

    def collect(env_path: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pytest", str(Path(__file__)), "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=300,
            env={**os.environ, "PATH": env_path, "TORTOISE_TEST_CARVE_OUT": "1"},
        )

    normal = collect(os.environ["PATH"])
    assert normal.returncode == 0, (
        f"this file did not collect on an ordinary PATH, so the comparison below cannot say "
        f"anything about the host that matters: rc {normal.returncode}\n"
        f"{normal.stdout[-2000:]}\n{normal.stderr[-2000:]}")
    hostile = collect(f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert hostile.returncode == 0, (
        "a grep that exits 3 for EVERY call — the non-answer the allowance probe refuses to read "
        "as a refusal — aborted COLLECTION of this file, so every citation guard in it was lost "
        "with the cells that need grep. The allowance must be derived LAZILY, at the point a cell "
        "asks for it, never at import (#14138): "
        f"rc {hostile.returncode}\n{hostile.stdout[-2000:]}\n{hostile.stderr[-2000:]}")

    def collected(proc: subprocess.CompletedProcess) -> int:
        found = re.findall(r"^(\d+) tests? collected", proc.stdout, re.MULTILINE)
        assert found, f"no collection count in the output: {proc.stdout[-2000:]!r}"
        return int(found[-1])

    have, also_have = collected(normal), collected(hostile)
    assert have >= 30, (
        f"only {have} cells collected on an ordinary PATH — the floor exists so a parser that "
        "collected nothing cannot certify that two empty collections agree (#14138)")
    assert also_have == have, (
        f"a grep that cannot answer for any spelling changed what COLLECTS: {also_have} cells "
        f"behind the stub against {have} on an ordinary PATH. Collection must not depend on grep "
        "at all — only the cells that measure it may (#14138)")


def test_the_d5_count_reader_reads_an_UNQUOTED_pattern_and_refuses_an_ambiguous_prefix():
    r"""The final-head review's four findings, each pinned with the measurement that decides it.

    ⛔ ALL FOUR MEASURED ON GNU grep 3.11 (#13995, MEASURED — the reviewer's P1/P2/P3, verified
    one at a time rather than applied on the strength of the prose):
      * `grep -c --context 2 '^[+-]$'` counts 2, so `--context` consumes a separate word. Reading
        `2` as the expression made a real blank-class count invisible → false PASS.
      * `grep -c -e^[+-]$` and `grep -c --regexp=^[+-]$` count 2: an UNQUOTED `-e`/`--regexp`
        value is still the pattern, and demanding a quote let a second prescription hide → false
        PASS. `grep -c -e 'x' -e^[+-]$` counts the union of both, so BOTH are prescriptions.
      * `grep -c'a' '^[+-]$'` counts 2: the shell has already removed the quotes, so this is argv
        `-ca` and the operand IS the pattern — the old pin called it "not a count" and that was a
        false PASS dressed as a safety pin. `grep -c'^[+-]$'` is argv `-c^[+-]$` and grep rejects
        it (`invalid option`), so a NON-alphanumeric quoted suffix stays unread.
      * `grep -c --re '^[+-]$'` is REFUSED (`option '--re' is ambiguous`), so declining the whole
        invocation is right: grading the operand of a command grep will not run is the false RED
        this file calls the worse direction.
    ⛔ AND THE FINAL REVIEW'S TWO FALSE VERDICTS (#14027 final review, P2/P3 — MEASURED ON GNU
    grep 3.11). Both were introduced by this reader, both were fail-closed before it, and each is
    pinned below with the grep output that decides it:
      * `grep -c -e=^[+-]$` counts **0**, rc 1 — `=` does not separate a SHORT option's argument,
        so the pattern is `=^[+-]$` and the row counts NOTHING of the blank class (`grep -c -e=a`
        prints 1 on a file holding `=a`, rc 0). Stripping the `=` graded a command that counts
        zero as the class. The invocation is still READ — the pattern is just `=^[+-]$` — and the
        assertion below is on the EXPRESSION, so a revert reds here by name.
      * `grep -c --count, '^\+$'` is REFUSED (`unrecognized option '--count,'`, rc 2) — the
        unquoted tail is part of the NAME. Discarding it graded the invocation as `--count`, a
        command grep will not run, and that was reachable both ways (a false PASS when it is the
        row's only command, a false RED as a second prescription).
    Non-vacuity: dropping `--context` from `_ARG_TAKING_LONG_FLAGS` reds the first row; restoring
    the quote requirement reds the `-e ^[+-]$`/`--regexp ^[+-]$`/`-e^[+-]$`/`--regexp=` rows;
    restoring the old pin reds the `-c'a'` row; removing `_long_option_is_accepted` reds the
    `--re`/`--counts`/`--count,` rows; restoring the unconditional `=` strip reds the `-e=` row;
    restoring `return option, None` in `_d5_flag_word` reds the `--count,` row; restoring
    `return option, rest` for a long option's QUOTED tail reds the `--count'x'` row, and a blanket
    refusal of that tail reds its `--count''` control.
    ⛔ A SHORT CLUSTER SPLITS AT ITS FIRST ARGUMENT-TAKING FLAG, AND THAT FLAG OWNS THE REST AS
    ITS VALUE — quoted or not (#14027 round 9, P2; round 10, P1 — MEASURED). This paragraph used
    to describe TWO different tests (quoted: `any(...)`; unquoted: `option[-1] in …`) and to
    call `-ec^[+-]$` "not a flag word at all". The split runs before both, so neither test decides
    anything for a cluster that carries an argument-taking flag, and BOTH `-ec'^[+-]$'` and
    `-ec^[+-]$` are `-e` with the value `c^[+-]$`. A cluster with NO argument-taking flag is the
    only place a two-quoting distinction survives, and there it is quote REMOVAL: an alphanumeric
    residue is one word (`grep -c'x'` is argv `-cx`), a non-alphanumeric one is unreadable
    (`grep -c'^\+$'` is argv `-c^\+$`, rejected). The pins below therefore witness the SPLIT,
    not an asymmetry — reverting it moves them — which is why they are reader pins and not graded
    rows: `-e`'s value is a pattern that is not this class, so no prescription changes either way.
    """
    for context in ("grep -c --context 2 '^[+-]$'", "grep -c --context=2 '^[+-]$'",
                    "grep -c -C 2 '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{context}`\n") == ["^[+-]$"], (
            f"{context!r} counts the blank class (measured: 2) — the long form takes a SEPARATE "
            "word, exactly like `-C`, and reading that word as the expression is a false PASS "
            "(#13995, MEASURED)")

    # ⛔ TWO DIFFERENT CODE PATHS READ AN UNQUOTED PATTERN, AND EACH NEEDS ITS OWN ROW (#13995
    # final-head review, P2 — MEASURED). A SEPARATE-word value (`-e ^[+-]$`) is consumed by
    # `_d5_command_expression`'s `awaiting_role == "pattern"` branch; an ATTACHED one
    # (`-e^[+-]$`, `--regexp=^[+-]$`) by its `attached` branch. The guard was added for the
    # SEPARATE-word spelling its comment names, but every row here used to be ATTACHED, so the
    # separate-word branch had NO assertion at all: reverting it to the old quote-requiring form
    # left the whole suite GREEN, the only observable change being
    # `_d5_count_prescriptions("grep -c -e ^[+-]$")` going `['^[+-]$']` -> `[]`. Both spellings are
    # pinned now, so each branch reds by name.
    for unquoted in (
        "grep -c -e ^[+-]$",               # SEPARATE word: the branch the comment names
        "grep -c --regexp ^[+-]$",         # …the long spelling of the same
        "grep -c -e x -e ^[+-]$",          # …and the union of two separate-word patterns,
                                          #    where the quoted one must not swallow its mate
        "grep -c -e^[+-]$",                # ATTACHED, short
        "grep -c --regexp=^[+-]$",         # ATTACHED, long with `=`
        "grep -c -e 'x' -e^[+-]$",         # …attached beside a quoted first pattern
    ):
        expected = ["x", "^[+-]$"] if " -e 'x'" in unquoted or " -e x " in unquoted else [
            "^[+-]$"]
        assert _d5_count_prescriptions(f"a count: `{unquoted}`\n") == expected, (
            f"{unquoted!r} is a count of the blank class (measured: 2) — an unquoted `-e` value is "
            "STILL the pattern, and requiring a quote let a second prescription hide behind the "
            "first (#13995, MEASURED). The SEPARATE-word row is not decoration: it is the only "
            "witness for `_d5_command_expression`'s `awaiting_role == 'pattern'` branch, and "
            "without it that branch can be reverted with the suite staying green")

    assert _d5_count_prescriptions("a count: `grep -c'a' '^\\+$'`\n") == ["^\\+$"], (
        "`grep -c'a'` is argv `-ca` — the shell removed the quotes, so the quoted text is MORE "
        "FLAG CHARACTERS and the operand IS the pattern (measured: it counts). The old pin called "
        "this `not a count`, which was a false PASS (#13995, MEASURED)")
    assert _d5_count_prescriptions("a count: `grep -c'^\\+$'`\n") == [], (
        "`grep -c'^\\+$'` is argv `-c^\\+$`, which grep rejects (`invalid option`) — a "
        "non-alphanumeric quoted suffix is not a flag, so the word is unreadable, not a count")

    # ⛔ AN UNQUOTED TAIL ON A LONG OPTION IS PART OF THE NAME, NOT A VALUE (#14027 final review,
    # P3 — MEASURED): `grep -c --count, '^\+$'` is refused (`unrecognized option '--count,'`, rc
    # 2), so the invocation must be UNREAD. Discarding the tail read it as `--count` and graded a
    # command grep will not run — a false PASS when it is the row's only command, a false RED as a
    # second prescription. Pinned apart from the two above because it reds through a DIFFERENT
    # revert (`_d5_flag_word`'s `return option, None`), not `_long_option_is_accepted`.
    assert _d5_count_prescriptions("a count: `grep -c --count, '^\\+$'`\n") == [], (
        "`grep -c --count,` is REFUSED by grep (`unrecognized option '--count,'`) — the unquoted "
        "tail belongs to the NAME, so the word is unreadable and no command is graded "
        "(#14027 final review, P3 — MEASURED)")

    # ⛔ `=` IS NOT A SEPARATOR ON A SHORT OPTION (#14027 final review, P2 — MEASURED ON GNU grep
    # 3.11): `printf '+\n-\nxx\n\n' | grep -c -e=^[+-]$` prints 0, rc 1, and `grep -c -e=a`
    # prints 1 on a file holding `=a` (rc 0) — the pattern IS `=^[+-]$`. The invocation is READ
    # (unlike `--count,` above), but its expression is `=^[+-]$`, which is NOT the blank class:
    # stripping the `=` made a command that counts ZERO of the class grade AS the class, the false
    # PASS #13962 exists to refuse. Both halves are asserted so a revert cannot pass on either.
    assert _d5_count_prescriptions("a count: `grep -c -e=^[+-]$`\n") == ["=^[+-]$"], (
        "`grep -c -e=^[+-]$` is `-e` with the pattern `=^[+-]$` (measured: counts 0, rc 1) — the "
        "`=` separates a LONG option's argument and not a short one's, so stripping it invented a "
        "blank-class prescription out of a command that counts none of it (#14027 final review, "
        "P2 — MEASURED)")
    assert _d5_blank_class_prescriptions("a count: `grep -c -e=^[+-]$`\n") == [], (
        "`grep -c -e=^[+-]$` must NOT be graded as the blank class: its pattern is `=^[+-]$` "
        "(measured 0, rc 1), so grading it is a false PASS in the direction this arm exists to "
        "refuse (#14027 final review, P2 — MEASURED)")

    # ⛔ A QUOTED TAIL ON A LONG OPTION IS MORE NAME, NOT A VALUE (#14027 final review, P2 —
    # MEASURED): the shell removes the quotes, so `grep -c --count'x' '^[+-]$'` is argv
    # `--countx`, which grep REFUSES (`unrecognized option '--countx'`, rc 2) — the invocation is
    # unread. Reading that tail as `--count`'s attached value graded a command grep will not run
    # AS the blank class, the same defect the `--count,` rule above closes. The CONTROL is the
    # same word with an EMPTY quote pair: it collapses to argv `--count` (measured, prints 2), so
    # this is QUOTE REMOVAL and not "every quoted tail is unread" — the pair says which one it is.
    assert _d5_count_prescriptions("a count: `grep -c --count'x' '^[+-]$'`\n") == [], (
        "`grep -c --count'x' '^[+-]$'` is argv `--countx` and grep refuses it (`unrecognized "
        "option '--countx'`), so no command is graded — grading it is the false PASS the "
        "unquoted-tail rule closes (#14027 final review, P2 — MEASURED)")
    assert _d5_count_prescriptions("a count: `grep -c --count'' '^[+-]$'`\n") == ["^[+-]$"], (
        "`grep -c --count'' '^[+-]$'` collapses to argv `--count` and really counts the blank "
        "class (measured: 2). Reading this row as unread would mean the quoted tail is refused "
        "wholesale instead of having its quotes removed, and a REAL count would go invisible "
        "(#14027 final review, P2 — MEASURED)")

    # ⛔ QUOTE REMOVAL FIRST, THEN THE SEPARATOR RULE (#14027 second review round, P3 —
    # MEASURED). Making every quoted tail "more name" made a `=`-shaped one unread, and grep RUNS
    # those: `--context'=2'` is argv `--context=2` (prints 2, rc 0) and `--regexp'=^[+-]$'` is argv
    # `--regexp=^[+-]$`, whose pattern IS the blank class. ⛔ THE TWO ROWS ARE NOT THE SAME KIND OF
    # PIN (#14027 third review round, P3 — MEASURED): `--context'=2'` IS a regression pin (HEAD
    # read `['^[+-]$']`), while `--regexp'=^[+-]$'` CORRECTS a false PASS that HEAD and the merge
    # base both carried — they reported the expression `=^[+-]$`, which grep counts 0 of on the
    # probe (rc 1), over a command whose real pattern counts 2.
    assert _d5_count_prescriptions("a count: `grep -c --context'=2' '^[+-]$'`\n") == ["^[+-]$"], (
        "`grep -c --context'=2' '^[+-]$'` is argv `--context=2` and grep counts the blank class "
        "(measured: 2, rc 0) — a `=` that survives quote removal is STILL the separator, and "
        "reading the whole word as the NAME declines a command grep executes "
        "(#14027 second review round, P3 — MEASURED)")
    assert _d5_count_prescriptions("a count: `grep -c --regexp'=^[+-]$'`\n") == ["^[+-]$"], (
        "`grep -c --regexp'=^[+-]$'` is argv `--regexp=^[+-]$`, so the pattern is `^[+-]$` and "
        "grep counts the blank class (measured: 2) — treating `'=^[+-]$'` as an attached "
        "value's LITERAL text would report the expression `=^[+-]$`, which is not this class "
        "(#14027 second review round, P3 — MEASURED)")

    # ⛔ THIS ROW IS THE ONE THE MODULE COMMENT CITES, and a claim nothing varies on is the
    # inert-narration defect this file bans (#14027 P3 — MEASURED). `grep -c -ce'^[+-]$'` is argv
    # `-c -e ^[+-]$` — the cluster `-ce` splits at the `e`, so the count flag and the pattern are
    # separate words — and real grep prints `2` with it. A reader that treated every quoted tail
    # as more flag characters would call it unreadable and leave a REAL count invisible behind it,
    # which is the false PASS this arm exists to refuse. ⛔ THE TWO `-ec` PINS BELOW ARE WITNESSES OF
    # THE SPLIT, NOT OF AN ASYMMETRY, and this comment used to claim the opposite on a measurement
    # that no longer holds: it said all three rows were "green on `origin/main`'s reader" and that
    # the `any(...)`/`option[-1]` mutants moved them. Measured since the split landed: the `-ec`
    # rows are NOT green on the base reader and neither mutant moves them, because the split
    # returns before both branches (#14027 round 10, P2). Revert the split and all three move.
    # Measured real grep: `-ec'^[+-]$'` is argv `-ec^[+-]$` = `-e c^[+-]$` (rc 1, nothing counted)
    # and `-ec^[+-]$` is the same argv, so `-e`'s pattern is `c^[+-]$` and neither token carries a
    # count flag of its own.
    assert _d5_blank_class_prescriptions("a count: `grep -c -ce'^[+-]$'`\n") == ["^[+-]$"], (
        "`grep -c -ce'^[+-]$'` is argv `-c -e ^[+-]$` and grep counts the blank class (measured: "
        "2, rc 0) — the class lives in the quoted tail of a cluster whose first character is the "
        "count, and the module comment cites this spelling as READ. A reader that treated every "
        "quoted tail as more flag characters would call it unreadable and leave a REAL count "
        "invisible behind it, which is the false PASS this arm exists to refuse "
        "(#14027 P3 — MEASURED)")
    # ⛔ BOTH SHAPES BELOW CHANGED WHEN THE CLUSTER STARTED SPLITTING AT ITS FIRST
    # ARGUMENT-TAKING FLAG (#14027 round 9, P2 — MEASURED). grep parses a cluster left to right
    # and the FIRST flag that takes an argument owns the rest, so `-ec'^[+-]$'` and `-ec^[+-]$` are
    # both argv `-ec^[+-]$` = `-e` with the pattern `c^[+-]$` — the NAME is `-e`, not `-ec`, and
    # the value starts at the `c`. The pins used to assert the opposite (`-ec` + `'^[+-]$'`, and
    # `None` for the unquoted form) because the reader looked at the cluster's LAST character; that
    # model is what let `grep -c -Dread '^[+-]$'` — which really counts the blank class — come back
    # UNREAD, a live false PASS. Neither shape is graded either way (`-e`'s value is a pattern that
    # is not this class), so the correction costs no row.
    assert _d5_flag_word("-ec'^[+-]$'") == ("-e", "c^[+-]$"), (
        "`-ec'^[+-]$'` is `-e` with an attached value `c^[+-]$`: the FIRST argument-taking "
        "character owns the tail, as grep parses it, and the quotes are the shell's — argv "
        "`-ec^[+-]$` is `-e c^[+-]$`, so a reported value of `c'^[+-]$'` is a string no command "
        "can contain (#14027 round 9, P2/P5 — MEASURED)")
    assert _d5_flag_word("-ec^[+-]$") == ("-e", "c^[+-]$"), (
        "`-ec^[+-]$` is the same argv and the same reading — `-e`, pattern `c^[+-]$` — so it IS a "
        "flag word with an attached value; grep runs it (rc 1, nothing matches). Pinned `None` "
        "before the split, which is the model that hid `-cDread` (#14027 round 9, P2 — MEASURED)")
    # ⛔ AND THE SPELLING THAT MOTIVATED THE SPLIT IS GRADED, NOT MERELY DESCRIBED (#14027 round 9,
    # P3 — MEASURED): no earlier row pinned `-Dread`/`-cDread` as READ, and the rows above do not see
    # a TAIL-level revert — a reader that splits at the cluster's LAST argument-taking character
    # keeps the `-ce`/`-ec` rows green and flips only this spelling (measured). `grep -c
    # -Dread '^[+-]$'` really counts the blank class (`2` on the probe, rc 0), and `-cDread` is the
    # same argv with `-c` in front of it (`2`, rc 0), so both must be READ. Under the one-name model
    # this row refuses, `-cDread` is a single word whose LAST character (`d`, directories) is the one
    # still awaiting a value (`_d5_needs_a_separate_value("-cDread")` is `True`), so the quoted
    # expression is consumed there and the invocation answers `[]`.
    for read_spelling in ("grep -c -Dread '^[+-]$'", "grep -c -cDread '^[+-]$'"):
        assert _d5_blank_class_prescriptions(f"a count: `{read_spelling}`\n") == ["^[+-]$"], (
            f"{read_spelling!r} counts the blank class — `-D` takes `read` as its value and `-c` "
            "still counts (measured: 2, rc 0) — so the expression must be READ. Answering `[]` is "
            "the false PASS the round-9 split closed (#14027 round 9, P3 — MEASURED)")

    # ⛔ DECLARED RESIDUAL, AND IT IS PINNED AS ONE (#14027 third review round, P3 —
    # MEASURED): this reader checks that an argument-taking SHORT flag HAS an argument, never that
    # the argument VALUE is one grep accepts. So `grep -c -A, '^[+-]$'` — grep answers `,: invalid
    # context length argument`, rc 2 — is graded as a blank-class count. The merge base was
    # FAIL-CLOSED here (an unquoted tail returned `None`); the unquoted-tail rule that reads
    # `-e^[+-]$` (a REAL count) is what made this spelling reachable, so the residual is stated
    # rather than implied. It is not closable with a shape test: grep runs `-m-1` and `-A2` and
    # refuses `-A,` and `-Anever`, and `-e`/`-f` take arbitrary text — a value grammar this
    # reader does not model, pinned here so a widening (or a narrowing) reds at this row.
    assert _d5_blank_class_prescriptions("a count: `grep -c -A, '^[+-]$'`\n") == ["^[+-]$"], (
        "KNOWN-UNGRADED SPELLING, pinned as a declared residual rather than a verdict: `grep -c "
        "-A, '^[+-]$'` is refused by grep (`,: invalid context length argument`, rc 2) and this "
        "reader grades it as the blank class, because it validates the flag NAME and not the "
        "argument VALUE. If this row ever returns [], the residual has been closed — delete the "
        "residual sentence in `_d5_flag_word`'s docstring in the same commit (#14027 third review "
        "round, P3 — MEASURED)")

    for refused in ("grep -c --re '^[+-]$'", "grep -c --counts '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{refused}`\n") == [], (
            f"{refused!r} is REFUSED by grep (ambiguous `--re`; unknown `--counts`), so the "
            "invocation is declined rather than graded — grading it reds a document that merely "
            "mentions the spelling (#13995, MEASURED)")
    # ⛔ THE `--colour` ALIAS IS A FALLBACK, NOT A RIVAL (#13995, MEASURED — and this pair is the
    # whole rule). `--cou=always` is refused with `option '--count' doesn't allow an argument`, so
    # a prefix matching `--count` resolves to it and the `-u` spelling is no rival; but `--colou`
    # matches NOTHING once the alias is set aside, and grep runs it (`2` printed), so declining it
    # made a real BLANK-class prescription invisible behind one that was read — a false PASS in
    # the exact direction this cell exists to refuse. Measured: `--col`, `--colo`, `--colou`,
    # `--colour` are all accepted and `--col=always` colours the output.
    for coloured in ("grep -c --col '^[+-]$'", "grep -c --colo '^[+-]$'",
                     "grep -c --colou '^[+-]$'", "grep -c --colour '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{coloured}`\n") == ["^[+-]$"], (
            f"{coloured!r} is accepted by grep as `--colour` (measured: it counts 2) — refusing it "
            "because the alias was excluded from the candidate set left a real prescription "
            "unread (#13995, MEASURED)")

    for accepted in ("grep -c --coun '^[+-]$'", "grep -c --cou '^[+-]$'",
                     "grep -c --max-c 2 '^[+-]$'", "grep -c --count '^[+-]$'",
                     "grep -c -- '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{accepted}`\n") == ["^[+-]$"], (
            f"{accepted!r} IS accepted by grep (measured) — the refusal above must not overshoot "
            "into declining real spellings (#13995)")

def test_the_d5_count_reader_reads_the_LONG_spelling_and_the_ABBREVIATION_of_a_flag():
    r"""A long spelling and its unambiguous abbreviation are the SAME flag — for count, for
    inversion, and for suppression — and an ambiguous prefix is neither.

    ⛔ MEASURED ON GNU grep 3.11, BOTH DIRECTIONS (#13995, after the word walk landed):
      * `grep --cou`/`--coun` ARE `--count` (`2` printed), while `--c`/`--co` are AMBIGUOUS between
        `--count`, `--color` and `--context` and grep refuses them (`option '--c' is ambiguous`);
      * `--max-c 2` IS `--max-count 2`, so its `2` is the flag's ARGUMENT — reading it as the
        expression was a false PASS;
      * `--files-with-matches` prints a FILENAME and `--quiet`/`--silent` print NOTHING: none is a
        count, and grading one reds a document that merely mentions it in prose — the false RED
        this file calls the worse direction;
      * `--inv`/`--inver`/`--invert` ARE the inversion (the complement is printed), so the command
        stays SEEN and stays OUT of the graded set.
    Non-vacuity: dropping `_resolved_long_option` reds the `--max-c` row; dropping
    `_D5_COUNT_SUPPRESSING_LONG_FLAGS` reds the suppressed rows; making `_long_flag_matches` ignore
    its `rivals` reds the `--c`/`--co` rows.
    """
    for spelled_count in ("grep --count '^[+-]$'", "grep --coun '^[+-]$'", "grep --cou '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{spelled_count}`\n") == ["^[+-]$"], (
            f"{spelled_count!r} is a count of the blank class — the long spelling and its "
            "UNAMBIGUOUS abbreviations are accepted by grep (measured: `--cou` prints 2 where "
            "`--c`/`--co` are ambiguous) (#13995, MEASURED)")
    for ambiguous in ("grep --c '^[+-]$'", "grep --co '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{ambiguous}`\n") == [], (
            f"{ambiguous!r} is AMBIGUOUS between `--count`, `--color` and `--context` and grep "
            "refuses the command, so reading it as a count would grade an invalid command "
            "(#13995, MEASURED)")

    assert _d5_count_prescriptions("a count: `grep -c --max-c 2 '^[+-]$'`\n") == ["^[+-]$"], (
        "`--max-c 2` is `--max-count 2` (measured), so its `2` is the flag's ARGUMENT and the "
        "quoted token is the expression — reading the `2` instead was a false PASS (#13995)")

    for suppressed in ("grep -c --files-with-matches '^[+-]$'", "grep -c --quiet '^[+-]$'",
                       "grep -c --silent '^[+-]$'", "grep -c --quie '^[+-]$'",
                       "grep -c --files-with-match '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{suppressed}`\n") == [], (
            f"{suppressed!r} prints a FILENAME or nothing (measured on GNU grep 3.11) — it counts "
            "nothing, so it is not a prescription and must not be graded (#13995, MEASURED)")
    # ⛔ THE REFUSAL IS THE LENGTH TEST ALONE, AND THE PROSE CREDITED A CLAUSE THAT COULD NEVER
    # FIRE (#13995 review, MEASURED). `_suppresses_the_count` carried
    # `len(candidates) == 1 and not "--file".startswith(name)`; for every prefix of `--file` the
    # set already yields BOTH `--files-*` members, so the `and` was never consulted — `--file`
    # itself matches no suppressor. The rows below pin the outcome a refactor must keep, and
    # `grep --f`/`--fi`/`--fil` really are AMBIGUOUS (measured: `option '--f' is ambiguous;
    # possibilities: '--fixed-regexp' '--file' '--files-with-matches' '--files-without-match'`).
    assert not any(_suppresses_the_count(word) for word in (
        "--files", "--f", "--fi", "--fil", "--file")), (
        "`--files`/`--f`/`--fi`/`--fil` are AMBIGUOUS in grep (measured: `option '--fil' is "
        "ambiguous; possibilities: '--file' '--files-with-matches' '--files-without-match'`) and "
        "`--file` is an option of grep's that takes an ARGUMENT (measured: `option '--file' requires "
        "an argument`), so none may be certified as a suppressor — the test is uniqueness across "
        "the set, not membership of one name, and it does not need (and cannot use) a `--file` "
        "clause (#13995; #14027 round 4, P3 — this message said grep refused ALL FIVE as ambiguous, "
        "which is false for `--file`)")
    assert [_suppresses_the_count(word) for word in ("--quie", "--s", "--files-with-match")] == [
        True, True, True], (
        "the suppressor set is still certified where grep accepts the spelling (measured: "
        "`--quie` is `--quiet`, `--s` is `--silent`, `--files-with-match` is "
        "`--files-with-matches`) — a narrowing that loses these reds here instead of silently "
        "ungrading the six rows above")

    for inverted in ("grep -c --invert '^[+-]$'", "grep -c --inver '^[+-]$'"):
        assert _d5_count_prescriptions(f"a count: `{inverted}`\n") == ["^[+-]$"], (
            f"{inverted!r} IS the long spelling of the inversion (measured: the complement is "
            "printed), so the command must stay seen (#13995, MEASURED)")
        assert _d5_blank_class_prescriptions(f"a count: `{inverted}`\n") == [], (
            f"{inverted!r} counts the COMPLEMENT, so it must not be graded against the positive "
            "class — grading it reds a document that prescribes both (#13995 round 4, long "
            "spelling in the word walk)")


def test_the_d5_count_reader_does_not_grade_a_complement_or_suppressed_count_as_this_class():
    """#13995 review round 4 — a count is graded on WHAT IT COUNTS, not on `c` alone.

    ⛔ MEASURED ON THE PRE-FIX READER, BOTH DIRECTIONS (GNU grep 3.11). `_requests_a_count`
    answered `True` to every short cluster holding a `c`, which is false in two ways:

      * `-l`, `-L` and `-q` WIN over `-c` — `-lc` prints the FILENAME, `-Lc` prints the files
        WITHOUT a match, `-qc` prints nothing. No count, yet each was read as a count command.
      * `-v` INVERTS the match set, so `grep -vc '^[+-]$'` counts the NON-blank lines — a
        DIFFERENT class — yet it was graded as a BLANK-class prescription. A document that
        legitimately prescribes the blank count AND its complement therefore made
        `len(blank_prescriptions) == 2`, and `_d5_blank_count_expression` redded a CORRECT
        document with "so this arm would execute one of them while a reader follows another",
        which is false in that case — the false RED on a correct document this file calls the
        worse direction.

    Non-vacuity: making `_requests_a_count` ignore `_D5_COUNT_SUPPRESSING_SHORT_FLAGS` reds every
    row in the first loop; dropping the flags from the graded filter
    (`_d5_blank_class_prescriptions` → the expression-only list) reds every row in the second loop
    and the control below.
    """
    # (1) A word carrying a count-SUPPRESSING flag is not a count request at all — `-c` is there,
    # and it does not win. One measured spelling per suppressor, plus the order-reversed pair.
    for suppressed in (
        "grep -lc '^\\+$'",            # `-l` wins: prints the filename
        "grep -Lc '^\\+$'",            # `-L` wins: prints the files WITHOUT a match
        "grep -qc '^\\+$'",            # `-q` wins: prints nothing
        "grep -cl '^\\+$'",            # …same command, cluster ordered the other way
        "grep -c -q '^\\+$'",          # …same, flags split
        "grep --count -l '^\\+$'",     # …same, long count form beside the suppressor
    ):
        assert _d5_count_prescriptions(f"a prescription: `{suppressed}`\n") == [], (
            f"{suppressed!r} does not COUNT anything — `-l`/`-L`/`-q` win over `-c` (measured on "
            "GNU grep 3.11) — so it is not a count prescription the reader may report, and it "
            "certainly cannot be graded as this class (#13995 review round 4, P2)")

    # (2) An INVERTED count IS a count, and it counts the COMPLEMENT — so the reader must SEE it
    # and must NOT grade it against the positive class. Both halves are asserted: dropping it from
    # the seen set would hide the command, and grading it would red a correct document.
    for inverted in (
        "grep -vc '^[+-]$'",              # `-v` clustered with `c`
        "grep -cv '^[+-]$'",              # …same command, cluster ordered the other way
        "grep -c -v '^[+-]$'",            # …same, flags split
        "grep -Evc '^[+-]$'",             # …same, beside another short flag
        "grep -c --invert-match '^[+-]$'",  # …same, the long spelling
    ):
        assert _d5_count_prescriptions(f"a prescription: `{inverted}`\n") == ["^[+-]$"], (
            f"{inverted!r} IS a count command, so the reader must see it — the exclusion from "
            "the graded set is what handles the inversion, not an omission from the seen set "
            "(#13995 review round 4, P2)")
        assert _d5_blank_class_prescriptions(f"a prescription: `{inverted}`\n") == [], (
            f"{inverted!r} counts the COMPLEMENT of `^[+-]$`, i.e. a different class — counting "
            "it as a BLANK-class prescription would red a document that prescribes the blank "
            "count and its complement together (#13995 review round 4, P2)")
        # …and the exclusion's RESIDUAL, stated so nothing here claims more than it does: the graded
        # set never sees a `-v` count, so a command that counts the blank lines by inverting the
        # NON-blank class (`grep -vc '^[+-][^+-]'`) is not graded here either. That is a shape this
        # arm does not read — it is pinned, not glossed, so a future widening reds here.
        assert _d5_blank_class_prescriptions(
            "a prescription: `grep -vc '^[+-][^+-]'`\n") == [], (
            "an inverted count is never graded against the positive class — which means the one "
            "inverted spelling that WOULD count the blank lines is unread as well. Keep that "
            "declared in the helper's docstring rather than implying the exclusion is lossless "
            "(#13995 review round 4, P2)")

    # (3) THE CONTROL — a document MAY carry both, and the arm still sees exactly ONE BLANK-class
    # prescription. This is the false RED the fix removes, asserted as an outcome rather than as a
    # rewrite of the predicate.
    both = (
        "- BLANK lines: `git diff origin/main...HEAD | grep -c '^[+-]$'`\n"
        "- and the complement: `git diff origin/main...HEAD | grep -vc '^[+-]$'`\n")
    assert _d5_count_prescriptions(both) == ["^[+-]$", "^[+-]$"], (
        "both prescriptions are counts and both must be SEEN")
    assert _d5_blank_class_prescriptions(both) == ["^[+-]$"], (
        "a document prescribing the blank count AND its complement must yield exactly ONE "
        "BLANK-class prescription — two would red it with 'so this arm would execute one of "
        "them while a reader follows another', which is false for a complement count "
        "(#13995 review round 4, P2 — the worse direction is a false RED on a correct document)")

    # …and the LONG inversion spelling's ambiguous prefixes are UNREAD, which is the reason the
    # helper needs no rival list (#13995 review, MEASURED): `grep --i`/`--in` are ambiguous in grep
    # (exit 2), and `_long_option_is_accepted` REFUSES both — 4 and 3 candidates in grep's whole
    # set — so `_d5_command_expression` declines the invocation before the inversion is ever asked.
    # ⛔ THE CAUSE IS NAMED, NOT APPROXIMATED (#14027 review round 2, P3): this comment used to say
    # `_resolved_long_option` reads both as `--include` and eats the next word as its argument,
    # which is false at this head (it returns `None`). Pinned as `[]`, so a change that makes the
    # row read cannot happen quietly.
    for refused_and_unread in (
        "grep -c --i '^[+-]$'",
        "grep -c --in '^[+-]$'",
    ):
        assert _d5_count_prescriptions(f"a prescription: `{refused_and_unread}`\n") == [], (
            f"{refused_and_unread!r} must stay UNREAD: grep refuses the ambiguous prefix (measured) "
            "and `_long_option_is_accepted` declines it before any flag is judged — that is why "
            "`_inverts_the_match_set` needs no rival list, and a resolution change that makes this "
            "row read has to restate the reason in its docstring")
    # …and the prefixes grep DOES accept are the control, read as the inversion.
    for accepted_inversion in ("grep -c --inv '^[+-]$'", "grep -c --inver '^[+-]$'"):
        assert _d5_count_prescriptions(f"a prescription: `{accepted_inversion}`\n") == [
            "^[+-]$"], (
            f"{accepted_inversion!r} IS the long spelling of the inversion (measured: the "
            "complement is printed), so the command must stay SEEN")
        assert _d5_blank_class_prescriptions(
            f"a prescription: `{accepted_inversion}`\n") == [], (
            f"{accepted_inversion!r} counts the COMPLEMENT, so it must not be graded against the "
            "positive class (#13995 round 4, long spelling in the word walk)")


def test_the_d5_count_reader_does_not_grade_a_command_whose_pattern_is_NOT_A_REGEX():
    r"""#13995 review — `-F`/`-z` keep the count and change WHAT IT MATCHES, so a test that reads the
    EXPRESSION cannot answer whether this class was counted.

    ⛔ MEASURED ON GNU grep 3.11, on a probe diff whose two blank lines are exactly `+` and `-`:
    `grep -c '^[+-]$'` prints `2`; `-Fc` (the pattern is LITERAL TEXT, so `^[+-]$` matches the
    literal seven characters and nothing in the diff) and `-zc` (a record ends at a NUL byte, so
    the whole file is ONE line) print `0`; `--fixed` and `--null-d` print `0` too, while `-i`,
    `-w`, `-x`, `-E`, `-G`, `-P` and `--null` print `2`. Grading a `0` as "exactly the lines that
    are one `+` or one `-`" is the same false claim the `-v` exclusion exists to prevent, one axis
    over: the command IS a count and the expression IS the class literal, and only the FLAGS say
    the literal is no longer a regex.

    Non-vacuity: dropping `_retargets_the_matched_text` from `_d5_blank_class_prescriptions` reds
    every row of the first loop (measured: `1 failed` on that mutation); adding `-w` to
    `_D5_RETARGETING_SHORT_FLAGS` reds the `-wc` control row. ⛔ NOT `--null`: adding it to
    `_D5_RETARGETING_LONG_FLAGS` reds NOTHING, because `_D5_RETARGETING_LONG_RIVALS` neutralises
    it — the RIVAL LIST is doing that work, not the table, and the control row stays green
    either way (#13995 review, P3 — MEASURED).
    """
    for retargeted in (
        "grep -Fc '^[+-]$'",                  # the pattern is literal text
        "grep -c -F '^[+-]$'",                # …same, flags split
        "grep -cF '^[+-]$'",                  # …same, attached to the count
        "grep --fixed-strings -c '^[+-]$'",   # …the long spelling
        "grep --fixed -c '^[+-]$'",           # …and its accepted abbreviation (measured `0`)
        "grep -zc '^[+-]$'",                  # a NUL-terminated record: the diff is ONE line
        "grep -c --null-data '^[+-]$'",       # …the long spelling
        "grep -c --null-d '^[+-]$'",          # …and its accepted abbreviation (measured `0`)
    ):
        assert _d5_count_prescriptions(f"a prescription: `{retargeted}`\n") == ["^[+-]$"], (
            f"{retargeted!r} IS a count command, so it must be SEEN — the exclusion from the "
            "graded set is what handles the retargeting, not an omission from the seen set")
        assert _d5_blank_class_prescriptions(f"a prescription: `{retargeted}`\n") == [], (
            f"{retargeted!r} does not count the BLANK class (measured `0` where the row's own "
            "command prints `2`), so grading it as the class would let a row re-spell its "
            "instrument while this arm reported the class as graded (#13995 review, MEASURED)")
    # …and the CONTROL, measured on the same probe: every one of these leaves the class intact and
    # must still be graded. `--null` is here on purpose — it is the long prefix that LOOKS like
    # `--null-data` and is not (`-Z`: a NUL after the FILE name, `2` on the probe).
    for control in (
        "grep -Ec '^[+-]$'", "grep -ic '^[+-]$'", "grep -wc '^[+-]$'", "grep -xc '^[+-]$'",
        "grep -Gc '^[+-]$'", "grep -Pc '^[+-]$'", "grep -c --null '^[+-]$'",
    ):
        assert _d5_blank_class_prescriptions(f"a prescription: `{control}`\n") == ["^[+-]$"], (
            f"{control!r} prints `2` on the same probe as the row's own command (measured), so it "
            "counts THIS class and the retargeting exclusion must not reach it")
    # …and the one BOUNDARY the helper declares, pinned where it is measurable: `--f`/`--fi` are
    # AMBIGUOUS in grep (`option '--fi' is ambiguous; possibilities: '--fixed-regexp' '--file'
    # '--files-with-matches' '--files-without-match'`, measured, rc 2) and
    # `_long_option_is_accepted` refuses both spellings (4 candidates each) — so the invocation is
    # DECLINED before any flag is judged, which is why the helper's over-inclusion changes nothing
    # today. ⛔ THIS SAID `_resolved_long_option` reads them as `--file` and makes the expression its
    # argument; that is false at this head (it returns `None`), corrected in #14027 round 2.
    for ambiguous_prefix in ("grep -c --fi '^[+-]$'", "grep -c --f '^[+-]$'"):
        assert _d5_count_prescriptions(f"a prescription: `{ambiguous_prefix}`\n") == [], (
            f"{ambiguous_prefix!r} must stay UNREAD — `_long_option_is_accepted` refuses the "
            "ambiguous prefix, so the invocation never reaches a flag; if this row reads, the "
            "helper's declared over-inclusion became observable and its docstring has to say so")
    # ⛔ EVERY LONG SPELLING IN THE RETARGETING TABLE MUST BE ONE THE ACCEPTANCE ORACLE ACCEPTS,
    # or the member is UNREACHABLE and nothing witnesses it (#13995 final-head review, P2 —
    # MEASURED). This is the invariant, not an example: `--fixed-regexp` used to sit in the table
    # while `_long_option_is_accepted` declined it (grep accepts the alias, but `grep --help`
    # does not document it, so it is absent from `_d5_all_long_options()` deliberately — listing it
    # would make `--fixed-` falsely ambiguous and decline a command grep RUNS). A member the walk
    # can never reach is the inert-narration defect this file bans, and its mutant was silent:
    # dropping it from the table left the whole suite green. Re-adding it — or any other declined
    # spelling — reds HERE by name.
    for retargeting_flag in sorted(_D5_RETARGETING_LONG_FLAGS):
        assert _long_option_is_accepted(retargeting_flag), (
            f"{retargeting_flag!r} is in `_D5_RETARGETING_LONG_FLAGS` but "
            "`_long_option_is_accepted` REFUSES it, so `_retargets_the_matched_text` can never "
            "see it — `_d5_command_expression` declines the invocation first. Delete the member "
            "and state the residual, or teach the acceptance oracle the spelling "
            "(#13995 final-head review, P2)")
    # …and the residual that DEAD MEMBER left behind, pinned so it is a stated shape rather than
    # an implied claim of closure: grep ACCEPTS `--fixed-regexp` / `--fixed-r` / `--fixed-re`
    # (measured: all three print `0`, i.e. `-F` semantics, so none counts this class) and this
    # reader DECLINES them, because the alias is undocumented in `grep --help`. Fail-closed, and
    # pinned as such — ALL THREE, because the paragraph that describes this residual NAMES three and
    # a row set of two left `--fixed-re` unpinned while the prose implied it was covered (#14027
    # review, P3 — MEASURED: `_d5_count_prescriptions` answers `[]` for all three).
    for alias_spelling in ("grep -c --fixed-regexp '^[+-]$'", "grep -c --fixed-r '^[+-]$'",
                           "grep -c --fixed-re '^[+-]$'"):
        # ⛔ `--fixed-re` is NOT decoration: it is the THIRD spelling the residual paragraph
        # NAMES, so a two-row loop leaves that paragraph claiming three witnesses with two — the
        # defect the previous cycle found. ⛔ AND IT IS NOT A BOUNDARY, which this comment used to
        # call it ("the longest prefix that is still an alias"): measured on GNU grep 3.11,
        # `--fixed-reg`, `--fixed-rege` and `--fixed-regex` are ALL accepted as the same alias and
        # print `0`, while `--fixed-regexx` is `unrecognized option` — a contiguous accepted range,
        # of which this row is one sample. The row's reason is the three-name claim, not a limit.
        assert _d5_count_prescriptions(f"a prescription: `{alias_spelling}`\n") == [], (
            f"{alias_spelling!r} is now READ — grep accepts it as `-F` and it prints `0` on the "
            "blank-class probe, so if the reader sees it, `_retargets_the_matched_text` must "
            "also exclude it or the row gets graded as a class it does not count "
            "(#13995 final-head review, P2)")


#: The prose claims about the count reader that #13995's reviews retired, each paired with the
#: PRE-FIX text it was cut from. Every needle is assembled from fragments so that it appears
#: contiguously in the pre-fix snippet and NEVER in this file's own source — otherwise the ban row
#: below would match the ban itself. ⛔ A NEW PROSE GUARD ABOUT THIS READER BELONGS HERE IN THE SAME
#: COMMIT: the cell can only ban sentences it is told about, and three separate rounds have now
#: found a FRESH over-claim in new words beside a ban that only knew the old ones.
_D5_RETIRED_OVERCLAIMS = (
    ("prescription in the bundle, "  "whatever command prefix",
     "#: ANY `grep -c…` prescription in the bundle, "  "whatever command prefix and whatever "
     "sentence"),
    ("EXPRESSION is "  "prefix-independent",
     "#: EXPRESSION is "  "prefix-independent and refuses only what the class is"),
    ("count command in `bundle`, "  "whatever its spelling",
     '    """The EXPRESSION of every `grep` count command in `bundle`, '  'whatever its spelling.'),
    # #13995 review, P2 — the ZERO-vs-MANY comment claimed the gate counts a prescription "whatever
    # their flag spelling" while the artifact beside it listed seven flag spellings it does not
    # count. Measured at `de79fcdb53`: `grep '^\+$' -c` and `grep -c -m 2 '^\+$'` both returned `[]`.
    ("COUNTS THEM "  "WHATEVER",
     "# ⛔ THE GATE ABOVE COUNTS PRESCRIPTIONS ANYWHERE IN THE BUNDLE, AND IT COUNTS THEM "
     "WHATEVER"),
    # #13995 review, P3 — the helper docstring named two spellings and said they were all pinned,
    # while neither string occurred in the cell it named (measured with `grep -n`).
    ("every one of them "  "is pinned",
     "and every one of them "  "is pinned in"),
    # #13995 review, P2 — `_suppresses_the_count`'s docstring claimed an attached value makes it
    # answer `True` and that the spelling is "already listed as unread"; measured, `-f/tmp/lpats`
    # answers `False` and `grep -c -f/tmp/pats '^[+-]$'` was graded, not unread.
    ("keeps the reader "  "on the side of NOT grading it",
     "and answering it `False` here "  "keeps the reader "  "on the side of NOT grading it"),
    # #14027 review round 6, P3 — three sites named `-e`/`--regexp` among the spellings the count
    # reader CANNOT see, while the reader reads and grades them (`grep -c -e '^[+-]$'` yields
    # `['^[+-]$']` and grep really counts `2`). A fixer following the ZERO message would look for a
    # second prescription that cannot be there. Banned by sentence so the fourth copy cannot land.
    ("a pattern supplied on "
     "`-e`/`-f`",
     "(an unquoted expression; a pattern supplied on "  "`-e`/`-f`)"),
)


def _comment_block_above(source: str, anchor: str) -> str:
    """The contiguous run of comment lines sitting immediately above `anchor` in `source`.

    ⛔ SCOPED ON PURPOSE (#13995 review round 4, P2 — MEASURED). The shape sentence below is
    asserted PRESENT so that deleting it cannot pass, and a whole-file search cannot carry that:
    the needle is also a plain literal in the assertion that checks it, so the row was satisfied
    by its own source text and a mutant that deleted the sentence from the constant's header
    stayed GREEN (agent-infra#3442 rule 3 — an assertion whose value cannot vary under the
    mutation it claims to guard). Handing the check only the comment block that IS the header
    leaves the needle one possible location: the region under test.
    """
    lines = source.splitlines()
    at = next(i for i, line in enumerate(lines) if line.startswith(anchor))
    first = at
    while first and lines[first - 1].lstrip().startswith("#"):
        first -= 1
    return "\n".join(lines[first:at])


def test_the_d5_count_reader_does_not_describe_itself_as_complete():
    """#13995 — the reader's prose may not promise more than its own residual list gives.

    ⛔ NON-VACUOUS BY CONSTRUCTION, which is why a prose finding gets a cell at all. Each needle
    below is a substring of the PRE-FIX SNIPPET recorded beside it in `_D5_RETIRED_OVERCLAIMS`, and
    both checks are made against THAT SNIPPET and this file's own source — never against a named
    commit, because the tuple does not record one per row: the rows were cut from successive review
    rounds whose texts live on `origin/main` and on earlier states of this branch, and the round
    comment above a row names its round only where one was written. ⛔ NEITHER A COUNT NOR A
    PER-COMMIT MAP IS STATED HERE ON PURPOSE (#14027 round 7, P3 — this sentence used to say
    "measured: four of the six needles do not occur in `git show origin/main:…`", and appending a
    needle made both integers false; its replacement then said the provenance "is beside each row",
    which does not resolve either, since not every row carries a round comment). A hardcoded
    census inside the cell whose subject is "a claim that outruns its own artifact" is that same
    defect one tuple entry later. The ban therefore REDS on
    code a review round actually graded — it is not a test written beside a wording edit, it IS the
    wording edit asserted. The
    checks run in the control direction first: every needle must still be found in the pre-fix
    snippet it was cut from (a typo'd needle is an inert ban, and an inert ban is the failure
    this file bans elsewhere in the document).

    The defect: the constant's header claimed ANY count prescription regardless of how it was
    prefixed and worded, the same block called the reader prefix-independent eleven lines above a
    paragraph opening "BECAUSE … WAS NOT", and `_d5_count_prescriptions`'s docstring (the first
    thing a fixer reads about the function) claimed every count command regardless of spelling —
    while the reader does not read the shapes its own residual block declares. One guard, three
    present-tense completeness claims refuted in its own block, and this file's rule
    (`_typed_answers_to_tortoise_cmd`'s closing sentence) is that a guard may be narrow but may
    not describe itself as complete. #13962's category-A argument is that a lane reads such a
    sentence as a guarantee and does not check — so the sentence is the defect, not a nit.
    Rounds 5-6 added three more, in new words each time: a "whatever their flag spelling" the
    residual list refutes, an "every one of them …" claim the named cell does not carry, and a
    residual paragraph true of an INVALID command and false of the valid one it names.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    for needle, pre_fix in _D5_RETIRED_OVERCLAIMS:
        assert needle in pre_fix, (
            f"retired-over-claim needle {needle!r} is not in the pre-fix snippet {pre_fix!r} — "
            "the row is inert and would let the completeness claim back in (#13995 review, P2)")
        assert needle not in source, (
            f"`tests/test_docs_citation_refs.py` states {needle!r} again. That is a completeness "
            "claim about the count reader, and the reader does not read the shapes its own "
            "residual block declares — so the claim is false, and it is precisely the "
            "false-PASS guarantee #13962 was filed against. State the SHAPE instead: "
            "`-`-anchored flag words followed by a quoted expression "
            "(#13995 review, P2)")
    # …and the replacement must be PRESENT, so this cannot pass by deleting both halves — the
    # shape sentence lives once on the constant and once in the helper's own docstring, and each
    # is checked IN ITS OWN REGION (#13995 review round 4, P2 — MEASURED). The previous cut searched
    # the WHOLE file for both needles; the constant's needle is a plain literal ~450 lines below the
    # header it guards, so it also occurred in this assertion's own tuple and the row was satisfied
    # by its own source text — deleting the sentence from the constant's header left it GREEN
    # (agent-infra#3442 rule 3). Scoping each haystack to the region under test makes the needle's
    # only possible location the region it grades.
    for where, haystack, shape_claim in (
            ("the constant's header",
             _comment_block_above(source, "_D5_GREP_INVOCATION_RE = re.compile("),
             "`-`-anchored flag words are followed by a quoted expression"),
            ("the helper's docstring",
             _d5_count_prescriptions.__doc__ or "",
             "`-`-anchored flag words\n    are followed by a quoted expression")):
        assert shape_claim in haystack, (
            f"the shape sentence that replaced the retired over-claims is gone from {where} — a "
            "deletion is not a fix; the reader's scope must be stated as the SHAPE it reads "
            "(#13995 review, P2)")


def test_the_comment_filter_is_not_described_by_a_property_its_input_cannot_have():
    """The `#`-only grep was described as something the filter cannot be (#13853 round 16).

    ⛔ MEASURED ON THE PRE-FIX TREE, which is why this is a pin and not a note. The cell
    said the stage "drops only lines whose first non-space character is `#`" — but a line
    reaching it has already passed `grep -E '^[+-][^+-]'`, so its first non-space character
    is `+` or `-` and NEVER `#`. It concluded that "a CODE line and a DOCSTRING line both
    survive it and would print", and therefore that "an empty result … means no changed
    `tortoise/` line is anything but a `#` comment" — while a docstring line whose own text
    opens with `#` is dropped exactly like a comment, so emptiness does not mean comment.
    Two sentences of that cell stated it; only the first was among the quoted three.

    Three things are asserted: the document states none of the retired descriptions, each
    needle still matches itself (a typo'd row is an inert ban, and the injection loop is
    what catches that), and the MECHANISM the replacements claim — the real greps drop a
    `#`-opening docstring line and print a code line — actually holds.
    """
    bundle = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
              / "05-verify.md").read_text(encoding="utf-8")
    assert _false_filter_descriptions(bundle) == [], (
        "05-verify.md restates a description of the `#`-only grep that the filter's own "
        f"input refutes: {_false_filter_descriptions(bundle)!r}. The stage drops a changed "
        "line whose first non-space character AFTER the diff marker is `#`, which covers "
        "`#` comments AND docstring lines opening with `#`; an empty result therefore "
        "proves the lines OPEN with `#`, never that they are comments (#13853 review "
        "round 16, P2-1 and P2-2)")

    # (1b) THE SHAPE of the property, not just the rows — see `_FALSE_FILTER_PROPERTY_RE`.
    assert _false_filter_property_restatements(bundle) == [], (
        "05-verify.md states the `#`-only filter's drop test as a character being `#` without "
        f"naming the diff marker: {_false_filter_property_restatements(bundle)!r}. That "
        "character is always `+` or `-` at that stage; the marker-qualified form is the true "
        "one. If this is an honest paraphrase, name the marker (#13853 review round 17, P2-2; "
        "round 18, P2)")
    # ...and the shape arm must be able to FIRE, on every spelling of the false property a
    # review has MEASURED — the row list alone left the re-worded `non-blank` form green, and
    # round 19 measured six more. Each probe below is one of those, kept as a positive control so
    # a later narrowing of the pattern cannot silently re-open it.
    for probe, expected in (
            ("… whose first non-space character is `#` — a comment …",
             "character is `#`"),
            ("… whose first non-blank character is `#` — a comment …",
             "character is `#`"),
            ("… whose first non-space character equals `#` …",
             "character equals `#`"),
            ("… whose first non-space character is equal to `#` …",
             "character is equal to `#`"),
            ("… whose first non-space char is `#` …",
             "char is `#`"),
            ("… whose first non-blank character is a `#` …",
             "character is a `#`"),
            ("… whose first non-blank character is # …",
             "character is #"),
            ("… whose first character after its leading spaces is `#` …",
             "character after its leading spaces is `#`"),
            # ⛔ THE CASE AXIS NEEDS ITS OWN CONTROL (#13948 review round 25, P2 — MEASURED).
            # Every probe above is lowercase, so recompiling the identical pattern with `re.S`
            # alone — i.e. REVERTING the round-24 `re.I` fix — left this whole cell GREEN, while
            # the comment above claimed the probes stop exactly that. This probe reds without it.
            ("… whose first non-space Character is `#` — a comment …",
             "Character is `#`"),
            # ⛔ THE MARK AXIS (#13948 review round 29, P2 — MEASURED). These three were ESCAPES
            # at the previous head: a bold or quoted `#` put the false property back while the
            # whole file reported `1 failed, 23 passed` — baseline — and nothing reded. The tail
            # accepted only whitespace and one backtick while the comment claimed "backticked or
            # not", so the over-claim and the hole were the same defect. Each probe is a separate
            # mark, so a narrowing back to `\s*`?#`?` reds all three.
            ("… whose first non-space character is **`#`** — a comment …",
             "character is **`#`**"),
            ("… whose first non-space character is '#' — a comment …",
             "character is '#'"),
            ("… whose first non-space character is \"#\" — a comment …",
             "character is \"#\""),
            # ⛔ AND THE COMPATIBILITY LOOKALIKE (#13948 review round 30, P2 — MEASURED): a
            # fullwidth `＃` is `#` on screen and was invisible to this arm, because the pattern's
            # `#` is a literal codepoint and the haystack was never normalised. `_fold_unicode`
            # makes NFKC the arm's input, so every fullwidth/compatibility spelling is graded.
            ("… whose first non-space character is ＃ — a comment …",
             "character is #"),
            # ⛔ AND PADDED LAYOUT, at the size real markdown produces (#13948 review round 32,
            # P2 — MEASURED). At the old `{0,8}` budget the effective room was SEVEN spaces, so an
            # aligned table cell or an eight-space list continuation re-inserted the false property
            # green. Both forms are below; each reds if the bound is narrowed back to single
            # digits.
            ("… whose first non-space character is" + " " * 9 + "`#` — a comment …",
             "character is" + " " * 9 + "`#`"),
            ("… whose first non-space character is\n\n        `#` — a comment …",
             "character is\n\n        `#`"),
            # ⛔ AND BEYOND ANY BUDGET (#13948 review round 33, P2 — MEASURED): at `{0,400}` a
            # four-hundred-space pad returned `[]`. The run is unbounded now, so these two reds if
            # anyone reintroduces a finite bound.
            ("… whose first non-space character is" + " " * 400 + "`#` — a comment …",
             "character is" + " " * 400 + "`#`"),
            ("… whose first non-space character is" + " " * 2000 + "`#` — a comment …",
             "character is" + " " * 2000 + "`#`"),
            # ⛔ AND THE COPULA'S OWN LAYOUT (#13948 review round 34, P1 — MEASURED): a tab or a
            # re-wrap INSIDE `is equal to` / `is a` escaped while the copula was spelled with
            # literal spaces.
            ("… non-space character\tis\tequal to `#` — a comment …",
             "character\tis\tequal to `#`"),
            ("… character is\r\nequal to `#` — a comment …",
             "character is\r\nequal to `#`"),
            # ⛔ A FORMAT CHARACTER STANDING IN FOR A SPACE (#13948 review round 35, P1 —
            # MEASURED). Deleting the Cf joined `is`+`equal` into `isequal` and the arm returned
            # `[]`; the Cf-as-space folding is what catches it, so this probe reds if that second
            # haystack is dropped.
            ("… character is\u200bequal to `#` — a comment …",
             "character is equal to `#`"),
            ("… character\u200bis `#` — a comment …",
             "character is `#`"),
            # ⛔ AND A MARK INSIDE THE COPULA (#13948 review round 35, P2 — MEASURED): a hyphen or
            # a non-breaking hyphen in `is equal to` — what a paste from a word processor yields —
            # escaped while the copula required plain whitespace.
            ("… character is equal-to `#` — a comment …",
             "character is equal-to `#`"),
            ("… character is equal\u2011to `#` — a comment …",
             # NFKC folds U+2011 NON-BREAKING HYPHEN to U+2010 HYPHEN, and the arm grades the
             # FOLDED haystack — so the expectation is U+2010, which is what a reader sees.
             "character is equal\u2010to `#`")):
        assert _false_filter_property_restatements(probe) == [expected], (
            "the shape ban no longer matches the property it exists to retire when it is "
            f"RE-WORDED ({probe!r} → {_false_filter_property_restatements(probe)!r}), so a "
            "restatement would pass it (#13853 review rounds 17/18/19)")
    # ⛔ AND THE ESCAPES THAT ARE *DECLARED*, pinned as escapes so this text cannot drift into
    # claiming class closure again: a restatement that re-words the NOUN or COPULA, or that only
    # MENTIONS `marker` (a decoy, not the qualifier), is NOT detected. Round 19 measured these;
    # naming them is the honest half, and the ledger + review are what cover them.
    for escape in (
            "… whose leading printable glyph amounts to `#` …",
            "… whose first non-space character (the marker word appears as a decoy) is `#` …",
            # ⛔ THE OTHER HALF OF THE ROUND-24 PIN (#13948 review round 25, P2 — MEASURED).
            # Without these two, reverting `markers?` to `marker` OR dropping `re.I` from the
            # MARKER side also left the cell green, so the flag was pinned on one axis only.
            "… whose first non-space character after the diff Marker is `#` …",
            "… whose first non-space character after the markers is `#` …",
            # ⛔ THE DECLARED LOCALITY RESIDUAL (#13948 review round 34, P2): the noun -> copula
            # window is 400 characters on purpose, so a pad BEYOND it is an escape rather than a
            # silent claim of closure. Pinned here so the prose above cannot drift into claiming
            # the axis is closed everywhere.
            "… character" + " " * 401 + "is equal to `#` …"):
        assert _false_filter_property_restatements(escape) == [], (
            f"the arm now catches {escape!r} — good, but then the DECLARED-ESCAPE list above "
            "is stale and must say so (#13853 review round 19)")
    # ⛔ AND THE WINDOW IS A CONTROL TOO, not just a number in the pattern: round 20 measured that
    # narrowing `{0,400}` back to `{0,200}` left every other control green, so the 200-char escape
    # round 19 fixed could have been re-opened silently. This probe puts ~240 characters after the
    # noun (the assert bounds the COPULA'S INDEX from the string start, not the gap itself, so it
    # stays meaningful if the prefix is edited); it must still be caught.
    wide_gap = "… whose first non-space character" + " and more words here" * 12 + " is `#` …"
    assert 200 < wide_gap.index(" is `#`") < 400, (
        f"the wide-gap probe stopped separating the 400-char window from the 200-char one "
        f"({wide_gap.index(' is `#`')} chars) — it would no longer catch a narrowing "
        "(#13853 review round 20, P2)")
    assert len(_false_filter_property_restatements(wide_gap)) == 1, (
        "the shape ban's 400-character window is not in effect: a false property stated with a "
        "~240-character gap after the noun (copula index 273) passes, which is the escape round 19 "
        "fixed and round 20 measured as unpinned (#13853 review round 20, P2)")
    # ...and the QUALIFIER keeps an honest sentence legal, which is the other half round 18
    # measured as a false red: naming the marker in any wording exempts the sentence.
    for exempt in ("… whose first non-space character **after that marker** is `#` …",
                   "… whose first non-space character (ignoring the marker) is `#` …"):
        assert _false_filter_property_restatements(exempt) == [], (
            f"the shape ban reds an honest statement of the drop test ({exempt!r}) — the "
            "qualifier is what makes it true (#13853 review round 18, P2)")

    # (2) CONTENT, not size: the tuple is pinned WHOLE. A loop over the module constant
    # iterates the MUTATED list, so deleting a row (or re-wording one by a character) left
    # the injection loop green while that description could be re-inserted into the document
    # uncaught — the measured bypass #13807 review finding 5 recorded for the membership
    # ledger, closed here the same way: pin the rows, never the count.
    #
    # ⛔ THE MODULE CONSTANT GOES ON THE LEFT, the pinned tuple on the right. Written the
    # other way round this is `SIM300` (yoda condition) and reds `main-lint`, which reddens
    # the merge ref of every open PR — measured on `main` at `bd020ff45b`, `main-lint` run
    # 44689040783: `SIM300 [*] Yoda condition detected --> tests/test_docs_citation_refs.py:2863:12`.
    # The comparison is symmetric (`_FILTER_FALSE_DESCRIPTIONS` is a tuple), so the flip is
    # semantics-preserving; the row pinning is unchanged.
    assert _FILTER_FALSE_DESCRIPTIONS == (
        "drops only lines whose first non-space character is `#`",
        "drops exactly the lines whose first non-space character is `#`",
        "a CODE line and a DOCSTRING line both survive it and would print",
        "a docstring line or a code line WOULD appear in its output and an empty result "
        "rules both out",
        "an empty result therefore means no changed `tortoise/` line is anything but a `#` "
        "comment",
        "a docstring line whose content opens with anything else passes both stages and "
        "prints",
        "a docstring or code line whose content opens with anything else WOULD appear in "
        "its output",
        "is the command that counts them at YOUR head",
    ), (
        "the `#`-filter description ledger changed. These are the exact spellings that were "
        "recorded as FALSE at a head and survived every arm, so a row that is edited, "
        "however slightly, is a ban the document can be re-inserted past. Retiring a NEW "
        "spelling means adding it; it never means re-wording one (#13853 review round 16, "
        "P2-1/P2-2; the property is #13807 review finding 5)")

    # (3) INJECTION: every needle must be reachable through the detector, or the ban has a
    # row that can never fire — "an unread row is a ban the document is re-inserted past".
    for spelling in _FILTER_FALSE_DESCRIPTIONS:
        probe = f"… and the grep {spelling} at this head …"
        assert _false_filter_descriptions(probe) == [spelling], (
            f"the ban's own row {spelling!r} is not detected when injected, so editing it "
            "(even by one character) turns this arm inert — the bypass #13807 review "
            "finding 5 recorded for the membership ledger")
    # ⛔ AND THE CASE AXIS HAS ITS OWN CONTROL (#13948 review round 27, P2 — MEASURED): every
    # probe above is lowercase, so reverting the detector to a raw `in` left the cell GREEN
    # while a sentence-initial cap on any ledger row escaped it — the same treatment the
    # `_D5_HEAD_BOUND_VERDICTS` sibling got in round 26.
    for spelling in _FILTER_FALSE_DESCRIPTIONS:
        cased = spelling[0].upper() + spelling[1:]
        assert _false_filter_descriptions(cased) == [spelling], (
            f"the ledger ban misses the capitalised form of its own row ({cased!r}), so a "
            "sentence-initial restatement of the false description passes it "
            "(#13948 review round 27)")
    # ⛔ AND THE LAYOUT AXES HAVE THEIR OWN CONTROLS (#13948 review round 28, P2 — MEASURED):
    # the same sentence RE-WRAPPED across a line break at the document's own prose width, or
    # carrying one inserted comma, restored the retired text with the whole file still at
    # baseline. Both are folded in `_norm_for_ban`, so both are pinned here: reverting the fold
    # reds these rather than leaving the cell silently green.
    for spelling in _FILTER_FALSE_DESCRIPTIONS:
        wrapped = spelling.replace(" ", "\n", 3)
        comma = spelling.replace(" is ", ", is ", 1)
        assert _false_filter_descriptions(wrapped) == [spelling], (
            f"the ledger ban misses its own row re-wrapped across a line break ({wrapped!r}) "
            "— a prose re-wrap would smuggle the retired description past it "
            "(#13948 review round 28)")
        if comma != spelling:
            assert _false_filter_descriptions(comma) == [spelling], (
                f"the ledger ban misses its own row with one inserted comma ({comma!r}) "
                "(#13948 review round 28)")
        # ⛔ AND A MARK THAT IS NEITHER WHITESPACE NOR A COMMA (#13948 review round 29): the
        # fold is total on purpose, so a fold narrowed back to `[,\s]+` must RED here. Without
        # this probe that regression left both cells GREEN (MEASURED: `2 passed`).
        assert _false_filter_descriptions(spelling.replace(" is ", " — is ", 1)) == [spelling], (
            "the ledger ban misses its own row with an em dash inserted — the fold must be total "
            "over punctuation, not comma-only (#13948 review round 29)")
        # ⛔ AND THE LOOKALIKE / INVISIBLE AXES (#13948 review rounds 30-31, both MEASURED): a
        # format character INSIDE a token split the needle (`char\u200bacter`), and one SITTING
        # WHERE THE SPACE IS (`at\u00adthis`) removed the separator — both left the arm green.
        word, rest = spelling.split(" ", 1)
        assert _false_filter_descriptions(word[:2] + "\u200b" + word[2:] + " " + rest) == [spelling], (
            "the ledger ban misses its own row with a zero-width space inside a word "
            "(#13948 review round 30)")
        assert _false_filter_descriptions(
            word + "\u00ad" + rest) == [spelling], (
            "the ledger ban misses its own row with a soft hyphen in place of a space "
            "(#13948 review round 31)")

    # (4) MECHANISM: the premise of (1), executed rather than reasoned about — and executed
    # with the patterns the DOCUMENT states, so a document that states a different filter is
    # caught here instead of staying green (#13853 review round 17, P2-1).
    patterns = _document_filter_patterns(bundle)
    assert len(patterns) == 1, (
        "05-verify.md states the `#`-only filter's two grep stages more than one way, or no "
        f"longer states them as one `grep -E … | grep -vE …` pipeline: {patterns!r}. ONE "
        "DISTINCT PAIR — the document states this pipeline three times (`:102` twice, `:147` "
        "once) and they are deduplicated here — is what lets this arm run the document's own "
        "patterns; two DISTINCT spellings would let one of them rot unseen (#13853 review "
        "round 17 P2-1; round 21 NIT)")
    include, exclude = patterns[0]
    docstring_line = "+        #11702 — an ABSENT belief contributes NO term.\n"
    code_line = "+        subject_binding = resolve_subject()\n"
    assert docstring_line.lstrip()[0] == "+", (
        "the fixture stopped carrying a diff marker, so it no longer reproduces the "
        "condition the retired description got wrong")
    assert _comment_filter(docstring_line, include, exclude) == "", (
        "the filter the document STATES no longer drops a docstring line whose own text "
        "opens with `#` — if that changed, the replacement prose in `05-verify.md` is now "
        "the wrong one")
    assert _comment_filter(code_line, include, exclude) == code_line, (
        "the filter the document states no longer prints a code line, so the replacement "
        "prose's second half is false")
    assert _comment_filter(docstring_line + code_line, include, exclude) == code_line, (
        "the two-stage pipeline is not behaving as the document describes it")
    # ⛔ THE DOCUMENT'S OWN MEASURED EXAMPLES, executed. Each one is a case where a DIFFERENT
    # but plausible pattern agrees with the canonical pair — which is why asserting only the
    # three fixtures above let `[[:space:]]*` become `[[:space:]]+` with the suite green. The
    # first line is the document's own ("printf '+#2521 (C5 #2513)' … is silent": a `#` with
    # NO space after the marker, which `[[:space:]]+#` would print).
    for payload, expected, why in (
            ("+#2521 (C5 #2513) — text\n", "",
             "a `#` immediately after the marker is dropped by the documented filter"),
            ("+\n", "", "stage 1 drops an added line that is just the marker"),
            ("++i\n", "",
             "stage 1 drops a line whose content itself opens with `+`"),
            ("--counter\n", "",
             "stage 1 drops a line whose content itself opens with `-`")):
        assert _comment_filter(payload, include, exclude) == expected, (
            f"05-verify.md's stated filter does not behave as the document says: {why}. "
            f"Patterns read from the document: include={include!r} exclude={exclude!r} "
            f"(#13853 review round 17, P2-1)")


#: Verdicts about the HEAD BEING MERGED that the D5 cell typed and the tree refuted at that
#: head. Retired BY NAME (#13853 review round 17, P1) — they came BACK with the re-cut merge,
#: which resolved `05-verify.md`'s `:134` to `main`'s side and dropped the branch's corrected
#: sentences, so this is a regression a pin would have caught and no arm did.
_D5_HEAD_BOUND_VERDICTS = (
    "does NOT hold for this head",
    # ⛔ THE HEAD QUALIFIER IS PART OF THIS ROW (#13948 review, P2). The spelling the re-cut
    # merge restored was `at this head the FIRST of them prints a path`; carrying it WITHOUT
    # `at this head` widened the ban past what was measured, so it also refused the shape this
    # document deliberately REQUIRES — a verdict stamped with the head it is true of, e.g.
    # "At `2584a86ec1` the FIRST of them prints a path." (measured: that sentence was refused,
    # with a message calling it a verdict "about the head being merged", which would be false of
    # it). A row is a MEASURED SPELLING, never a widened one — the convention the ledger above
    # this cell states. RESIDUAL, declared: a present-tense restatement that re-words the
    # clause (a different verb or noun) can still slip, which is the same bounded-token residual
    # every row here carries. ⛔ NOT declared, and CLOSED: CASE — the detector compares
    # case-insensitively, because a case-SENSITIVE test missed the one-token slip from the shape
    # this document deliberately requires (`At `2584a86ec1` the FIRST…` -> `At this head the
    # FIRST of them prints a path.`), i.e. a sentence-initial capital. That was a false PASS
    # (#13948 review round 26, MEASURED) and it is the same asymmetry round 25 closed one arm
    # away in `_FALSE_FILTER_PROPERTY_RE`; the case axis has its own control below.
    "at this head the FIRST of them prints a path",
)


def _d5_head_bound_verdicts(doc: str) -> list[str]:
    """Every retired per-head verdict still spelled in `doc` (#13853 review round 17, P1).

    ⛔ CASE-INSENSITIVE, deliberately (#13948 review round 26, P2 — MEASURED). As a plain
    substring test it missed the sentence-initial slip from the shape this document itself
    requires: `At this head the FIRST of them prints a path.` passed while
    `at this head the FIRST of them prints a path` was caught, so the cell reported GREEN
    over exactly the per-head verdict it exists to retire — the FALSE-PASS direction this
    file says must never be accepted.
    """
    lowered = _norm_for_ban(doc)
    return [spelling for spelling in _D5_HEAD_BOUND_VERDICTS if _norm_for_ban(spelling) in lowered]


def test_the_D5_row_types_no_verdict_about_the_head_being_merged():
    """Two typed per-head verdicts the re-cut merge restored, and the tree refuted (#13853 r17).

    MEASURED at `84b94ad74f` (the head that merged), against its merge base `b23528fa93` with
    `290912f303` — ⛔ the base is NAMED and the TWO-ARG form is used, because `A...B` silently
    takes `merge-base(A,B)` as its base: `290912f303` is NOT an ancestor of `84b94ad74f`, so
    spelling it as `290912f303...84b94ad74f` would print this block's `(no output)` while a
    reader reproducing the sentence with the two-arg form gets the OPPOSITE answer (5
    `tortoise/` paths) — measured:

    ```
    $ git merge-base 290912f303 84b94ad74f
    b23528fa93a2adebf6abb0183f2907f8ad9298e7
    $ git diff --name-only b23528fa93 84b94ad74f -- tortoise/
    (no output)                       # so "no runtime behaviour change" DOES hold here
    $ git diff --name-only 290912f303 84b94ad74f -- tortoise/ | wc -l
    5                                 # the answer a reader gets from the WRONG base
    $ git diff --name-only b23528fa93 84b94ad74f -- docs/epics/…/04-plan.md
    (no output)                       # so the FIRST of the two members prints NOTHING
    ```

    and the cell said the opposite of both — `does NOT hold for this head`, and "at this head
    the FIRST of them prints a path". PROVENANCE, measured: the merged `:134` is byte-identical
    to `main`'s (`git show 84b94ad74f:docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md
    | sed -n '134p' | md5sum` == the same pipeline at `e23cf75b0a`) — ⛔ the `rev:path:LINE` form
    cannot express a line, so the extraction is spelled out; the plain `rev:path` blob differs at
    both heads and only the LINE is what this row is about, while
    the branch's own `:134` carried the corrected sentences — so the conflict resolution took
    one side wholesale, and nothing in the suite read either sentence.

    Two things are asserted: the document states neither verdict, and each needle still matches
    itself (a typo'd row is an inert ban — the bypass #13807 review finding 5 recorded).
    """
    bundle = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
              / "05-verify.md").read_text(encoding="utf-8")
    assert _d5_head_bound_verdicts(bundle) == [], (
        "05-verify.md types a verdict about the head being merged, which the tree refuted at "
        f"the head that wrote it: {_d5_head_bound_verdicts(bundle)!r}. Membership, counts and "
        "runtime impact are the ANSWER OF A COMMAND at the head the reader merges — name the "
        "command, never the answer (#13853 review round 17, P1)")

    # CONTENT, not size: the tuple is pinned WHOLE, so a row that is edited (or deleted) cannot
    # leave the ban inert while the injection loop below still passes.
    assert _D5_HEAD_BOUND_VERDICTS == (
        "does NOT hold for this head",
        "at this head the FIRST of them prints a path",
    ), (
        "the per-head-verdict ledger changed. These are the exact sentences #13853's re-cut "
        "merge restored and the tree refuted; retiring a NEW spelling means adding it, never "
        "re-wording one (#13853 review round 17, P1)")

    # INJECTION: every needle must be reachable through the detector.
    for spelling in _D5_HEAD_BOUND_VERDICTS:
        probe = f"… and the claim {spelling} at this head …"
        assert _d5_head_bound_verdicts(probe) == [spelling], (
            f"the ban's own row {spelling!r} is not detected when injected, so editing it "
            "(even by one character) turns this arm inert")
    # ⛔ AND THE CASE AXIS HAS ITS OWN CONTROL (#13948 review round 26, P2 — MEASURED): every
    # probe above is lowercase, so reverting the detector to a case-SENSITIVE `in` left this
    # whole cell GREEN while the sentence-initial slip from the shape the document REQUIRES
    # (`At this head the FIRST of them prints a path.`) went uncaught. These two reds iff the
    # detector stays case-insensitive — the same treatment round 25 gave the filter arm.
    for cased in ("At this head the FIRST of them prints a path.",
                  "AND THE CLAIM DOES NOT HOLD FOR THIS HEAD."):
        assert _d5_head_bound_verdicts(cased), (
            f"the per-head-verdict ban misses the capitalised form {cased!r}, so a "
            "sentence-initial restatement passes it (#13948 review round 26)")
    # ⛔ AND THE LAYOUT AXES, the same way (#13948 review round 28, P2 — MEASURED): a re-wrap or
    # one inserted comma put the retired per-head verdict back with the whole file at baseline.
    for wrapped, comma in (("at this head the\nFIRST of them prints a path",
                            "at this head, the FIRST of them prints a path"),
                           ("and the claim does not\nhold for this head",
                            "and the claim does not, hold for this head")):
        assert _d5_head_bound_verdicts(wrapped), (
            f"the per-head-verdict ban misses {wrapped!r} re-wrapped across a line break "
            "(#13948 review round 28)")
        assert _d5_head_bound_verdicts(comma), (
            f"the per-head-verdict ban misses {comma!r} with one inserted comma "
            "(#13948 review round 28)")
    # ⛔ Non-comma, non-whitespace marks too — the fold is total, and a comma-only regression
    # must RED (#13948 review round 29).
    for marked in ("at this head — the FIRST of them prints a path",
                   "and the claim does not; hold for this head"):
        assert _d5_head_bound_verdicts(marked), (
            f"the per-head-verdict ban misses {marked!r} with a mark inserted — the fold must be "
            "total over punctuation, not comma-only (#13948 review round 29)")
    # ⛔ And the invisible/lookalike axes, as in the ledger cell (#13948 review rounds 30-31).
    for invisible in ("at this head the FIRST of them prints a pa\u200bth",
                      "at\u00adthis head the FIRST of them prints a path",
                      "at this head the FIRST of them prints a pa＃th"):
        assert _d5_head_bound_verdicts(invisible), (
            f"the per-head-verdict ban misses {invisible!r} — an invisible format character or a "
            "compatibility lookalike must be folded (#13948 review rounds 30-31)")

    # (5) THE COUNT COMMAND ITSELF, executed (#13956 review, P2). The cell names the class it
    # drops as "a diff line that is just `+` or `-`", and prescribes a count for it. The
    # spelling that stood at `a03c1dd163e` matched only blank ADDED lines: over a diff whose
    # only change is a blank REMOVED line it printed `0`, which is precisely the false
    # negative this cell exists to prevent ("a blank-line-ONLY change is invisible to it").
    # Arm (1) bans the WORDING that said so; this arm holds the instrument, because a regex
    # can narrow under a correct sentence and no text ban can see that.
    assert _blank_line_count("+\n") == 1, (
        "the document's count command no longer counts a blank ADDED line")
    assert _blank_line_count(" a\n-\n b\n") == 1, (
        "the document's count command does not count a blank REMOVED line, which is half "
        "the class the cell names (\"a diff line that is just `+` or `-`\") and the shape "
        "the superseded spelling read as `0` (#13956 review, P2)")
    # ⛔ TWO ASSERTIONS WERE DELETED HERE (#13956 review round 7, P3; then round 8, P2 raised
    # the SECOND one and was right about the reason — measured below):
    #  • `_blank_line_count("+\n-\n") == 2` was a strict CONSEQUENCE of the two assertions above
    #    — a `grep -c` counts LINES, so any expression that counts a blank `+` and a blank `-`
    #    returns 2, and under `^\+$` the REMOVED-line assertion above reds first. Its intent is
    #    still held, direction by direction, by those two.
    #  • `_blank_line_count("++i\n--counter\n+- 2)\n") == 0` was DELETED ON A REASON THAT WAS
    #    FALSE: round 7 claimed no mutation could reach it because the class gate would raise
    #    its loud ZERO first. Round 8 measured the two-edit mutant — widen the SENTENCE to
    #    `^[+-]`, append a `^\+$` decoy elsewhere in the bundle — and found the whole suite GREEN
    #    with this line REINSTATED it reds alone (`assert 3 == 0`) while the two directional
    #    assertions stay green. So it WAS reachable; what was actually missing was the BINDING
    #    in `_d5_blank_count_expression()` that the executed expression is the BLANK-class one.
    #    That binding now exists (see its assertion above), so the sentence's expression is
    #    provably exactly-one-character and NO mutation can make this line the one that reds:
    #    a widened sentence reds the binding first. The deleted line's intent is therefore held
    #    at the root, where the decoy path is closed too — not merely restated here.
    # ⛔ A dead assertion is not free: it reads as coverage the tree does not have. But neither is
    # a deleted one that was not dead — which is why the second deletion was re-measured and its
    # justification replaced with the binding rather than with an argument.


def test_the_bundles_stated_index_re_point_lands_in_the_file_it_re_points():
    """⛔ The doc said it re-pointed that row to `sdk.py:19485`. The row reads `:21496`.

    `05-verify.md` stated, as the row's re-point at the head being merged, a pair the
    tree refutes at BOTH ends: neither `sdk.py:19449` nor `sdk.py:19485` occurs in
    `docs/00_index.md` at this head or on `main`, while the diff's actual re-point is
    `sdk.py:21474` -> `:21496` (2-dot and 3-dot `--numstat` both `3 3`). It survived
    several review rounds for the same reason the census green did: the D5 arms in
    `test_7949_capstone_measure.py` scope to D5's OWN line, and this claim is in the
    file-table section, so nothing read it.

    The arm is deliberately GIT-FREE (no other leg in the suite shells out): a
    re-point's NEW side must resolve in the file at the head being merged, and its OLD
    side must NOT — which is exactly what "re-pointed" means, and it is checkable
    from the working tree alone.

    NON-VACUITY, MEASURED: on the pre-fix document the regex does not match at all
    (it stated ``re-pointing that row's `sdk.py:19449` → `:19485` at the merge head``,
    with no `cite from`), so `assert m` reds; and had it matched, the new side
    `19485` is absent from `docs/00_index.md` (`grep -c 'sdk.py:19485'` -> 0), so the
    second leg reds too.
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    # ⛔ BOTH SPELLINGS ARE GRADED, because the two sides of the merge state the SAME
    # re-point in different words and neither may lose its arm (#13802 merge): `main`
    # writes it as "re-pointing that row's `sdk.py` cite from **`:OLD`** to **`:NEW`**",
    # this branch writes it as "that row's cite moved **`:OLD`** → **`:NEW`**" — and the
    # branch's attribution arm REFUSES the first spelling by name, because a gerund
    # "re-pointing" reads as this repair's own act. Extending the matcher keeps the arm
    # discriminating on the merged document (both ends named, new side resolvable, old
    # side absent) instead of forcing one side's prose through the other's list.
    m = re.search(
        r"re-pointing that row's `sdk\.py` cite from \*\*`:(\d+)`\*\*.*?"
        r"to \*\*`:(\d+)`\*\*"
        r"|that row's cite moved \*\*`:(\d+)`\*\* → \*\*`:(\d+)`\*\*",
        doc, re.S)
    assert m, (
        "05-verify.md no longer states the index row's re-point with both ends named "
        "as `from **`:OLD`** ... to **`:NEW`**` (or `moved **`:OLD`** → **`:NEW`**`). "
        "State it with both ends named, so this arm can check the pair against the file "
        "the re-point lands in (#13489 review, cycle 5)")
    old, new = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    index = (DOCS / "00_index.md").read_text(encoding="utf-8")
    assert f"sdk.py:{new}" in index, (
        f"05-verify.md says the row's cite is re-pointed to `sdk.py:{new}`; "
        f"`docs/00_index.md` does not carry that cite at this head, so the document "
        "names a line the file it describes does not point at "
        "(#13489 review, cycle 5)")
    assert f"sdk.py:{old}" not in index, (
        f"05-verify.md calls `sdk.py:{old}` the PRE-re-point value, but "
        f"`docs/00_index.md` still carries it — either the re-point did not happen "
        "or the pair is stale (#13489 review, cycle 5)")


_TORTOISE_NAMED_HEAD = re.compile(r"(?:`[0-9a-f]{7,40}`|\b[0-9a-f]{7,40}\b|#\d{3,6})")


#: ⛔ A PROXIMITY WINDOW IS NOT A BINDING (#13802 review, round 12, P1 — MEASURED: with the
#: window alone, `Per `#10215`, at this head the `tortoise/` set is empty.` was GREEN, and so
#: was the same claim after any `#ref` within 80 characters — 21.5% of the guarded corpus
#: sits inside that window). The head must be the head the claim is ABOUT, so the allowance
#: accepts only two BOUND constructions inside ONE clause:
#:
#:   1. `at` / `on` / `in` / `as of` + a sha        -> "at `2584a86ec1` the command printed …"
#:   2. a `#PR` (or sha) followed by an EVENT verb  -> "`#12943` adds `tortoise/cache_leg.py`"
#:
#: A copula is deliberately NOT an event verb: `#10215` shipped that; at this head …` is a
#: claim about THIS head introduced by an unrelated citation, and round 12's counterexamples
#: are all of that shape.
#: ⛔ AND THE BINDING IS CASE-INSENSITIVE BECAUSE THE BAN IS (#13802, round 21 — MEASURED).
#: `_TORTOISE_SET_DIRECTION_CLAIM` compiles `re.IGNORECASE` (its own contract: "FORMATTING IS
#: NOT CONTENT … the form matched case-insensitively"), and these two constants did not — so
#: the SAME sentence was refused when its binding was spelled at a sentence start, which is
#: the most ordinary spelling of a named head in this corpus (`At `945817d22a`, the base this
#: branch was re-derived on`). Measured in-process on the round-20 head:
#:
#:   "At `2584a86ec1` the `tortoise/` set was non-empty — five paths."   -> REFUSED
#:   "at `2584a86ec1` the `tortoise/` set was non-empty — five paths."   -> allowed
#:   "`#10215`'s `8bc2fbed1` Touches no `tortoise/` file at all"          -> REFUSED
#:
#: That is a FALSE BLOCK on head-named history — the one direction this file's docstring
#: forbids — and a contract inconsistency inside one module. The widening is only the CASE of
#: the SAME bound constructions (the sha class accepts `[0-9A-F]`, which every git object id
#: spelling allows), never a third construction: measured over all 855 `docs/**/*.md`, the ban's
#: hit set is UNCHANGED by the flag (6 -> 6, 0 newly refused, 0 newly allowed, and the witness
#: test above passes untouched), so no claim that was refused on its merits is now excused.
#: NO LIVE VICTIM at the head this was written (0 case-only false blocks in the corpus) — this
#: closes the class before the next historical sentence is written in the capitalised form.
_TORTOISE_HEAD_BINDING_AT = re.compile(
    r"(?:at|on|in|as\s+of)\s+`?[0-9a-f]{7,40}`?", re.IGNORECASE)


_TORTOISE_HEAD_BINDING = re.compile(
    r"(?:(?:at|on|in|as\s+of)\s+`?[0-9a-f]{7,40}`?"
    # ⛔ AND THE `#PR` FORM REQUIRES ITS EVENT VERB TOO (round 13 review, P1 — MEASURED):
    # without this group the alternative was satisfied by ANY `#NNN` followed by whitespace,
    # so `Per `#10215` at this head the `tortoise/` set is empty.` — the pinned round-12
    # must-red witness with ONE COMMA DELETED — was excused as a historical mention and the
    # ban read green over it. The copula is still not an event verb (see above), which is
    # exactly why `#10215` + ` at this head` must not bind.
    r"|`?#\d{3,6}`?['’]?s?\s+(?:`?[0-9a-f]{7,40}`?\s+)?"
    r"(?:adds?|added|touches?|touched|lands?|landed|ships?|shipped|moves?|moved"
    r"|re-?points?|re-?pointed|re-?derives?|re-?derived|de-?types?|de-?typed"
    r"|says?|said|states?|claims?|records?|carried|derives?|renumbers?|renumbered)\b"
    r"|`?[0-9a-f]{7,40}`?\s+"
    r"(?:adds?|added|touches?|touched|lands?|landed|ships?|shipped|moves?|moved"
    r"|re-?points?|re-?pointed|re-?derives?|re-?derived|de-?types?|de-?typed"
    r"|says?|said|states?|claims?|records?|carried|derives?|renumbers?|renumbered)\b)",
    # case as well as spelling — see the measurement on `_TORTOISE_HEAD_BINDING_AT` above
    re.IGNORECASE)


class _HeadBoundBan:
    """The direction ban, minus HISTORICAL mentions, and minus FORMATTING.

    A sentence that NAMES the head it is about is not a head-bound claim about the head you
    merge: the document is allowed to say "`#10215`'s `8bc2fbed1` touches no `tortoise/`
    file", and the witness list pins the past-tense form as MUST-NOT-REFUSE. A regex
    lookbehind cannot express that (variable width), so the allowance is a post-filter on
    the match's preceding window (#13802 review, P2 — measured: three named-head emptiness
    sentences were refused by the unanchored verb-before-path alternative, one of them
    already shipped in `docs/evidence/7949-capstone/README.md`).

    ⛔ AND THE WINDOW ALONE WAS NOT ENOUGH (#13802 review, round 12, P1 — MEASURED): with a
    bare 80-char proximity test, `Per `#10215`, at this head the `tortoise/` set is empty.`
    was GREEN, and so was the same claim behind ANY `#ref` or sha within 80 characters —
    21.5% of the guarded corpus sits inside that window, so the allowance covered a fifth of
    the text it was supposed to be checking. The name must now BIND: `at`/`on`/`in` + a sha,
    a `#PR` followed by an event verb (optionally through its `'s <sha>`), or a bare sha
    followed by an event verb — read on the clause, because a clause boundary is exactly what
    separates an unrelated citation from a claim about THIS head. A copula is not an event
    verb: that is what the round-12 counterexamples all turn on.

    ⛔ AND IT TOLERATES MARKDOWN EMPHASIS, BECAUSE ITS SIBLING DOES (#13802 review, cycle 7,
    P1 — MEASURED). `_typed_answers_to_tortoise_cmd` strips `[`*_]` and matches
    case-insensitively, and this file PINS that as the contract ("FORMATTING IS NOT CONTENT …
    not 'one spelling of it'"). This ban did neither, so `Prints two paths`,
    `` `prints` the two comment-only modules ``, `**prints** the two COMMENT-ONLY modules`,
    `` prints `two` paths ``, `prints _two_ paths`, `prints Empty`/`Blank`/`Zero`/`Single`/
    `Both`/`Comment-Only` were each read by the detector and refused by no arm — two
    injected into `05-verify.md` stayed GREEN. The pattern now absorbs emphasis around the
    VERB and before the ANSWER and compiles case-insensitively; the text itself is NOT
    stripped, because the `[^`]` spans rely on backticks as delimiters (the `[*_`]{0,3}`
    classes in the pattern below are the whole of the tolerance).
    """

    __slots__ = ("_rx",)

    def __init__(self, rx):
        self._rx = rx

    def _claimed(self, m, text):
        """Is this match about the head it names? Bound construction inside one clause."""
        _window = text[max(0, m.start() - 90):m.end()]
        # `at <sha>` binds across a comma (`at `2584a86ec1`, the `tortoise/` set was empty`),
        # so it is read on the window; the attribution form is read on the clause, because
        # `Per #10215, at this head …` cites a PR and then claims something about THIS head —
        # exactly the false PASS round 12 measured.
        if _TORTOISE_HEAD_BINDING_AT.search(_window):
            return False
        _seg = _window
        _cut = max((_seg.rfind(ch) for ch in ".;:!?\n,"), default=-1)
        if _cut >= 0:
            _seg = _seg[_cut + 1:]
        return not _TORTOISE_HEAD_BINDING.search(_seg)

    def search(self, text):
        for m in self._rx.finditer(text):
            if self._claimed(m, text):
                return m
        return None

    def findall(self, text):
        return [m.group(0) for m in self._rx.finditer(text) if self._claimed(m, text)]

    def finditer(self, text):
        return (m for m in self._rx.finditer(text) if self._claimed(m, text))


_TORTOISE_SET_DIRECTION_CLAIM = _HeadBoundBan(re.compile(
    # ⛔ FORMATTING IS NOT CONTENT, AND THIS LINE IS THE CONTRACT, NOT A CONVENIENCE (#13802
    # review, cycle 7, P1 — MEASURED). The sibling detector normalises before it matches —
    # `low = re.sub(r"[`*_]", "", low)` and `flags=re.IGNORECASE` — and this file PINS that
    # as required, not incidental (`FORMATTING IS NOT CONTENT … the form matched
    # case-insensitively … not 'one spelling of it'`). This ban did neither, so eleven
    # spellings the detector read were refused by no arm in any checkout — measured
    # `ban=None` against a non-empty detector verdict, every one of them: `Prints two
    # paths` (capital P), `**prints** the two COMMENT-ONLY modules` and
    # `` `prints` the two comment-only modules `` (emphasis/backtick-wrapped verb),
    # `prints `two` paths` and `prints _two_ paths` (wrapped answer), `prints Empty`,
    # `prints Blank`, `prints Zero`, `prints Single`, `prints Both`, `prints Comment-Only`
    # (capitalised answer). Two of them injected into `05-verify.md` left the suite GREEN.
    # So the pattern absorbs emphasis around the VERB and BEFORE THE ANSWER and is compiled
    # case-insensitively (the `[*_`]{0,3}` inline classes); the text itself is NOT stripped —
    # the two command
    # anchors keep their LITERAL backticks, because making them `?` widened the anchor to any
    # `-- tortoise/` and refused two true `git grep … -- tortoise/` sentences in RECONCILE.
    # ANCHOR — the command (long spelling or the `-- tortoise/` shorthand), the
    # `tortoise/` SET, or the DEICTIC reference that names no command at all.
    r"(?:(?:git diff --name-only origin/main\.\.\.HEAD -- tortoise/`"
    # ⛔ THE BACKTICKS HERE ARE LITERAL, NOT OPTIONAL (#13802 review, cycle 7 — MEASURED):
    # making them `?` widened the anchor to ANY `-- tortoise/`, and `git grep -n … --
    # tortoise/` → **8** / `git grep apply_hierarchy_edges -- tortoise/` → **two** (both
    # real sentences in RECONCILE-A1-A2-A5.md, about a DIFFERENT command) were then refused.
    # This anchor is the `-- tortoise/` SHORTHAND FOR THAT DIFF, and the backticks are what
    # identifies it as a command spelling rather than a flag on any `git` invocation.
    # ⛔ AND THE CLASS IS THE FILE'S OWN DECLARED ONE, NOT `\**` (#13802 review, round 19, P1 —
    # MEASURED). Round 18 wrote `\**` here and at the `tortoise/` arm. That is a NARROWER class
    # than the contract stated 80 lines above ("the tolerance is a `[*_`]{0,3}` class … written
    # INLINE"): underscore emphasis is not asterisks, so `The _`tortoise/`_ set prints NOTHING`
    # stayed GREEN. The backtick stays OUT of this class on purpose — the arm above measured that
    # making the delimiters optional widens the anchor to ANY `-- tortoise/`, which refuses two
    # true `git grep … -- tortoise/` sentences. Emphasis inside, delimiters literal.
    r"|[*_]{0,3}`-- tortoise/`[*_]{0,3}"
    # ⛔ AND THE TREE'S OTHER SUBJECT NOUNS (#13802 review, round 13, P2 — MEASURED: the SAME
    # verb and the SAME answer were refused on `set` and GREEN on `diff`/`files`/`paths`/
    # `directory`, every one of them a noun this pattern's own ANSWER alternation carries, so
    # the stated residual — "a noun outside both lists" — did not describe them)
    # ⛔ AND EMPHASIS AROUND THE ANCHOR ITSELF (#13802 review, round 18, P1 — MEASURED: the
    # pattern tolerated emphasis around the VERB and before the ANSWER but not around the
    # anchor, so `The **`tortoise/`** set prints NOTHING` and `**`-- tortoise/`** prints
    # NOTHING` were GREEN here while the detector read the second — the exact
    # "detector-read, ban-blind" shape, on the rail where this ban is the only arm)
    r"|[*_]{0,3}`?tortoise/`?[*_]{0,3}\s+[*_]{0,3}(?:set|side|part|diff|files?|paths?|directory|tree|subtree|"
    r"module\s+set|delta|changes?)(?:\s*\([^)]{0,90}\))?[^`]{0,40}?"
    # ⛔ AND THE INDIRECT REFERENCE (#13802 review, round 5, P2 — MEASURED). The guarded
    # document states the answer WITHOUT repeating the command: "at THIS head the command
    # this clause prescribes returns NOTHING AT ALL (the diff carries no `tortoise/`
    # path)". Neither anchor above sees it — measured in-process at that head, both
    # detectors returned `[]` — so the ban declared a class closed while the document it
    # guards still carried an instance of it. A POINTER that names the command is still
    # allowed; this anchor matches only the deictic form, which carries no pointer.
    r"|the command this clause prescribes)"
    # ` there -> ` IS THE DOCUMENT'S OWN SPELLING, and a BOUNDED filler follows it so the
    # verb one word further out ("`-- tortoise/` command returns NOTHING AT ALL") cannot
    # slip through — measured green without this. Three words is the bound on purpose: the
    # POINTER form ("… is the command that answers it at the head you merge") and the
    # PAST-TENSE form must still reach no answer, and the witness test below pins both as
    # MUST-NOT-REFUSE.
    # ⛔ THE FILLER IS WIDER, AND ALLOWS A COMMA (#13802 review, P1 — MEASURED on a temp
    # copy: `the `-- tortoise/` command at this head prints NOTHING` and `the set, at this
    # head, returns NOTHING` were BOTH GREEN through this ban AND its sibling detector, in
    # the depth-1 configuration where the measured leg skips). Six tokens with optional
    # punctuation is still bounded: the POINTER and PAST-TENSE forms must reach no answer.
    # ⛔ AND THE EMPHASIS CLASS IS FOLLOWED BY ITS OWN WHITESPACE (#13802 review, round 19, P1 —
    # MEASURED). Ordered `\s*[*_`]{0,3}` and with no `\s*` after it, emphasis that is then
    # followed by a space could not be crossed at all: `**`git diff --name-only
    # origin/main...HEAD -- tortoise/`** prints NOTHING` and its `***` spelling were read by the
    # sibling DETECTOR and refused by no arm in this checkout or at the base — the "detector-read,
    # ban-blind" shape, on the one anchor where the detector is meant to be the companion. The
    # pair is what crosses `**` + space; it cannot cross a word, so the over-block surface is
    # unchanged.
    # ⛔ AND THE FILLER ADMITS A PATH-BEARING TOKEN AND A LONGER RUN (#13802 review, round 26,
    # P2 — MEASURED: the filler was `[\w-]+` x <=6, so a gap containing a `/`-bearing token
    # (`at the head of origin/main`) or one longer than six words defeated it, and every one of
    # those cells was DETECTOR-READ and BAN-BLIND — 600 of 11,220 swept cells. On the anchors
    # where the measured leg skips (a shallow checkout, `:1122`), this ban is the only layer, so
    # a detector-read/ban-blind spelling is a hole and not a narrowing. `/` is in the token class
    # because `origin/main` is this repo's most ordinary path token; the run is bounded at eight.
    # ⛔ AND THAT WIDENING COSTS AN OVER-BLOCK, DECLARED RATHER THAN CLAIMED AWAY (#13802 review,
    # round 27, P2 — MEASURED GREEN at `180b95d07d` and RED here, so it is THIS change's): the
    # peer's determiner/count arm accepts `the <count-noun>` with no count in it, so a
    # path-bearing filler now carries the class into TRUE prose:
    #   `` `-- tortoise/` prints the paths it printed yesterday. ``                 -> REFUSED (pre-existing)
    #   `` `-- tortoise/` at the head of origin/main prints the same paths it printed before. `` -> REFUSED (NEW)
    # Removing `a|an|the` from that arm un-refuses NOTHING — every must-refuse witness still
    # fires with them gone (measured: 0 lost; the pinned sentence above survives on the
    # `the\s+same` answer class, not on the determiners), so a narrowing is not a drive-by and
    # would not buy the sentence back anyway. The trade is taken in the safe direction (something
    # right is refused, not something wrong slips through), DECLARED here and pinned in the
    # must-refuse tuple so a later tightening reds it instead of changing behaviour silently.
    # Live cost today: zero — the corpus refusal set is the same six lines at `180b95d07d`, at
    # the peer head and here. ⛔ NO COUNT IS TYPED FOR THE WITNESS TUPLE, ON PURPOSE: an earlier
    # draft said "61 of the 192", the figure did not reproduce under any reading of "that arm",
    # and the `192` was not this head's tuple length either way it is counted — the same
    # typed-figure rot this file polices about itself, caught by #13802 review round 28, P3.
    # ⛔ NO COUNT OF THIS HEAD'S TUPLE IS TYPED OR RE-DERIVED HERE. Any further correction to
    # this note goes in the commit message, not in it: what the reader needs is the rule, and
    # the provenance of its wording is not load-bearing.
    r"(?:\s+there)?(?:\s*,?\s*(?:\([^)]{0,90}\)|[\w/-]+),?){0,8}\s*[*_`]{0,3}\s*"
    # ⛔ THE VERB CLASS IS THE DETECTOR'S OWN LIST (#13802 review, round 5, P2; merge-round
    # review, P1): `reports exactly two paths` and `yields `tortoise/x.py`` were green
    # here, and so were `shows`, `names`, `has`, `includes`, `contains` and the bare `=` /
    # `:` forms — all of which `_typed_answers_to_tortoise_cmd` reads. That arm SKIPS
    # once the base is unreadable, which is every job that gates a merge, so THIS list is
    # what carries the class on the rail; a verb the detector knows and this pattern does
    # not is a hole, not a narrowing.
    r"(?:prints?|->|→|returns?|reports?|lists?|yields|carries|gives|shows?|names?|"
    r"comes? back|resolves? to|answers? with|"
    r"includes?|contains?|outputs?|has|holds?|answers?|resolves?|reveals?|reads?|touches?|"
    r"is|are|was|were|remains?|stays?|[=:])\s*[*_`]{0,3}"
    # ⛔ AND A PREPOSITION BETWEEN THE VERB AND THE ANSWER (`comes back WITH an empty
    # list`, `resolves to ZERO`), measured GREEN before this line (#13802 review, round 7)
    r"(?:(?:with|to|as|into|out|of)\s+)?"
    # ⛔ AND THE QUANTIFIER AND THE COMMA-BOUNDED INSERTION BETWEEN THEM (#13802 review,
    # round 10, P1 — MEASURED GREEN through ban, detector and ledger: `prints ONLY one
    # path`, `has NO FEWER THAN two files`, `shows MORE THAN one path`, `prints, at this
    # head, nothing`). Every quantifier here is in `_typed_answers_to_tortoise_cmd`'s OWN
    # strip list, so a quantifier that detector removes and this bridge does not is a hole
    # in the same sense the verb class was — and on the `set`/deictic anchor this ban is
    # the only layer at all.
    r"(?:(?:just|only|exactly|at\s+least|at\s+most|more\s+than|fewer\s+than|"
    r"no\s+fewer\s+than|no\s+more\s+than|as\s+many\s+as|a\s+total\s+of|all|both|"
    # ⛔ AND THE DETECTOR'S OWN MODIFIER STRIP LIST (#13802 review, round 12, P1 — MEASURED:
    # `prints now nothing`, `is still empty` and 1260 of 1536 verb×modifier×answer cells were
    # detector-only, and on the `set`/deictic anchor NEITHER arm saw them)
    r"now|still|actually|also)\s+)?"
    # ⛔ AND ONE DASH IS AN INSERTION TOO (#13802, round 21 — `f0fea1ce1c` made the closing dash
    # optional; the row claimed it, and `The `tortoise/` set prints — NOTHING` had been GREEN
    # because the alternative demanded a CLOSING dash as well)
    r"(?:,\s*[^,.;\n]{1,30},|\s*[—–]\s*[^.;\n]{0,30}?(?:[—–]\s*)?)?\s*"
    # ⛔ AND A PARENTHETICAL (#13802 review, round 14, P2 — MEASURED: `(run at this head)`
    # between the command and the verb, and `set (measured with `git diff …`) is empty`,
    # were GREEN in BOTH arms while the comma and bare forms were refused)
    r"(?:\([^)]{0,90}\)\s*)?"
    # ⛔ AND THE VERB PARTICLE `back` (`gives back nothing`) (#13802 review, round 12, P2)
    r"(?:(?:us|them|you)\s+)?(?:back\s+)?"
    # ⛔ AND `no longer`, the ordinary English spelling of the emptiness reversal (#13802
    # review, round 12, P2 — `is no longer empty` was GREEN through both arms)
    # ⛔ AND A BACKTICK AFTER THE EMPHASIS (#13802 review, round 20, P1 — MEASURED: the verb's
    # `[*_`]{0,3}` spends its budget on `**`, leaving the backtick that opens the answer
    # unconsumed — `**prints** `NOTHING`` was GREEN while the detector read it, the
    # detector-read/ban-blind shape again)
    r"(?:(?:NOT|not|no\s+longer|NO\s+LONGER)\s+)?[*_`]{0,3}"
    # AND THE ANSWER FORMS that detector knows, in the same widening: `one path` as
    # readily as `exactly two`, `both modules` as readily as `TWO modules`, and a
    # MODIFIED membership count ("the two COMMENT-ONLY modules") as readily as a bare one.
    r"(?:NOTHING|NO paths?|no (?:`?tortoise/[^`\s]*`?\s*)?(?:paths?|files?|entry|entries|"
    r"modules?|edits?|diffs?|hits?|records?|items?|rows?|lines?|changes?)"
    # ⛔ AND THE SPACE SPELLING OF `non-empty` (#13802 review, cycle 6, P1 — MEASURED):
    # the detector's own class is `non[- ]?empty`, so it reads `is non empty`; this
    # pattern listed only the hyphen and closed spellings, so
    # ``the `tortoise/` set is non empty`` was read by NO arm in ANY checkout — measured
    # `ban=False det=[('prints non empty', False)]`. A token the detector reads and this
    # bridge does not is the same hole the verb class was.
    # ⛔ AND AN INDEFINITE ARTICLE BEFORE THE ANSWER (#13802 review, round 14, P1 — MEASURED:
    # `returns a non-empty set` is the DETECTOR's OWN pinned witness string at `:1320`, and the
    # ban returned False for it; with the `set` anchor NEITHER arm read it, at a head where the
    # real answer is `nothing`)
    # ⛔ AND A DETERMINER FROM THE DETECTOR'S OWN STRIP LIST (#13802 review, round 15, P2 —
    # MEASURED: `the` is in `_typed_answers_to_tortoise_cmd`'s determiner list at `:698` and
    # NOT here, so `returns the empty set` was detector-read and ban-blind — and the ban is the
    # only arm over 12 of the 13 bundle documents. `an empty set` was already carried; `the`
    # is the same answer with a determiner the sibling strips.
    # ⛔ AND EVERY DETERMINER THE DETECTOR STRIPS, NOT ONLY `a`/`an`/`the` (#13802 review, round 19,
    # P2 — MEASURED: the detector strips determiners in a LOOP at `:698`
    # (`the|a|an|its|this|that|those|these|just|only|still|now|actually|also|…`), while the two
    # alternatives here carried only `a`/`an`/`the`. `returns its empty set` was therefore
    # detector-read (`[('returns its empty set', True)]`) and BAN-BLIND, and `the`/`an`/`a`
    # reaching the answer is the same answer with a determiner the sibling already strips. The
    # shape is the detector's own — a REPEATED strip, so `those two files` and
    # `just the empty set` are read the same way the detector reads them.
    r"|(?:(?:the|THE|a|an|its|this|that|those|these|just|only|still|now|actually|also"
    r"|exactly|at\s+least|at\s+most|more\s+than|no\s+fewer\s+than)\s+)+"
    r"(?:empty|EMPTY|non[- ]?empty|not[- ]empty|non[- ]?EMPTY)"
    r"|(?:empty|EMPTY|non[- ]?empty|not[- ]empty)"
    r"|exactly those two|exactly (?:one|two)"
    r"|ONE module|TWO modules|one module|two modules|both modules"
    r"|ONE path|TWO paths|one path|two paths|no module"
    # ⛔ AND `both` WITH ANY NOUN, NOT ONLY `modules` (#13802 review, round 9, P1 — MEASURED
    # GREEN on the `set`/deictic anchor, where this ban is the only layer: `both files`,
    # `both paths` were read by nothing while `both modules` red)
    r"|both\s+(?:files?|paths?|modules?|entries|diffs?|hits?)"
    r"|(?:the\s+)?(?:one|two|three|four|five)\s+[\w-]+\s+modules"
    r"|`?tortoise/[A-Za-z0-9_./-]+\.py`?"
    # ⛔ AND THE TAIL SPELLINGS 15 FRESHLY-INVENTED SENTENCES REACHED FOR (#13802 review,
    # round 5, P1 — every one measured GREEN before this line existed): an empty list
    # literal, `none`/`zero`, a counted FILE set (`holds exactly four files`), a named path
    # after an unlisted verb (`answers with `tortoise/x.py``), and the diff-side paraphrase
    # of the comment-only claim (`is this diff's own comment-only edits`).
    r"|`?\[\]`?|none|None|zero|ZERO|nothing|Nothing|blank|BLANK"
    # ⛔ AND THE `an empty list` / `single` / BARE-DIGIT tails (#13802 review, round 7, P1 —
    # each measured GREEN here while the detector read it; for the DEICTIC and `set` anchors
    # this ban is the ONLY layer, because the detector anchors on a command token)
    r"|(?:an?\s+)?(?:empty|blank|EMPTY)\s+(?:list|set|array|result|output)"
    r"|(?:an?\s+)?single|SINGLE|\b\d+\b"
    # ⛔ AND A COUNT WORD WITH NO NOUN AT ALL, AND A BARE `tortoise/` ANSWER
    # (#13802 review, cycle 6, P1 — MEASURED, carried across the round-11 merge):
    # the detector's `_member` reads a bare `one`/`two`/…/`ten`/`both` and a bare
    # `tortoise/`, so those answers walked through green here on the `set` and
    # deictic anchors, where this ban is the only layer.
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|both)"
    r"(?=[\s.,;:!?)*_`]|$)"
    r"|`?tortoise/`?(?=[\s.,;:!?)*_`]|$)"
    # ⛔ AND THE SAME REPEATED-DETERMINER SHAPE ON THE COUNT ANSWER (#13802 review, round 19, P2 —
    # MEASURED: `prints those two files` and `returns its two files` were detector-read and
    # ban-blind, while the pinned sibling `prints both files` red — a ONE-WORD deletion from a
    # pinned must-red witness. Swept 940 detector-read/ban-blind cells over
    # determiner×count×noun×verb before the widening; 0 after.)
    r"|(?:(?:the|THE|a|an|its|this|that|those|these|just|only|still|now|actually|also"
    r"|exactly|at\s+least|at\s+most|more\s+than|no\s+fewer\s+than)\s+)*"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|both|\d+|a|an|the)[*_`]{0,3}\s+"
    # ⛔ `\d+` IS IN THAT GROUP BECAUSE THE ANSWER GROUP STARTS AFTER THE VERB (#13802 review,
    # round 19, P2 — MEASURED): the bare-digit alternative is the separate `\b\d+\b` arm, which
    # only fires when the digit sits DIRECTLY after the verb, so `returns its 2 files` and
    # `returns those 2 files` were detector-read and ban-blind while `returns 2 files` red.
    # ⛔ THE WHITESPACE IS REQUIRED, AND THAT IS THE WHOLE GUARD AGAINST A SUBSTRING MATCH
    # (#13802 review, cycle 7 — MEASURED): with `\s*` on either side of the adjective, the
    # word `narrow` in `its `-- tortoise/` sibling is the NARROWER measurement` matched as
    # `nar` + `row` and the bundle document was refused — a false positive that the required
    # space makes impossible. Emphasis stays optional; the SEPARATOR does not.
    r"(?:[\w-]+[*_`]{0,3}\s+)?(?:files?|paths?|modules?|edits?|entry|entries|diffs?|hits?|"
    r"records?|items?|rows?|lines?|changes?)"
    r"|(?:[a-z''-]+\s+){0,4}comment[^.]{0,40}?(?:only|text|edits?)"
    # ⛔ AND THE VERB-BEFORE-PATH PARAPHRASES that carry the answer as a NOUN PHRASE
    # (`paths only as comment/docstring text`) — no verb after the anchor at all.
    r"|paths?\s+only\s+as[^.]{0,60}"
    # ⛔ AND THE NO-DIFFERENCE NOUN-COMPLEMENT (#13802 review, round 10, P2): the `set`
    # ANCHOR reaches a verb, but `the `tortoise/` set is untouched` needs these words in the
    # ANSWER class too — measured GREEN with only the bare-`tortoise/` alternative added.
    r"|untouched|unchanged|unmodified|byte-identical|identical|the\s+same|no\s+code"
    r"|`?tortoise/[A-Za-z0-9_./-]+\.py`?)"
    r"|neither is `git diff --name-only origin/main\.\.\.HEAD -- tortoise/`"
    # ⛔ AND THE ANSWER-FIRST ORDER (#13802 review, round 13, P2 — MEASURED GREEN in all three
    # arms: `Nothing is in the `tortoise/` set at this head.`, `Empty is what `-- tortoise/`
    # prints at this head.`). Every arm here is verb-then-answer, so the same claim stated the
    # other way round was read by nothing; this alternative requires the ANSWER to open a
    # clause and the anchor to follow inside the same sentence, so ordinary prose that merely
    # begins with "Nothing" is untouched.
    r"|neither is `git diff --name-only origin/main\.\.\.HEAD -- tortoise/`)"
    # ⛔ AND THE "which N paths" SHAPE (#13802 review, P1 — MEASURED): the measured leg's own
    # claim list carries `which two `tortoise/` paths this diff carries`, and this ban could
    # not see it — an anchor-less membership question in the document's own idiom.
    r"|which (?:one|two|three|four|five)\s+`?tortoise/[^`]*`?\s+paths?"
    # E) ⛔ AND THE ANSWER-FIRST ORDER (#13802 review, rounds 13 and 16 — MEASURED GREEN in all
    # three arms, BOTH directions: `Nothing is in the `tortoise/` set at this head.`,
    # `Empty is what `-- tortoise/` prints`, `Both files are what the `tortoise/` set holds`,
    # `Two paths are what `-- tortoise/` prints`, `tortoise/x.py is what `-- tortoise/`
    # returns`, `A non-empty set is what the `tortoise/` set is`). Every other alternative is
    # verb-then-answer, so the same claim stated the other way round was read by nothing; this
    # one requires an ANSWER to open a clause and `tortoise/` to follow inside the same
    # sentence, so ordinary prose that merely begins with `Nothing` is untouched. The
    # emptiness-only cut of round 13 covered ONE direction of the two — that is round 16's fix.
    # ⛔ AND THE SENTENCE BOUNDARY TOLERATES EMPHASIS AFTER THE PUNCTUATION (#13802 review,
    # round 26, P1 — MEASURED: `**At this head.** There are no files in the `tortoise/` set.`
    # was GREEN because a bold span CLOSING the previous sentence puts `**` between the `.` and
    # the space this branch required. `.` then `[*_`]{0,3}` then whitespace is the same sentence
    # boundary the branch already reads; it crosses no word, so the over-block surface is
    # unchanged (corpus 6 -> 6, measured).
    r"|(?:(?:^|\n)[ \t]*(?:[-*+>]+|\d+[.)])?[ \t]*|[.;:!?][*_`]{0,3}\s)"
    r"(?:[*_`]{0,3}(?:at|on|in)[*_`]{0,3}\s+"
    r"(?:[*_`]{0,3}(?:this|the|that)[*_`]{0,3}\s+)?"
    # ⛔ AND THE FRONTED ADVERBIAL IS A STRUCTURE, NOT A LIST OF SPELLINGS (#13802 review, round
    # 23, P1 + P2 — MEASURED at `3b901efa84`). Round 22 closed the fronting hole with a bounded
    # list (`at this head|at the head you merge|at this merge|…`) and that reproduced, on the
    # element it had just added, the two defects this file has closed four times already:
    #
    # (a) NO FORMATTING TOLERANCE. Every other element in this ban absorbs `[*_`]{0,3}`, and the
    #     contract at `:2047` is that formatting is NOT content. The fronted group sat BEFORE the
    #     class, so it could never absorb emphasis, and the corpus's own house spelling walked:
    #       `**At this head** there are no files in the `tortoise/` set.`   -> GREEN
    #       `_At this head_ …`, `` `At this head` … ``, `At **this** head …`  -> GREEN
    #       `At this head — there are no files in the `tortoise/` set.`     -> GREEN
    #     The emphasised form is not invented, and the count is the MEASURED one (round 24, P2 —
    #     the earlier sentence said "3 files" for a spelling that occurs ONCE): `grep -ro -F
    #     '**At this head**' docs --include='*.md'` -> 1 occurrence, case-insensitively -> 4
    #     in 4 files (`-i`; round 25, P2 — the earlier sentence said 3, and a THIRD number
    #     survived at the witness block below, so the file disagreed with itself), e.g. `docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md:216`.
    #     The ban does not care about the case: it compiles IGNORECASE. And
    #     the sibling detector reads none of them (`_typed_answers_to_tortoise_cmd -> []`), so
    #     there was no second layer.
    # (b) THE LIST'S STATED JUSTIFICATION WAS FALSE. "The spellings this corpus actually uses …
    #     all already in the ban's own vocabulary" is refuted by the census: `on this head` 11x
    #     (and `on` IS in the ban's vocabulary — `_TORTOISE_HEAD_BINDING_AT` carries `at|on|in`),
    #     `at the head` 158x, `at this head` 331x. Thirteen of eighteen realistic fronted
    #     spellings were still GREEN, `On this head …` among them (re-measured here, 13/18).
    # ⚠️ AND THE STRUCTURE'S OWN RESIDUAL IS MEASURED, NOT ASSERTED (round 23): of the same
    # eighteen spellings, THREE remain GREEN at this head — `at merge time`, `as of this head`
    # and `at the head of this merge` — because none of them has the shape the class reads
    # (preposition + determiner + … + `head`/`merge`). ⛔ ROUND 24 CUT THAT RESIDUAL FROM THREE TO
    # TWO, AND ONE OF THE THREE WAS A REGRESSION RATHER THAN A RESIDUAL (MEASURED at
    # `8f2f2ad25b`): `at merge time` was RED at `3b901efa84` — the round-22 bounded list carried
    # `at\s+merge\s+time` — and the structural class DROPPED it, i.e. a capability left the
    # guard silently and NO witness red, because that spelling was pinned nowhere. So the
    # determiner above is now OPTIONAL and `time` is in the trailing group: `At merge time there
    # are no files in the `tortoise/` set.` is RED here again, and pinned below. The residual is
    # ONE spelling that still has no shape to read — `as of this head` (the preposition phrase `as
    # of`) — and it is priced by LIVE VICTIMS, not word counts: it does not occur in `docs/`
    # today, and the corpus scan below finds 0 new refusals and 0 lost ones. ⛔ `at the head of
    # this merge` USED TO BE THE SECOND RESIDUAL AND IS NOT ONE ANY MORE (#13802 review, round
    # 28, P3 — MEASURED: it is REFUSED from `f5ea75f1d3` onward by the `merg*` tail below, so a
    # residual list that still names it UNDERSTATES the class, which is the same rot in the other
    # direction).
    # So the boundary is the STRUCTURE — a preposition the ban already treats as a head binder,
    # an OPTIONAL determiner, up to two intervening words, then `head`/`merge` (optionally a
    # trailing `head`, `time`, or `<pronoun> merge`) — case-insensitively (the ban's own flag),
    # with the emphasis class on every token and the em/en-dash bridge the dash family already
    # carries. Enumerating spellings is what failed; this reads the shape the spellings share.
    # ⛔ AND THE CLAUSE OPENS ON A MARKDOWN PREFIX TOO (#13802 review, round 24, P2 — PRE-EXISTING
    # at `3b901efa84`, closed here because this range's whole subject is the clause-opening
    # shape): `^` is string-start only (no MULTILINE) and the old `\n` branch required the
    # adverbial IMMEDIATELY after the newline, so a list item, a blockquote or an indented line
    # defeated the entire family. ⛔ THE MARKER CLASS BUYS THE `)` ITEM AND NESTED QUOTES, NOT THE
    # `.` ITEM (#13802 review, round 25, P2 — MEASURED, and the earlier sentence here claimed
    # otherwise): `1. At this head …` was ALREADY refused before this anchor existed, because the
    # item's own period satisfies the pre-existing `[.;:!?]\s` branch — so a witness built on
    # `1.` pins nothing and the arm stays green even if `\d+[.)]` is narrowed to `\d+\.`
    # (measured: the suite passes with that narrowing while `1)` and `10)` go GREEN). The forms
    # this class actually adds are `1)`, `10)`, `-`, `*`, `+`, `>` and `>>`.
    # 115 lines in `docs/` carry a `[-*+>] … at|on|in (this|the|that) … (head|merge)` shape
    # today; the corpus scan below shows 0 of them is a direction claim, so the widening costs
    # nothing live. ⚠️ SCOPED OUT DELIBERATELY, not an oversight: a HEADING (`## At this head …`),
    # a bold label (`**Note:** At this head …`) and a table cell (`| At this head, … |`) are not
    # clause openings this family models — no `docs/` line carries any of them with a direction
    # claim, and matching a `|`-delimited cell would need a cell boundary model this ban does not
    # have.
    # ⛔ AND THAT INTERVENING RUN ADMITS A PATH-BEARING TOKEN, FOR THE SAME REASON ITS SIBLING
    # FILLER DOES (#13802 review, round 27, P2 — MEASURED: with `[\w-]+`, `At the head of
    # origin/main you are about to merge there are no files in the `tortoise/` set.` was GREEN
    # while its un-fronted twin was RED — the parity the `merg*` tail now gives every other
    # spelling, defeated by one `/`. The two arms read the SAME token class for the SAME reason:
    # `origin/main` is this repo's most ordinary path token.)
    # ⛔ AND THE INTERVENING RUN IS THE SAME EIGHT (#13802 review, round 28, P2 — MEASURED: the
    # comment below claimed "the bound is now the SAME eight in both places" while this run was
    # still `{0,3}`, so the parity broke again at a FOUR-token gap — `At the very top outer layer
    # head of the branch you are about to merge …` GREEN against a RED twin. The bound is one
    # constant across the three runs now (this one, and both branches of the tail), which is what
    # makes the parity a property of the pattern rather than of one clause.)
    r"(?:[*_`]{0,3}(?!(?:head|merge)(?![A-Za-z0-9]))[\w/-]+[*_`]{0,3}\s+){0,8}"
    # ⛔ AND THE HYPHENATED `merge-time` (#13802 review, round 25, P2 — MEASURED: `At merge-time
    # there are no files in the `tortoise/` set.` was GREEN while its UN-FRONTED twin `There are
    # no files in the `tortoise/` set at merge-time.` was RED — the same one-order-refused
    # asymmetry rounds 13/16/21 closed, sitting inside the class this round widened.)
    r"[*_`]{0,3}(?:head|merge)(?:[ -]time)?(?:['’]s)?(?![A-Za-z0-9])[*_`]{0,3}"
    # ⛔ AND THE TRAILING SHAPE IS PRONOUN-GENERAL, NOT ONE PRONOUN (#13802 review, round 24,
    # P2 — MEASURED at `8f2f2ad25b`): the literal `you\s+merge` refused `head you merge` and
    # left `head we merge` / `head they merge` / `head one merge` GREEN in BOTH arms. `time`
    # is here for the same reason (#13802 review, round 24, P1 — the structural class DROPPED
    # `at merge time`, which the round-22 bounded list HAD caught: RED at `3b901efa84`, GREEN
    # here before this line, i.e. a capability silently leaving the guard with no witness to
    # red. That regression is why the determiner above is optional and `time` is below.)
    # ⛔ AND ONE `<word> merge` WAS STILL ONE ALTERNATION, NOT A TAIL (#13802 review, round 26,
    # P2 — MEASURED: `["-+]\s+merge` reads exactly ONE word before `merge`, so the FRONTED
    # order stayed GREEN while its un-fronted twin was REFUSED in five grammatical shapes —
    # `at the head you are merging` / `… you are about to merge` / `… to be merged` /
    # `… you will merge` / `… post-merge` — the same one-order-refused asymmetry rounds
    # 13/16/21/25 closed, one grammatical step further out. The tail is now a bounded TOKEN RUN
    # ending in a `merg*` word, so every inflection (`merge`/`merging`/`merged`) and every
    # adverbial form of the same claim is read by the shape rather than by a list.
    # ⚠️ DECLARED RESIDUAL, MEASURED: a tail that carries NO `merg*` word — `at the head of the
    # PR`, `at the head of this branch` — is still GREEN in the fronted order. A general
    # any-word tail was CONSIDERED AND REJECTED: it is not a shape, it is "any phrase", and it
    # would refuse ordinary prose (`At the head of the file, …`) that this file pins as
    # MUST-NOT-REFUSE. Live victims measured: `grep -rIo 'head of the PR' docs | wc -l` -> 0,
    # `grep -rIo 'head of this branch' docs | wc -l` -> 2 (neither is a direction claim — the
    # count and the READING are stated separately, so a verifier re-running the command gets the
    # number this comment prints); corpus hit set unchanged at 6.
    # ⛔ AND THE RUN IS BOUNDED AT EIGHT, MATCHING FAMILY A (#13802 review, round 27, P2 —
    # MEASURED: at `{0,4}` the parity the line above claims was still broken one step out —
    # `At the head of the branch you are about to merge there are no files in the `tortoise/`
    # set.` was GREEN while its un-fronted twin was RED, and the sibling detector reads neither
    # (`[]`), so there was no second layer. The sentence above said "every adverbial form"; it
    # said so while the bound was 4 and family A's filler (the `(?:\s+there)?` alternative) was
    # 8, which is why the same five-token phrase was caught command-first and missed fronted. The
    # bound is now the SAME eight in all three runs — the intervening one above and both tail
    # branches — so the parity is a property of the pattern rather than a coincidence of one
    # clause's bound (#13802 review, round 28, P2: the round-27 edit widened the token CLASS of
    # the intervening run and left its BOUND at 3, which is exactly the gap this note closed).)
    # ⛔ AND THE TAIL READS THE SAME TOKEN CLASS AS ITS TWO SIBLINGS (#13802 review, round 27,
    # P2 — MEASURED: with `[\w-]+` here, `At the head of origin/main you are about to merge
    # there are no files in the `tortoise/` set.` stayed GREEN while its un-fronted twin was RED —
    # the LAST of the three runs in this family to learn that a `/` is a token character, and the
    # one that kept the parity claim false after the other two were fixed.)
    r"(?:\s+(?:(?:head|time)(?:\s+(?:[\w/-]+\s+){0,8}[\w/-]*merg\w*)?"
    r"|(?:[\w/-]+\s+){0,8}[\w/-]*merg\w*)[*_`]{0,3})?"
    # ⛔ AND EMPHASIS MAY WRAP THE ADVERBIAL'S OWN TRAILING PUNCTUATION (#13802 review, round 26,
    # P1 — MEASURED: round 23 put `[*_`]{0,3}` on every TOKEN of the fronted group and declared
    # the class closed under formatting, but the `,` / `:` / `.` / `—` that ENDS the adverbial had
    # to sit OUTSIDE the emphasis. So the idiomatic markdown lead-in — the bundle's own house
    # spelling, `**At this head:**` — was refused by no alternative:
    #   `**At this head,** there are no files in the `tortoise/` set.`  -> GREEN
    #   `**At this head:** …` · `_At this head,_ …` · `**At merge time,** …` -> GREEN
    #   `**At this head —** …` · `**At this head.** There are …`              -> GREEN
    # The sibling detector reads none of them (no command token), so there was no second layer.
    # The closing emphasis is absorbed after the punctuation, which cannot cross a word.
    r"\s*(?:[—–]\s*)?,?[*_`]{0,3}\s+)?"
    # ⚠️ DECLARED OVER-BLOCK, MEASURED (round 23, P2): this prefix extends a PRE-EXISTING
    # over-block into fronted prose — `At this head nothing in the `tortoise/` set is broken.`,
    # `At this head none of the `tortoise/` files were touched.` and `At this head both files in
    # `tortoise/` have docstrings.` are GREEN at `9024f2e5d0` and RED here, and their un-fronted
    # twins were already RED. Corpus cost today: **zero** — the ban's hit set over all 855
    # `docs/**/*.md` is 6 before and 6 after, set difference zero in both directions.
    r"(?:there\s+(?:is|are)\s+)?[*_`]{0,3}(?:NOTHING|Nothing|EMPTY|Empty|NONE|None|ZERO|Zero|`?\[\]`?"
    r"|both\s+(?:files?|paths?|modules?|entries)"
    # ⛔ AND THE REPEATED-DETERMINER / `\d+` SHAPE THE VERB-FIRST ORDER GOT IN ROUND 19, ONE
    # ORDER OVER (#13802 review, P2 — MEASURED at `180b95d07d`). Round 19 widened the
    # verb-then-answer count answer to strip determiners in a LOOP and to carry `\d+`; this
    # alternative kept the bare `(?:one|two|…|ten)\s+<noun>` head, so the SAME claim was
    # refused in one order and GREEN in the other — the asymmetry rounds 13/16/21/25 each
    # closed once, reappearing in the dimension round 19 had just closed:
    #   RED    `` `-- tortoise/` prints those two files at this head. ``
    #   GREEN  `` Those two files are what `-- tortoise/` prints at this head. ``
    # SEVEN cells measured GREEN here while their verb-first twins red — `those`, `these`, `its`,
    # `all`, `just`, `those 2`, and a bare `2`. `\d+` belongs here for round 19's own reason:
    # the separate `\b\d+\b` arm fires only when the digit sits directly after the VERB, and
    # this order puts it before the clause instead. The noun class, the optional MODIFIED noun
    # (`the two comment-only modules`) and the determiner list are the verb-first group's, so
    # the two orders cannot drift apart in this dimension either.
    # ⛔ OVER-BLOCK IS NOT BOUNDED BY THE TAIL — MEASURED, AND THE EARLIER CLAIM HERE WAS FALSE
    # (#13802 review, round 26, P2). The text this replaced said the widened forms "still have to
    # reach `tortoise/` and then a subject-noun OR VERB", and that this kept
    # `All six paths under `tortoise/` are covered by tests.` allowed because `are`/`under`/`were`
    # are not in that list. The `under tortoise/` spelling IS still allowed — but the
    # `in the `tortoise/` SET` spelling is not, because the tail's own anchor noun `set` (likewise
    # `tree`, `directory`) satisfies it BEFORE any verb is reached, so the verb is never consulted.
    # MEASURED at `3b8a38bdd0` (GREEN at `180b95d07d` -> RED here):
    #   `These two files in the `tortoise/` set are the resolver and its test.`
    #   `All six paths in the `tortoise/` set are covered by tests.`
    #   `All six paths in the `tortoise/` tree are covered by tests.`
    #   `Those two files in the `tortoise/` set are read by the resolver.`
    #   `2 files in the `tortoise/` set were added by that PR.`
    # while their bare-anchor twins (`in `tortoise/``, `under `tortoise/``) stay ALLOWED. So this
    # widening BUYS the closing of the false-PASS cell and PAYS an over-block class it did not
    # have; the file's own rule is that such a class is DECLARED rather than claimed away, so it
    # is declared here with its examples. Corpus cost, measured over all 855 `docs/**/*.md`:
    # 6 refusals before, 6 after, set difference 0 — no live victim, which is why this is a
    # documented residual and not a blocker. ⛔ TIGHTENING IT WAS MEASURED AND REFUTED, NOT LEFT
    # OPEN (#13802 review, round 27, P2): "require a verb, not a bare anchor noun, after the
    # `tortoise/` reference" was implemented and measured at `c42c6cb4`. It closes four of the five
    # sentences above, still refuses the fifth (`… set are read by the resolver`, because `reads?`
    # is in the verb list), and OPENS a false PASS — `<N> <noun> in the `tortoise/` set at this
    # head.` has no verb after the reference, so the SIZE of the diff's `tortoise/` set at this head
    # walks green. That is the direction this file says costs more than an over-block (D5), so the
    # over-block above is the cheaper side of the trade and the residual STAYS. Separating the two
    # shapes needs a predicate-vs-locative model, not a one-line tail edit; the four spellings the
    # tightening would green are therefore still refused, and the five membership claims it would
    # allow are pinned MUST-RED in the witness test so the same edit cannot be landed silently.
    # (Pre-existing and not this widening's doing: `Every file in the `tortoise/` set has a module
    # docstring.` is refused at BOTH `180b95d07d` and this head — the shared tail, not this class.)
    r"|(?:(?:the|a|an|its|this|that|those|these|just|only|still|now|actually|also"
    r"|exactly|at\s+least|at\s+most|more\s+than|fewer\s+than|no\s+fewer\s+than"
    r"|no\s+more\s+than|as\s+many\s+as|a\s+total\s+of|all|both)\s+)*"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|both|\d+)[*_`]{0,3}\s+"
    r"(?:[\w-]+[*_`]{0,3}\s+)?"
    # ⛔ AND THE OTHER COUNT NOUNS ARE IN THIS CLASS TOO (#13802 review, cycle 9, P2 —
    # MEASURED): `entries`, `diffs` and `edits` red in the VERB-THEN-ANSWER arm (`:2169` binds
    # them) while `Six entries are what the `tortoise/` set holds at this head` stayed GREEN
    # here — an asymmetry between the two orders of the same sentence, and one the row's own
    # residual sentence did not cover, because these nouns are inside the ban's own lists.
    r"(?:files?|paths?|modules?|entries|entry|edits?|diffs?|hits?|records?|items?|rows?|lines?|changes?)"
    r"|(?:an?|the)\s+(?:empty|non[- ]?empty)"
    # ⛔ AND THE `no <noun>` SPELLING IN THE ANSWER-FIRST ORDER (#13802 review, round 21 —
    # MEASURED at this head: `There are no files in the `tortoise/` set at this head.` is GREEN
    # while its VERB-FIRST twin `The `tortoise/` set shows no changes at this head` is refused
    # by the arm below — the same claim, two orders, one refused, which is exactly the
    # asymmetry rounds 13 and 16 closed twice before). The noun class is the ban's OWN count
    # class (`entries|entry|edits?|diffs?|hits?|records?|items?|rows?|lines?|changes?`) plus the
    # three shapes it spells out (`files?|paths?|modules?`), so the two orders cannot drift
    # apart again. Bounded exactly like its siblings: the clause must OPEN with `no <noun>`,
    # `tortoise/` must follow inside the same sentence, and a verb or subject-noun of the anchor
    # list must follow THAT — so `No changes to `tortoise/` are needed` stays allowed (its
    # `are` is not in the list), pinned in the must-not-refuse set below.
    r"|no\s+(?:files?|paths?|modules?|entries|entry|edits?|diffs?|hits?|records?|items?|"
    r"rows?|lines?|changes?))"
    # ⚠️ DECLARED RESIDUAL, MEASURED (delta review of this widening): the QUOTE- and
    # PAREN-wrapped answer (`The `tortoise/` set prints 'NOTHING' at this head.`, `… prints
    # (NOTHING) at this head.`) is read by NEITHER arm — the emphasis class is `[*_`]`, and the
    # sibling DETECTOR strips `[*_`]` but matches on a COMMAND token, so it reads none of them
    # either. A gap in BOTH arms, not the detector-read/ban-blind asymmetry the rail must not
    # carry; the live-victim scan over all 855 `docs/**/*.md` finds zero of either form.
    # ⛔ AND THE ANCHOR MUST BE FOLLOWED BY THE VERB OR THE SUBJECT-NOUN (#13802 review, round
    # 16 — MEASURED): without that, ordinary prose (`… it is transport only — nothing in
    # `tortoise/``, `; `tortoise/x.py` was in an earlier cut`) matched the emptiness answer and
    # the path answer with no claim behind it. A path is also dropped from the answer class
    # here: `tortoise/x.py is what … returns` is prose this pattern cannot distinguish from a
    # mention, and the row's own rule is that an over-block costs more than a false PASS.
    # ⛔ A LOOKAHEAD, NOT `\b` (#13802 review, cycle 9, P2 — MEASURED): the answer class above
    # ENDS with `` `?\[\]`? ``, and a word boundary cannot follow `` ` `` or `]` — so the `[]`
    # branch was UNREACHABLE here. Measured: `` `-- tortoise/` prints `[]` at this head `` red
    # in the verb-then-answer arm while `` `[]` is what `-- tortoise/` prints at this head ``
    # was GREEN, i.e. the same claim in the two orders disagreed. `(?![A-Za-z0-9_])` is the
    # boundary that is true after a word AND after punctuation, so both spellings are read.
    r"(?![A-Za-z0-9_])[^.\n]{0,60}?tortoise/`?\s*(?:set|side|part|diff|files?|paths?|directory|tree|"
    r"subtree|delta|changes?|holds|holds?|returns?|prints?|reports?|lists?|"
    r"carries|contains?|includes?|shows?|names?|gives|outputs?|yields|answers?|"
    # ⛔ AND THE LINK VERB THIS FAMILY WAS MISSING (#13802 review, P2 — MEASURED, and
    # disclosed at `480c675313` as PRE-EXISTING and left: *"the same `[]`-unreachability
    # survives in a SIBLING alternative … arm E's trailing verb list has no `has`"*).
    # The answer-first order names the action with the LINKING verb, and this list carried
    # no `has` while BOTH other families did (the general verb class above: `has|holds?`;
    # the verb-before-path family: `carries|touches|has|holds|…`). ⛔ MEASURED, RE-DERIVED IN
    # ROUND 22 (an earlier cut of this sentence published `42 of 210` on a stated
    # `13-verb × 7-answer` grid, and 13 × 7 is 91, not 210 — a denominator the stated grid
    # cannot produce): with the COMMAND anchor and `[]` as the answer, the answer-first order
    # read NONE of the eight link verbs the other two families carry at `9024f2e5d0` — 8 of 8
    # cells GREEN through this ban, with `_typed_answers_to_tortoise_cmd` returning `[]` for
    # every one, so on the `set`/deictic anchor there was no second layer either, while the
    # SAME claim in the verb-then-answer order red. A verb the other two families carry and
    # this one does not is the same hole round 17 closed in the verb-before-path direction, so
    # the list is now aligned with theirs.
    # ⚠️ THE COPULAS STAY OUT, AND THAT IS A MEASURED DECISION, NOT AN OVERSIGHT (#13802
    # review, P2 — the general class carries `is|are|was|were|remains?|stays?` because there
    # a copula is BOUNDED by the answer that follows it; at the END of this alternative there
    # is nothing after the verb to bind it, so `is` would refuse `One path in `tortoise/` is
    # deprecated.` and `Two paths in `tortoise/` are read by the resolver.` — ordinary prose
    # about the tree, not a direction claim. That is the file's own trade, stated here rather
    # than matched: an over-block costs more than a false PASS. ⛔ AND THE OVER-BLOCK CLASS IS
    # MEASURED, NOT ASSERTED ABSENT (an earlier cut of this sentence said the added verbs buy
    # "NOT a NEW over-block class", and round 22 measured that false): `have|had` are far more
    # ordinary with a PLURAL subject than `holds`, so at head — GREEN at `9024f2e5d0`, RED now —
    # `Both files in `tortoise/` have a module docstring.` and `Two paths in `tortoise/` have
    # been updated.`, and the widened count-noun set does the same to `The `tortoise/` set has
    # no lines longer than 88 characters.` That is the price of closing the false-PASS cell
    # above, paid knowingly: the file's own trade. Measured over 855 `docs/**/*.md`: **0 new
    # hits** (6 before, 6 after, set difference zero) and 0 over the 13 guarded bundle
    # documents, so the cost is bought against prose nobody has written — every must-not-refuse
    # form in the witness test stays GREEN.
    r"has|have|had|comes? back|resolves?|reveals?|reads?|touches?)\b"
    # C) ⛔ AND THE VERB-BEFORE-THE-PATH FAMILY (#13802 review, P1 — 15 freshly-invented
    # spellings measured GREEN): `this diff carries no `tortoise/` path at all`, `this diff
    # touches no `tortoise/` file`, `the `tortoise/` set holds no path here`. Shape A cannot
    # see any of these — it anchors on the COMMAND or on `the `tortoise/` set` and then
    # needs a verb, while here the subject is the DIFF and the path comes AFTER the verb.
    # This alternative matches only a LIMITING qualifier (`no`/`zero`/`only`) between the
    # verb and the path, so an ordinary sentence ("this diff carries `tortoise/x.py`
    # because …") is untouched.
    r"|\b(?:carries|touches|has|holds|contains|includes|names|lists|reads|changes"
    # ⛔ AND EVERY VERB THE REST OF THIS PATTERN CARRIES (#13802 review, round 17, P2 —
    # MEASURED: with `reports|gives|outputs|shows|=|:|->|→`, `no `tortoise/hosted_api.py`` was
    # detector-read and ban-blind, because this family's list was written separately from the
    # general verb class above — a verb the generic class knows and THIS family does not is the
    # same hole in the verb-before-path direction)
    r"|is|are|gets|returns|yields|prints|adds|removes|edits|reports?|gives|outputs?|shows?"
    r"|answers?|resolves?|reveals?|reads?|comes? back)"
    # ⛔ WITH THE SAME INSERTION TOLERANCE FAMILY A CARRIES (#13802 review, round 18, P2 —
    # MEASURED: `This diff carries, at this head, no `tortoise/` path at all` and
    # `This diff carries (measured at this head) no …` were GREEN while the bare form red)
    # ⛔ AND THE PREPOSITION BRIDGE AND THE DASH INSERTS, WHICH FAMILY A HAD AND THIS ONE DID
    # NOT (#13802 review, round 21, P2 — MEASURED in-process at `9024f2e5d0`): the verb list
    # above was widened to "every verb the rest of this pattern carries" and it then carried
    # `resolves?`/`answers?`/`comes? back` WITHOUT the particle bridge family A pairs them with
    # at its own `(?:(?:with|to|as|into|out|of)\s+)?`, so the natural spelling of the very verbs
    # the round-17 widening added stayed GREEN through BOTH arms (`This diff resolves to no
    # `tortoise/` path at all.` / `comes back with no …` / `answers with no …` -> ban-NONE), while
    # family A's `resolves to ZERO` red. Family C also had no dash insert at all. The pre-fix
    # readings are pinned as must-red witnesses in the test below, so this is measured rather
    # than asserted. `\s*` before the qualifier is what lets the bridge's own trailing space be
    # consumed — family A's post-insert tolerance is `\s*` for the same reason, and it cannot
    # cross a word, so the over-block surface is unchanged (verified over all 855 docs).
    r"\s*(?:(?:with|to|as|into|out|of)\s+)?"
    r"(?:,\s*[^,.;\n]{1,30},|\s*\([^)]{0,90}\)|\s*[—–]\s*[^.;\n]{0,30}?(?:[—–]\s*)?)?"
    r"\s*(?:no|zero|only)\s+`?tortoise/[^`]*`?"
    # ⛔ AND THE SYMBOL VERBS IN THEIR OWN ALTERNATIVE (the regex `\b` cannot anchor before
    # `-` or `→`, which is why putting them in the list above left them blind — MEASURED, round 17)
    r"|\s(?:=|:|->|→)\s+(?:no|zero|only)\s+`?tortoise/[^`]*`?"
    r"(?:\s+(?:paths?|files?|modules?|edits?)\b)?"
    r"|\b(?:carries|touches|has|holds|contains|includes|names|lists)\s+"
    r"`?tortoise/[^`]*`?\s+(?:paths?|files?|modules?)\b"
    # D) ⛔ AND THE NO-DIFFERENCE FAMILY (#13802 review, round 10, P2 — MEASURED GREEN).
    # "`tortoise/` is untouched by this diff" passed this ban AND the sibling detector: the
    # anchor needs `set|side|part`, and no answer form carries `untouched`/`unchanged`/
    # `identical`/`byte-identical` — which is exactly how a writer states that the set is
    # empty without saying `NOTHING`. The class the ban declares is "a direction claim
    # about that command"; "untouched" IS that claim, so it belongs here rather than in a
    # footnote limiting the ban's scope.
    r"|`?tortoise/`?\s+(?:is|are|was|were|remains?|stays?)\s+(?:NOT\s+|not\s+)?"
    r"(?:untouched|unchanged|unmodified|identical|byte-identical|the\s+same|no\s+code)"
    # ⛔ AND ITS TOKENS STAY CASE-SENSITIVE, ALONE IN THIS PATTERN (#13802 review, cycle 7 —
    # MEASURED): they are pinned case-BY-case on purpose, and the compile is now
    # `re.IGNORECASE` for the verb/answer alphabet — with the flag reaching this group,
    # `It does **not** discharge A1: it is **transport only** — nothing in `tortoise/`
    # consumes \`result["relations"]\`` (ordinary prose about a directory, not a direction
    # claim) matched and `RECONCILE-A1-A2-A5.md` was refused. The scoped `(?-i:…)` is what
    # keeps one compile from widening an alternative that opted out of it.
    r"|(?:^|[.;:!?]\s|\n)(?:(?-i:NOTHING|Nothing|EMPTY|Empty|NONE|None|ZERO|Zero)|`?\[\]`?)"
    r"\b[^.\n]{0,60}?tortoise/",
    # ⛔ CASE-INSENSITIVE, for the same reason and by the same measurement as the emphasis
    # tolerance above: the detector compiles with `re.IGNORECASE`, and this file's own
    # witness list pins `Prints two paths` as a must-red spelling. Compiled WITHOUT it, the
    # ban refused none of the eleven capitalised/emphasis-wrapped forms measured at cycle 7
    # (`Prints two paths`, `prints Empty`/`Blank`/`Zero`/`Single`/`Both`/`Comment-Only`, …),
    # two of which kept `05-verify.md` GREEN when injected.
    re.IGNORECASE))


def test_the_tortoise_direction_claim_detector_covers_both_directions_and_the_verb_class():
    """⛔ A PATTERN GUARD WITH NO WITNESS IS THE BLACKLIST IT REPLACED, ONE LEVEL UP.

    The five-literal ban this replaced was measured leaking FOUR spellings the document had
    actually used; the first cut of the PATTERN then leaked the whole MEMBERSHIP direction —
    measured in a real `--depth 1` clone over an injected membership claim (the counts are
    not typed here — they move whenever this file gains a test), while the emptiness form
    red. On that checkout the measured sibling
    SKIPS, so nothing else covered it. Every spelling below is pinned, in BOTH directions,
    including the ` there -> ` form the document itself used to carry; the pointer and
    past-tense forms are pinned as MUST-NOT-REFUSE, because refusing the document's own
    pointer to the command is the same defect in the other direction
    (#13795 review, note 5; #13802 merge round, finding 1).
    """
    _cmd = "git diff --name-only origin/main...HEAD -- tortoise/"
    for _claimed in (
        # emptiness
        f"`{_cmd}` prints NO path at all",
        f"`{_cmd}` prints NOTHING",
        "`-- tortoise/` returns NOTHING",
        "`tortoise/` set above is EMPTY",
        f"neither is `{_cmd}`",
        # membership
        f"`{_cmd}` prints `tortoise/hosted_api.py`",
        f"`{_cmd}` returns exactly those two",
        f"`{_cmd}` returns ONE module",
        # the answer one word further from the command — the document's own historical form
        f"`{_cmd}` there -> `tortoise/activation_scorecard.py`",
        # ⛔ AND EVERY VERB THE DETECTOR KNOWS BUT THE FIRST CUT OF THIS BAN DID NOT
        # (merge-round review, P1 — measured in-process: all four were GREEN here while
        # `_typed_answers_to_tortoise_cmd` read them as emptiness answers).
        "`-- tortoise/` shows no path at all",
        "`-- tortoise/` names no path at all",
        "`-- tortoise/` has no path at all",
        "`-- tortoise/` = empty",
        # ⛔ AND THE FIVE THAT SHARED A WITNESS WITH ANOTHER ALTERNATIVE (#13802 review,
        # round 8, P2 — MEASURED): deleting `includes?|contains?` left EVERY witness above
        # still matching, and the only `[=:]` witness is satisfied by `=` alone; `outputs`
        # and `→` were in the detector's list and not in this pattern's.
        "`-- tortoise/` includes no path at all",
        "`-- tortoise/` contains no path at all",
        "`-- tortoise/` : empty",
        "`-- tortoise/` outputs no path at all",
        "`-- tortoise/` → `tortoise/a.py`",
        # ⛔ THE FILLER'S AND ANCHOR'S OWN GAPS (#13802 review, P1 — all four were GREEN)
        "`-- tortoise/` command at this head prints NOTHING",
        "`-- tortoise/` command, at this head, returns NOTHING",
        "the `-- tortoise/` set, at this head, prints NOTHING",
        "which two `tortoise/` paths this diff carries",
        # ⛔ THE NO-DIFFERENCE FAMILY (#13802 review, round 10, P2 — all four were GREEN):
        # "the set is empty" stated as "untouched"/"unchanged"/"identical" instead.
        "`tortoise/` is untouched by this diff",
        "`tortoise/` remains unchanged",
        "the `tortoise/` set is untouched",
        "`tortoise/` is byte-identical to `main`",
        # ⛔ AND THE SAME THREE WORDS THROUGH THE `set` ANCHOR (cycle-5 P2 — all GREEN):
        # alternative D anchors on a BARE `tortoise/`, so it never sees the document's own
        # dominant idiom, "the `tortoise/` set is …". Five of seven words reached the set
        # anchor; these three did not.
        "the `tortoise/` set is identical to `main`",
        "the `tortoise/` set is the same on both sides",
        "the `tortoise/` set carries no code",
        # ⛔ AND THE FIFTEEN FRESHLY-INVENTED SPELLINGS A REVIEW ROUND MEASURED GREEN
        # (#13802 review, round 5, P1): the verb-BEFORE-the-path family (no anchor at all),
        # the `comes back` / `resolves to` / `answers with` verbs, the counted FILE set, the
        # empty-list literal, and the diff-side paraphrase of the comment-only claim.
        "this diff carries no `tortoise/` path at all",
        "the `tortoise/` side of this diff is empty",
        "`git diff --name-only origin/main...HEAD -- tortoise/` comes back empty",
        "the `-- tortoise/` form answers with `tortoise/x.py`",
        "the `tortoise/` set holds exactly four files",
        "at THIS head the `tortoise/` set above is this branch's own comment/docstring-only edits",
        # ⛔ AND THE NOUN-SYNONYM HOLE (#13802 review, round 6, P2 — MEASURED: a one-word
        # edit, `path`->`files`/`diff`, put the refuted claim back GREEN in BOTH arms)
        "`-- tortoise/` prints no files at all",
        "`-- tortoise/` prints no diff",
        # ⛔ AND THE `an empty list` / `single` / BARE-DIGIT tails (#13802 review, round 7,
        # P1 — each measured GREEN while the detector read it; for the DEICTIC and `set`
        # anchors this ban is the ONLY layer, the detector anchoring on a command token)
        "the command this clause prescribes returns an empty list",
        "the `tortoise/` set above is an empty list",
        "`-- tortoise/` comes back with an empty list",
        "`-- tortoise/` prints single path",
        "`-- tortoise/` reports 0 paths",
        # ⛔ AND `both`/`non-empty` WITH THE SET ANCHOR (#13802 review, round 9, P1 — all
        # three measured GREEN through the ban AND the detector AND the spelling ledger)
        "The `tortoise/` set prints both files at this head.",
        "The `tortoise/` set is non-empty at this head.",
        "At this head the `tortoise/` set holds both paths.",
        # ⛔ AND A QUANTIFIER OR A COMMA-BOUNDED INSERTION BETWEEN THE VERB AND THE ANSWER
        # (#13802 review, round 10, P1 — all four measured GREEN through ban, detector and
        # ledger; every quantifier here is in the detector's own strip list)
        "the `tortoise/` set prints only one path",
        "the `tortoise/` set has no fewer than two files",
        "the `tortoise/` set shows more than one path",
        "the `tortoise/` set prints, at this head, nothing",
        # ⛔ AND `six`..`ten` BOUND TO A NOUN THE `no …` ARM ALREADY CARRIED (#13802 review,
        # round 10, P2 — `prints six entries` was GREEN while `prints six files` red)
        "`-- tortoise/` prints six entries",
        "`-- tortoise/` prints ten entries",
        "`-- tortoise/` prints seven diffs",
        "`-- tortoise/` prints blank",
        # ⛔ AND `a single <noun>` — `single` behind an ARTICLE, plus the singular nouns the
        # `entries` list did not carry (#13802 review, round 11, P1 — all measured GREEN, and
        # on the SET anchor the detector returns NOTHING because it needs a command token)
        "the `tortoise/` set prints a single entry",
        "the `tortoise/` set holds a single record",
        "the `tortoise/` set prints a single item",
        "the `tortoise/` set prints a single row",
        # ⛔ AND THE DETECTOR'S MODIFIER WORDS, THE PARTICLE, `no longer`, AND THE NO-DIFFERENCE
        # VERBS ON THE SET ANCHOR (#13802 review, round 12 — 1260 of 1536 verb×modifier×answer
        # cells were detector-only, and the set/deictic anchor saw NONE of them because the
        # detector needs a command token)
        "the `tortoise/` set prints now nothing",
        "the `tortoise/` set is still empty",
        "`-- tortoise/` gives back nothing",
        "the `tortoise/` set is no longer empty",
        "the `tortoise/` set remains unchanged",
        "the `tortoise/` set stays untouched",
        "the `tortoise/` side remains byte-identical to `main`",
        # ⛔ AND THE THREE SENTENCES THE PROXIMITY WINDOW LET THROUGH (#13802 review, round 12,
        # P1 — a head token anywhere within 80 chars neutralised the ban; 21.5% of the guarded
        # corpus sits inside that window). Each is a claim about THIS head introduced by an
        # unrelated citation.
        "Per `#10215`, at this head the `tortoise/` set is empty.",
        "`#10215` shipped that; at this head the `tortoise/` set prints NOTHING.",
        "refs #13572 and #13746: the `tortoise/` set is empty at this head.",
        # ⛔ AND THE TREE'S OTHER SUBJECT NOUNS, THE PRONOUN BEFORE `back`, AND THE ANSWER-FIRST
        # ORDER (#13802 review, round 13, P2 — each measured GREEN while the SAME verb and the
        # SAME answer were refused on `set`: the noun set and the word order were the holes,
        # not the vocabulary)
        "The `tortoise/` diff is empty at this head.",
        "The `tortoise/` files are empty at this head.",
        "The `tortoise/` paths hold nothing at this head.",
        "The `tortoise/` directory prints nothing at this head.",
        "`-- tortoise/` gives us back nothing at this head.",
        "Nothing is in the `tortoise/` set at this head.",
        # ⛔ AND THE MEMBERSHIP DIRECTION IN THAT ORDER (#13802 review, round 16, P2 — the
        # round-13 cut covered emptiness only, so these four were GREEN in both arms)
        "Both files are what the `tortoise/` set holds at this head.",
        "Two paths are what `-- tortoise/` prints at this head.",
        # ⛔ the path answer is NOT pinned: a bare `tortoise/x.py` at a clause start is
        # indistinguishable from a mention in ordinary prose (`; `tortoise/x.py` was in an
        # earlier cut`), and the row's own rule is that an over-block costs more than a false
        # PASS — so this ONE spelling of the membership direction stays a declared residual.
        "A non-empty set is what the `tortoise/` set holds at this head.",
        # ⛔ AND THE TWO FORMS THE SAME ALTERNATIVE COULD NOT SEE (#13802 review, cycle 9, P2 —
        # each MEASURED GREEN here while its other order RED): `` `[]` `` never matched because a
        # `\b` cannot follow `` ` `` or `]` (the boundary is a lookahead now), and the count
        # nouns below `files?|paths?|modules?` were bound in the verb-then-answer arm and not in
        # this one.
        "`[]` is what `-- tortoise/` prints at this head",
        "Six entries are what the `tortoise/` set holds at this head.",
        "Two diffs are what the `tortoise/` diff carries.",
        "Empty is what `-- tortoise/` prints at this head.",
        # ⛔ AND THE INDEFINITE ARTICLE, AND THE PARENTHETICAL (#13802 review, round 14, P1+P2).
        # `returns a non-empty set` is the DETECTOR's OWN pinned witness string at `:1320` and
        # the ban returned False for it; the parenthetical is the insertion the document's own
        # idiom reaches for, and both comma and bare forms of the same sentence were refused.
        "`-- tortoise/` returns a non-empty set",
        "the `tortoise/` set returns a non-empty set",
        # ⛔ AND THE DETERMINER `the` (#13802 review, round 15, P2 — detector-read, ban-blind)
        "`-- tortoise/` returns the empty set at this head",
        "`-- tortoise/` prints the empty list at this head",
        "the `tortoise/` set holds the empty set at this head",
        # ⛔ AND `no <path>` BEHIND EVERY VERB THE PATTERN CARRIES (#13802 review, round 17, P2 —
        # MEASURED: `reports|gives|outputs|shows` and the symbol verbs `=`/`:`/`->`/`→` left
        # `no `tortoise/hosted_api.py`` detector-read and ban-blind)
        "`-- tortoise/` reports no `tortoise/hosted_api.py`",
        "`-- tortoise/` gives no tortoise/",
        "`-- tortoise/` -> no `tortoise/hosted_api.py`",
        "`-- tortoise/` : no tortoise/",
        # ⛔ AND EMPHASIS AROUND THE ANCHOR, AND INSERTIONS IN FAMILY C (#13802 review, round 18 —
        # the pattern tolerated emphasis around the VERB and before the ANSWER but not around
        # the ANCHOR, so `**`-- tortoise/`** prints NOTHING` was GREEN here while the detector
        # read it; family C had none of family A's insertion tolerance)
        "The **`tortoise/`** set prints NOTHING at this head.",
        "**`-- tortoise/`** prints NOTHING at this head.",
        "This diff carries, at this head, no `tortoise/` path at all.",
        "This diff carries (measured at this head) no `tortoise/` path at all.",
        "`None` is what the `tortoise/` set holds at this head.",
        # ⛔ AND THE PRODUCTS THE ENUMERATED CLASSES MISSED (#13802 review, round 20 — emphasis
        # on the VERB plus a backticked answer left the backtick unconsumed; the em dash is this
        # corpus's own house punctuation for the same slot; and `There is …` puts a copula in
        # front of the answer-first arm)
        "`-- tortoise/` **prints** `NOTHING` at this head.",
        "`-- tortoise/` **_prints_** `two` paths at this head.",
        "The `-- tortoise/` set prints — at this head — NOTHING.",
        "There is NOTHING in the `tortoise/` set at this head.",
        # ⛔ AND THE SINGLE DASH, AND THE `no <noun>` SET THE BAN'S OWN COUNT CLASS CARRIES
        # (#13802 review, round 21 — the dash alternative required a CLOSING dash, and the `no`
        # alternative listed 5 of the 13 nouns its own count class lists)
        "The `tortoise/` set prints — NOTHING at this head.",
        # …in both dash spellings (the `[–]` half is the same insertion, pinned separately)
        "The `tortoise/` set prints – NOTHING at this head.",
        "The `tortoise/` set shows no changes at this head",
        "The `tortoise/` set carries no edits at this head",
        "The `tortoise/` set reports no rows at this head",
        # ⛔ AND THE PARTICLE BRIDGE AND THE DASH IN THE VERB-BEFORE-THE-PATH FAMILY (#13802
        # review, round 21, P2 — MEASURED at `9024f2e5d0`: the round-17 widening added
        # `resolves?|answers?|comes? back` to family C's verb list but not the preposition bridge
        # family A pairs them with, and family C had no dash insert at all, so all four strings
        # below were GREEN through the ban AND the sibling detector. Non-vacuity: every one of
        # them FAILS on the pre-fix code and passes here.)
        "This diff resolves to no `tortoise/` path at all, at this head.",
        "This diff comes back with no `tortoise/` file at this head.",
        "This diff answers with no tortoise/ file at this head.",
        "This diff carries — at this head — no `tortoise/` path at all.",
        # ⛔ AND THE SAME CLAIM WITH THE ADVERBIAL FRONTED, WHICH IS THE CORPUS'S OWN HOUSE
        # SPELLING (#13802 review, round 22, P1 — MEASURED at `20f01e1075`: moving `at this
        # head` to the FRONT of the clause defeated the WHOLE answer-first family, in this ban
        # AND in the sibling detector, so no second layer existed. Six of six fronted forms
        # GREEN while every un-fronted twin above red — the same two-orders-one-refused
        # asymmetry rounds 13, 16 and 21 closed three times already, which is why round 22 put
        # the tolerance in the FAMILY PREFIX rather than in the newest branch.)
        "At this head there are no files in the `tortoise/` set.",
        "At this head, there are no entries in the `tortoise/` set.",
        "At this head there is NOTHING in the `tortoise/` set.",
        "At this head NOTHING is what `-- tortoise/` prints.",
        "At the head you merge there are no files in the `tortoise/` set.",
        # ⛔ AND THE SIXTH MEASURED FORM, WHICH ROUND 22 FIXED AND PINNED NOWHERE (#13802 round
        # 23, P2: the comment above said "six of six" while the list held five — this form
        # appeared only in that comment, so a later prefix edit could regress it silently).
        "At this head two paths are what the `tortoise/` set holds.",
        # ⛔ AND THE FORMATTING TOLERANCE ON THE ADVERBIAL ITSELF (#13802 round 23, P1 —
        # MEASURED at `3b901efa84`: the fronted group sat BEFORE the `[*_`]{0,3}` class, so it
        # could not absorb emphasis — the one form the corpus actually writes
        # (`**At this head**` in 4 `docs/` files) walked GREEN, in this ban AND in the sibling
        # detector. Formatting is not content: `:2047` is the contract.)
        "**At this head** there are no files in the `tortoise/` set.",
        "_At this head_ there are no files in the `tortoise/` set.",
        "`At this head` there are no files in the `tortoise/` set.",
        "At **this** head there are no files in the `tortoise/` set.",
        "At this head — there are no files in the `tortoise/` set.",
        # ⛔ AND THE PREPOSITION AND DETERMINERS THE BOUNDED LIST MISSED (#13802 round 23, P2 —
        # MEASURED: `on this head` occurs 11x in `docs/` and `on` is ALREADY in the ban's own
        # head-binding vocabulary; the round-22 list read 5 spellings and left 13 of 18
        # realistic ones GREEN. The class is the STRUCTURE, so these need no new code — only
        # the pin.)
        "On this head there are no files in the `tortoise/` set.",
        "At that head there are no files in the `tortoise/` set.",
        "At the merge there are no files in the `tortoise/` set.",
        "At this merge head there are no files in the `tortoise/` set.",
        "In this head there are no files in the `tortoise/` set.",
        # ⛔ AND THE SHAPES ROUND 23's STRUCTURE STILL DID NOT READ (#13802 review, rounds 24-25
        # — MEASURED at `8f2f2ad25b` in this ban AND in the sibling detector): the
        # determiner-less temporal spelling the round-22 LIST caught and the structural class
        # DROPPED (a regression, P1: RED at `3b901efa84` → GREEN at `8f2f2ad25b`), the
        # `<pronoun> merge` shape for pronouns other than `you`, the hyphenated `merge-time`
        # (Green while its un-fronted twin was RED), and the markdown list / blockquote /
        # numbered-item / indented openings that the clause anchor could not reach.
        # ⛔ AND `1.` IS NOT ONE OF THEM (round 25, P2 — the earlier comment claimed "every one
        # GREEN at `8f2f2ad25b`" and that was FALSE for this string): `1. At this head …` was
        # already refused before the new anchor existed, because the item's own period satisfies
        # the pre-existing `[.;:!?]\s` branch. It is kept as a genuine must-red sentence, NOT as
        # a witness of this change; `1)` and `10)` below are what pin the `\d+[.)]` class.
        "At merge time there are no files in the `tortoise/` set.",
        "At merge-time there are no files in the `tortoise/` set.",
        "At the head we merge there are no files in the `tortoise/` set.",
        "At the head they merge there are no files in the `tortoise/` set.",
        "- At this head there are no files in the `tortoise/` set.",
        "> At this head there are no files in the `tortoise/` set.",
        ">> At this head there are no files in the `tortoise/` set.",
        "1. At this head there are no files in the `tortoise/` set.",
        "1) At this head there are no files in the `tortoise/` set.",
        "10) At this head there are no files in the `tortoise/` set.",
        "  At this head there are no files in the `tortoise/` set.",
        # ⛔ AND THE SAME THREE CLAIMS IN THE ANSWER-FIRST ORDER (#13802 review, round 21 —
        # MEASURED GREEN at this head while every verb-first twin above was refused: the
        # `no <noun>` branch existed only on the verb-then-answer side)
        "There are no files in the `tortoise/` set at this head.",
        "There are no entries in the `tortoise/` set at this head.",
        "There are no changes in the `tortoise/` diff at this head.",
        # ⛔ AND THE THREE AXES ROUND 18 LEFT OPEN (#13802 review, round 19, P1 — every one
        # MEASURED GREEN at the round-18 head, RED here). Round 18 wrote a `\**` class where
        # this file's own contract says `[*_`]{0,3}`, and it put that class BEFORE the space
        # rather than on both sides of it, so: (a) the emphasis-then-space form — the one the
        # sibling DETECTOR reads — was refused by no arm; (b) underlines and (c) emphasis on
        # the NOUN after the anchor were outside the class entirely. (a) is the dangerous
        # direction: detector-read and ban-blind is a refuted claim walking green.
        # ⛔ AND THE ANSWER-FIRST LINK VERBS (#13802 review, P2). ⛔ CORRECTED IN ROUND 22:
        # the first cut of this block claimed "every one GREEN at `9024f2e5d0`" and that was
        # FALSE for six of the eight. Measured by ABLATION (delete exactly the added verbs
        # `|has|have|had|comes? back|resolves?|reveals?|reads?|touches?` from the live pattern
        # and recompile): `Two paths are what the `tortoise/` set has at this head.`, `… set had
        # …`, `Nothing is what `-- tortoise/` reads …`, `Six entries are what the `tortoise/` diff
        # reveals …`, `Both files are what the `-- tortoise/` set touches …` and `` `[]` is what
        # the `tortoise/` set comes back with … `` ALL still red with the verbs gone — they were
        # reaching family E's count/emptiness ANSWER branch plus the `set|diff` subject noun, so
        # they never pinned the verb at all (a vacuous witness, which is the failure mode this
        # file exists to refuse). Only the two COMMAND-anchored spellings actually pinned it.
        # The block below is therefore every one of the eight added verbs, each on the COMMAND
        # anchor, so each needs its own verb to fire: measured RED here, GREEN at `9024f2e5d0`,
        # and GREEN again under the ablation — 8 of 8 (re-derived in round 22, not carried).
        "`[]` is what `-- tortoise/` has at this head.",
        "`[]` is what `-- tortoise/` have at this head.",
        "`[]` is what `-- tortoise/` had at this head.",
        "`[]` is what `-- tortoise/` comes back with at this head.",
        "`[]` is what `-- tortoise/` resolves to at this head.",
        "`[]` is what `-- tortoise/` reveals at this head.",
        "`[]` is what `-- tortoise/` reads at this head.",
        "`[]` is what `-- tortoise/` touches at this head.",
        f"**`{_cmd}`** prints NOTHING at this head.",
        f"***`{_cmd}`*** prints NOTHING at this head.",
        "The _`tortoise/`_ set prints NOTHING at this head.",
        "The `tortoise/` **set** prints NOTHING at this head.",

        # ⛔ AND EVERY DETERMINER THE DETECTOR STRIPS (#13802 review, round 19, P2 — MEASURED:
        # `_typed_answers_to_tortoise_cmd` strips determiners in a LOOP at `:698`, but this ban's
        # count and emptiness answer groups carried only `a`/`an`/`the`, so `returns those two
        # files` was detector-read (`[('returns those two files', False)]`) and ban-blind while
        # the pinned sibling `prints both files` red — a ONE-WORD deletion from a must-red
        # witness. Swept determiner x count x noun x verb: 940 detector-read/ban-blind cells,
        # 0 after the widening, with 0 new refusals over the 855-doc corpus (6 before, 6 after).
        # `\d+` is in the count group because the separate `\b\d+\b` arm only fires when the
        # digit sits DIRECTLY after the verb, so `returns those 2 files` was blind too.
        "`-- tortoise/` returns those two files at this head",
        "the `tortoise/` set prints its two files at this head",
        "`-- tortoise/` returns those 2 files at this head",
        "`-- tortoise/` returns its empty set at this head",
        "`-- tortoise/` (run at this head) prints nothing",
        # ⛔ AND THE ANSWER-FIRST ORDER OF THE SAME DETERMINER CLASS (#13802 review, P2 —
        # MEASURED at `180b95d07d`: every one of these was GREEN while its pinned verb-first
        # twin directly above reds, i.e. round 19's widening reached ONE of the two orders).
        # Non-vacuity: each FAILS on the pre-fix code — measured GREEN at `180b95d07d`, RED
        # here — and `two files` stays as the control that reds at BOTH heads, so the block
        # is not satisfied by the bare-count class the arm already had.
        "Those two files are what `-- tortoise/` prints at this head.",
        "These two files are what `-- tortoise/` prints at this head.",
        "Its two files are what `-- tortoise/` prints at this head.",
        "Just two files are what `-- tortoise/` prints at this head.",
        "All six paths are what `-- tortoise/` prints at this head.",
        "Those 2 files are what `-- tortoise/` prints at this head.",
        "2 files are what `-- tortoise/` prints at this head.",
        "The two comment-only modules are what `-- tortoise/` prints at this head.",
        "The `tortoise/` set (measured with `git diff --name-only origin/main...HEAD -- tortoise/`) is empty",
        # ⛔ AND THE FOUR FORMS THE DETECTOR READ WHILE THIS BAN DID NOT (#13802 review,
        # cycle 6, P1 — each MEASURED `ban=False` in-process against the detector's own
        # `_member`/`_empty` classes; on the `set`/deictic anchors this ban is the only
        # layer, so each was a refuted claim that no arm refused).
        "the `tortoise/` set is non empty at this head",
        "the `tortoise/` set prints one",
        "the `tortoise/` set holds both",
        "`-- tortoise/` prints `tortoise/`",
        # ⛔ AND THE ELEVEN CASE/EMPHASIS SPELLINGS THE DETECTOR READ AND THIS BAN DID NOT
        # (#13802 review, cycle 7, P1 — every one MEASURED `ban=None` against a non-empty
        # detector verdict, and two of them kept the suite GREEN when injected into
        # `05-verify.md`). Formatting is not content: the detector strips `[`*_]` and
        # matches case-insensitively, so each of these is the SAME claim as its plain form.
        "`-- tortoise/` Prints two paths",
        "`-- tortoise/` **prints** the two COMMENT-ONLY modules",
        "`-- tortoise/` `prints` the two comment-only modules",
        "the `tortoise/` set prints Empty",
        "the `tortoise/` set prints Blank",
        "the `tortoise/` set prints Zero",
        "the `tortoise/` set prints Single",
        "the `tortoise/` set prints Both",
        "`-- tortoise/` prints `two` paths",
        "`-- tortoise/` prints _two_ paths",
        "`-- tortoise/` prints Comment-Only",
        # ⛔ AND EMPHASIS MAY WRAP THE ADVERBIAL'S OWN TRAILING PUNCTUATION (#13802 review,
        # round 26, P1 — every one MEASURED GREEN before the fix, and refused by no
        # alternative: round 23 put the emphasis class on every TOKEN of the fronted group and
        # read that as "closed under formatting", but the `,`/`:`/`.`/`—` ENDING the adverbial
        # had to sit OUTSIDE the emphasis. These are the bundle's own house lead-in spellings
        # (`**… measured at this head:**` is used 8x in `PRE-REGISTRATION.md`), and the
        # sibling detector reads none of them, so there was no second layer.
        "**At this head,** there are no files in the `tortoise/` set.",
        "**At this head:** there are no files in the `tortoise/` set.",
        "_At this head,_ there are no files in the `tortoise/` set.",
        "**At merge time,** there are no files in the `tortoise/` set.",
        "**At this head —** there are no files in the `tortoise/` set.",
        "**At this head.** There are no files in the `tortoise/` set.",
        # ⛔ AND THE GRAMMATICAL TAILS OF THE FRONTED ORDER (#13802 review, round 26, P2 —
        # every one MEASURED `fronted=GREEN, unfronted=RED`, the one-order-refused asymmetry
        # rounds 13/16/21/25 closed, one grammatical step out from the pinned
        # `At the head we merge …`). The trailing group is a bounded token run ending in a
        # `merg*` word, so these pin the SHAPE rather than another list of spellings.
        "At the head you are merging there are no files in the `tortoise/` set.",
        "At the head to be merged there are no files in the `tortoise/` set.",
        "At the head post-merge there are no files in the `tortoise/` set.",
        "At the time of the merge there are no files in the `tortoise/` set.",
        "At this merge's head there are no files in the `tortoise/` set.",
        # ⛔ AND THE PARITY HOLDS AT THE BOUND, NOT ONLY BELOW IT (#13802 review, round 27, P2 —
        # MEASURED: the tail's run was `{0,4}` and `[\w-]+`, so parity broke for a path-bearing
        # token and for a run of five; `At the head of the branch you are about to merge …` was
        # GREEN while its un-fronted twin was RED. Both runs now read the same eight-token,
        # `/`-bearing class as family A, so the parity is a property of the two arms rather than
        # a coincidence of their bounds. These two are the bound and the `/`, one each.)
        "At the head of the branch you are about to merge there are no files in the `tortoise/` set.",
        "At the head of origin/main you are about to merge there are no files in the `tortoise/` set.",
        # ⛔ AND THE INTERVENING RUN'S OWN BOUND (#13802 review, round 28, P2 — MEASURED: the
        # round-27 edit widened that run's TOKEN CLASS and left its BOUND at three, so the parity
        # the tail's note claims broke again at a FOUR-token gap: `At the very top outer layer
        # head of the branch you are about to merge …` was GREEN against its RED twin. All three
        # runs — this one and both tail branches — are now the same eight.)
        # ⛔ AND THE WITNESS IS THE FOUR-TOKEN SPELLING, NOT A THREE-TOKEN ONE (#13802 review,
        # round 29, P2 — MEASURED VACUOUS as first pinned: `At the very top outer head …` carries
        # THREE intervening tokens and stays RED with the bound reverted to three, so it could not
        # see the regression it was added for — reverting dropped 0 of the tuple's witnesses. The
        # spelling below carries FOUR, is GREEN at `67bed284f0` and RED here, so it is the one that
        # reds on a revert.)
        "At the very top outer layer head of the branch you are about to merge there are no files in the `tortoise/` set.",
        # ⛔ AND THE FAMILY-A FILLER'S TWO BLIND SPOTS (#13802 review, round 26, P2 — MEASURED
        # detector-read/ban-blind on 600 of 11,220 swept cells: a `/`-bearing token
        # (`origin/main`) and a run longer than six words both defeated the filler, while the
        # detector read the answer). On the anchors where the measured leg skips this ban is
        # the only layer, so each is a hole rather than a narrowing.
        f"`{_cmd}` at the head of origin/main prints NOTHING at this head.",
        f"`{_cmd}` at the head you are about to merge prints NOTHING at this head.",
        # ⛔ AND THE OVER-BLOCK THE `/` WIDENING BUYS, PINNED ON PURPOSE IN THE REFUSING
        # DIRECTION (#13802 review, round 27, P2 — MEASURED `base=GREEN, HEAD=RED`: the peer's
        # count arm accepts `the <count-noun>` with no count in it, so a path-bearing filler
        # carries the class into TRUE prose). Pinned here so a later tightening reds this line
        # and forces the declaration at the widening to be rewritten, instead of the behaviour
        # changing silently while the suite stays green — the same trade, and the same pin, the
        # peer's determiner widening already carries.
        f"`{_cmd}` at the head of origin/main prints the same paths it printed before.",
        # ⛔ AND THE DECLARED OVER-BLOCK, PINNED AS MUST-REFUSE ON PURPOSE (#13802 review, round 26,
        # P2). `All six paths in the `tortoise/` set are covered by tests.` is TRUE prose and this
        # ban REFUSES it: the determiner/count widening's tail is satisfied by its own anchor noun
        # `set` before any verb is read, so the widening reaches a sentence it should not. It sits
        # here so the trade is WITNESSED IN THE FAILING DIRECTION — a later tightening of the tail
        # reds this line and forces the declaration at the widening to be rewritten, instead of the
        # behaviour changing silently while the suite stays green (which is how this finding arose).
        "All six paths in the `tortoise/` set are covered by tests.",
        # ⛔ AND THE CELL THE RECORDED FOLLOW-UP MEASURABLY OPENS, WHICH IS WHY IT WAS NOT APPLIED
        # (#13802 review, round 27, P2 — MEASURED, both directions, at `c42c6cb4`). The round-26
        # lane recorded the tightening as "requiring a verb, not a bare anchor noun, after the
        # `tortoise/` reference", and that was implemented here and MEASURED before it was
        # rejected: it does close four of the five over-block sentences above (`… set are
        # covered by tests`, `… set are the resolver`, `… set were added`, plus the `tree`
        # spelling), leaves the fifth REFUSED (`… set are read by the resolver` — `reads?` is in
        # the verb list), and turns the five MEMBERSHIP CLAIMS below GREEN, because a bare
        # `<N> <noun> in the `tortoise/` set at this head` has no verb after the reference to
        # reach. Those claims state the SIZE of the diff's `tortoise/` set at this head — the
        # direction this file says costs more than an over-block (D5) — so the tightening is a
        # REFUTED prescription, not an unfinished one: the over-block and that false PASS share
        # one substring shape, and separating them needs a predicate-vs-locative model this ban
        # does not have. Their polarity at `c42c6cb4` is RED and it must stay RED; each is pinned
        # here so the next lane that tries the same one-line tightening reds the suite instead of
        # landing it. Measured at the same head, the same five are GREEN under that tightening.
        "Six paths in the `tortoise/` set at this head.",
        "All six paths in the `tortoise/` set at this head.",
        "Six entries in the `tortoise/` set at this head.",
        "Two files in the `tortoise/` set, at this head.",
        "Those two files in the `tortoise/` set at this head.",
    ):
        assert _TORTOISE_SET_DIRECTION_CLAIM.search(_claimed), (
            f"the direction detector does not see {_claimed!r}, so that spelling of a "
            "head-bound answer about the `-- tortoise/` set walks through green — measured, "
            "and it is how the emptiness-only first cut leaked the whole membership "
            "direction (#13802 merge round, finding 1)")
    for _allowed in (
        f"`{_cmd}` is the command that answers it at the head you merge",
        "at `2584a86ec1` the command printed no path at all",
        "read the set from `git diff --name-only origin/main...HEAD`",
        # ⛔ AND THE HISTORICAL FORM MUST STILL BE ALLOWED (#13802 review, round 6, P2 —
        # MEASURED: the unanchored verb-before-path alternative refused a named-head
        # emptiness claim, and the bundle already ships one in README.md)
        "`#10215`'s `8bc2fbed1` touches no `tortoise/` file at all",
        # ⛔ AND ITS CAPITALISED SPELLINGS (#13802, round 21 — MEASURED: both were REFUSED at
        # the round-20 head while the lowercase witnesses above were allowed, because the two
        # binding constants were compiled without `re.IGNORECASE` while the ban itself is not
        # — a sentence-initial `At `…`` is the corpus's own spelling and case is not content).
        # Non-vacuity: the first two FAIL on the pre-fix code (`ban.findall` returns them).
        "At `2584a86ec1` the `tortoise/` set was non-empty — five paths.",
        "In `2584a86ec1` the `tortoise/` set was non-empty",
        "`#10215`'s `8bc2fbed1` Touches no `tortoise/` file at all",
        # ⛔ AND THE `no <noun>` ANSWER-FIRST BOUND MUST NOT REFUSE ORDINARY PROSE (#13802
        # review, round 21). The verb after the anchor is the bound: `are` is not in that list,
        # so this sentence states no direction about the `-- tortoise/` set and stays allowed —
        # the over-block pin for the widening above.
        "No changes to `tortoise/` are needed for this row",
        # ⛔ AND THE MUST-STAY-GREEN FORMS THE WIDENED CLASSES INHERITED (#13802 review, round
        # 26, P3 — MEASURED: `8379b2cfec` and `180b95d07d` both reported "10 of 10
        # must-stay-green prose forms still GREEN", and NEITHER string existed in this file:
        # the tuple held 8 entries, none from the fronted/marker classes those rounds added. So
        # the over-block claim rested on an in-process measurement nobody could re-run, and
        # nothing would red if the next widening made a head adverb in ordinary prose refuse.
        # These pin it: a head adverb that carries NO direction claim stays allowed, in a list
        # item, an ordered item and a blockquote — the three clause openings the marker class
        # adds — and each is a one-edit regression away from the ban's own vocabulary.
        "- At the head of the file, the parser stops.",
        "1. Note the merge base.",
        "> At the head of the file, nothing is imported.",
        # ⛔ AND THE OVER-BLOCK PINS FOR THE DETERMINER WIDENING (#13802 review, P2 —
        # MEASURED GREEN at `180b95d07d` and GREEN here): a determiner + count + noun is
        # ordinary English, so the widening is bounded by the TAIL — but ⛔ NOT BY A VERB ONLY
        # (#13802 review, round 26, P2: this comment said the tail keeps these allowed because
        # `are`/`under`/`were` are not in the anchor list, and that is FALSE for the
        # `in the `tortoise/` set` spelling, where the tail's own anchor noun `set` satisfies it
        # before any verb is read). What these four actually pin is the BARE-anchor spelling
        # (`in `tortoise/``, `under `tortoise/``); the `in the `tortoise/` set|tree|directory`
        # spelling is the DECLARED OVER-BLOCK, measured and stated at the widening itself. The
        # corpus refusal set is 6 before and 6 after either way.
        "Those two files in `tortoise/` are read by the resolver.",
        "All six paths under `tortoise/` are covered by tests.",
        "These three modules in `tortoise/` were touched by #12943.",
        "2 files under `tortoise/` were added by that PR.",
    ):
        assert not _TORTOISE_SET_DIRECTION_CLAIM.search(_allowed), (
            f"the direction detector REFUSES {_allowed!r}, which states the command (or "
            "records a PAST-tense reading at a named head) and asserts no current direction "
            "— refusing it would over-block the document's own pointer, the same defect in "
            "the other direction (#13795 review, note 5)")


#: A `--numstat` figure is a head-bound number: `git diff --numstat origin/main...HEAD --
#: docs/00_index.md` prints NOTHING at this head, so a document that types what it printed
#: is refuted by the command it quotes beside the figure. The pre-fix document did exactly
#: that at four clauses (#13802 review, finding 1 — the D5 row and the file-table section),
#: so the pattern below is the armed detector for that defect class.
_TYPED_INDEX_NUMSTAT = re.compile(
    r"`git diff --numstat origin/main\.\.\.HEAD -- docs/00_index\.md`"
    r"\s*(?:->|prints)\s*`\d+\s+\d+`"
    # ⛔ THE SHORT FORM TOO (#13802 review, P2). The document's own spelling of this
    # clause is "…and its `--numstat` form both print NOTHING", and a regex that knows
    # only the long form walks straight past the figure that replaces `NOTHING` there —
    # measured: `both print `1 1`` left both arms green.
    r"|`--numstat`\s+form\s*(?:both\s+)?(?:prints?|print|returns?|reports?|shows?|"
    r"gives?|lists?|names?|->|\u2192)\s*`\d+\s+\d+`"
    # ⛔ AND A BARE PRESENT-TENSE VERB ON THE `numstat` NOUN (#13802 merge round P2).
    # `the numstat prints `3 3` here` is the same head-bound class and no alternative above
    # saw it — measured: injected into the head document it left this arm GREEN, while the
    # long form, the `--numstat` form and `both print` each red. The participle ("printing
    # `1 1` at a head of its own") is HISTORY and stays allowed, which is why the verb here
    # is present-tense only.
    # the whole verb class, not three verbs: `and the numstat shows `1 1` here`,
    # `and its `--numstat` form gives `1 1` here` and `and the numstat -> `1 1` here` were
    # each measured GREEN at depth 1 (#13802 review, round 6)
    r"|numstat[^`]{0,160}?(?:prints?|print|returns?|reports?|shows?|gives?|lists?|"
    r"names?|->|\u2192)\s*`\d+\s+\d+`"
    # ⛔ AND THE VERB WITHOUT THE COMMAND (#13802 review, round 3, P2). Three of the
    # document's `both print NOTHING` clauses state the CURRENT reading without repeating
    # `--numstat` beside it ("…its numstat printing `1 1` at a head of its own, and at
    # THIS head both print NOTHING"), so keying the short form on the command's name
    # missed them: rewriting THAT clause to `both print `1 1`` came back green. A `both
    # print` followed by a typed pair is this class by construction; the HISTORICAL form
    # ("printing `1 1` at a head of its own") is the participle, not this verb.
    r"|\bboth print\s*`\d+\s+\d+`"
    # ⛔ AND THE PAIR TYPED AFTER THE `--name-only` COMMAND — the spelling the document
    # ACTUALLY USED, found by a fresh reviewer at the merge-round head (#13802 merge-round
    # review, P2). Every alternative above keys on `--numstat`, the `numstat` noun, or a
    # bare `both print`; the document instead wrote
    # ``git diff --name-only origin/main...HEAD -- docs/00_index.md` prints
    # `docs/00_index.md` and `1 1` `` — the identical head-bound numstat pair, and it walked
    # through GREEN while this pattern's own header called itself the armed detector for
    # that class. The doctrine this file states applies to the file itself: a guard may be
    # narrow, but may not describe itself as complete.
    # ⚠️ The verb is required and the span is bounded ON PURPOSE. An unbounded
    # `command …`\d+ \d+`` alternative over-blocked two HISTORICAL quotes in this very
    # document (the `for ONE cite` retraction and the `read THERE` pointer, each quoting a
    # past head's pair a few characters later) — measured, and refusing a historical
    # reading is the same defect in the other direction.
    r"|`git diff --name-only origin/main\.\.\.HEAD -- docs/00_index\.md`\s*"
    r"(?:both\s+)?(?:prints?|returns?|shows?|gives?|lists?|reports?|names?|->|\u2192|:|=)\s*"
    r"(?:`[^`]{0,80}`\s*and\s*)?`\d+\s+\d+`"
    # ⛔ AND THE DEICTIC FORM — `the command prints `docs/00_index.md` and `1 1``. The
    # reviewer measured the arm GREEN on exactly this sentence (#13802 merge-round review,
    # P2): the document names the command as "the command" a clause earlier and then types
    # the pair, so no alternative above sees it. The shape is specific on purpose — the
    # numstat pair is TWO NUMBERS and `docs/00_index.md` followed by such a pair is a typed
    # numstat reading by construction, not an English coincidence.
    r"|`docs/00_index\.md`\s*and\s*`\d+\s+\d+`")


def test_the_document_types_no_numstat_figure_for_the_index_residual():
    """⛔ The D5 row and the file-table rows typed `1 1` — refuted by their own command.

    `git diff --numstat origin/main...HEAD -- docs/00_index.md` prints NOTHING at this
    head, so a document that states what it `prints` is contradicted by the command it
    quotes in the same clause. That is precisely the defect class this file exists to
    catch — a typed head-bound figure — applied to the figure's own command, and it
    survived the earlier rounds because each guard spelled its own phrase list instead
    of recognizing the shape (#13802 review, finding 1).

    NON-VACUITY, MEASURED — and ⛔ the census is NOT typed per line here, because this
    comment is subject to the very rule it describes. An earlier cut of it said "FIVE
    times across FOUR clauses (:108, :134 … :114, :134 again, :143)" and NO revision in
    this branch's range produces that census, and ⛔ **the figure the earlier cut typed was HEAD-BOUND, not false**:
    with this same pattern it reproduces SIX matches across FIVE lines at `6238dde72a`
    (`:102`, `:108`, `:114`, `:134`, `:143`) and at every head from there up to
    `8bdd34c631^` (the blob is unchanged back to `9205cdc72c`, so those ancestors measure
    the same 6/5 — the figure STARTS there, not at `6238dde72a`) — ⛔ and the WINDOW is
    stated rather than left to "every head before"
    (round 12 review, P2: that absolute was false — heads OLDER than the blob carry FEWER
    of the clauses, `aeb4882c18` matching three lines and `f6f202cf5c` four, so a reviewer who
    tested it on one of those would read the figure as unreproducible and "correct" a true
    statement, which is how round 10 went wrong) — whose own commit (`fix(13728)`,
    `#13728`/`#13845`) de-typed those clauses, and it measures ZERO from there and at
    `origin/main` (`28ca66878d`). So a census is quoted WITH its head or not at all: the cut
    that stood here typed it "at `origin/main`", a floating ref, which is the rule this
    paragraph enforces everywhere else (round 10 review, P2 — and the first correction of it
    blamed `#13807`, which landed on a file already at zero; both halves measured here).
    Re-run the pattern at the head you are reviewing and read the map from that, rather than
    trusting a figure written here
    — which is the arm's whole thesis applied to its own docstring.
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    typed = [m.group(0) for m in _TYPED_INDEX_NUMSTAT.finditer(doc)]
    assert not typed, (
        "05-verify.md TYPES the figure that `git diff --numstat origin/main...HEAD -- "
        f"docs/00_index.md` printed ({typed[0]!r}). A numstat is a head-bound figure — "
        "it is EMPTY at this head, so the clause is refuted by the very command quoted "
        "beside it. State the command and let the reader re-derive the set "
        "(#13802 review, finding 1)")


def test_the_typed_numstat_detector_sees_every_spelling_and_only_typed_figures():
    """⛔ THE ONLY ARM IN THIS FILE WITHOUT A WITNESS, AFTER BEING WRONG THREE TIMES.

    Each earlier cut was found deficient by hand-injection alone — a five-literal list, then a
    short form that missed `both print`, then a bare-verb class that missed `shows` / `gives` /
    `->`. A pattern whose classes are never exercised is the blacklist it replaced, one level up;
    its sibling has had a witness for three rounds for exactly that reason. MEASURED: replacing
    this pattern with a never-matching one, or deleting the bare-verb alternative, reds HERE.
    """
    for _typed in (
        "`git diff --numstat origin/main...HEAD -- docs/00_index.md` -> `1 1`",
        "`git diff --numstat origin/main...HEAD -- docs/00_index.md` prints `1 1`",
        "…and its `--numstat` form both print `1 1`",
        "…and its `--numstat` form gives `1 1`",
        "both print `1 1`",
        "and the numstat shows `1 1` here",
        "and the numstat returns `3 3` here",
        "and the numstat -> `1 1` here",
        # the spelling the merge-round reviewer found GREEN (#13802 merge-round review, P2):
        # the pair is typed after the `--name-only` command, not after `--numstat`.
        "`git diff --name-only origin/main...HEAD -- docs/00_index.md` prints "
        "`docs/00_index.md` and `1 1`",
        "`git diff --name-only origin/main...HEAD -- docs/00_index.md` prints `1 1`",
        "`git diff --name-only origin/main...HEAD -- docs/00_index.md` -> `3 3`",
        # ── the DEICTIC spelling the reviewer measured GREEN (#13802 merge-round review, P2)
        "at this head the command prints `docs/00_index.md` and `1 1`",
        "prints `docs/00_index.md` and `1 1`",
    ):
        assert _TYPED_INDEX_NUMSTAT.search(_typed), (
            f"the numstat detector does not see {_typed!r} — that spelling of a head-bound figure "
            "walks through green, which is how each previous cut of this arm was found "
            "(#13802 review, rounds 3, 6, 7 and 8)")
    for _allowed in (
        "its numstat printing `1 1` at a head of its own",
        "`git diff --numstat origin/main...HEAD -- docs/00_index.md` prints NOTHING",
        # a HISTORICAL quote of a past head's pair, sitting a few characters after the
        # command — must stay allowed without a present-tense verb to bind it
        # (#13802 merge-round review, P2: the unbounded cut of this alternative refused it).
        "`git diff --name-only origin/main...HEAD -- docs/00_index.md` and "
        "`git diff --numstat origin/main...HEAD -- docs/00_index.md`, read THERE: the "
        "clause that stood here said the file WAS in the set with the numstat printing `1 1`",
    ):
        assert not _TYPED_INDEX_NUMSTAT.search(_allowed), (
            f"the numstat detector REFUSES {_allowed!r}, which types no current figure — "
            "refusing a historical reading is the same defect in the other direction")


def _added_cite_tokens(diff_text: str) -> set[str]:
    """The `sdk.py:<n>` tokens a `--word-diff=plain` patch ADDS to `docs/00_index.md`.

    Derived from the diff, never typed: `{+…+}` is word-diff's added span, so the set
    is empty exactly when the patch re-points no cite.
    """
    added = "".join(re.findall(r"\{\+(.*?)\+\}", diff_text, re.S))
    return set(re.findall(r"sdk\.py:(\d+)", added))


def _measured_index_word_diff(
        proc: subprocess.CompletedProcess | None = None) -> set[str]:
    """The `sdk.py:<n>` tokens this diff ADDS to `docs/00_index.md`, or `_Unmeasurable`.

    ⛔ A FAILED `git diff` IS UNKNOWN, NEVER "moved nothing" (#13795 review, finding 2).
    The cut before this one wrote
    `moved = _added_cite_tokens(proc.stdout) if proc.returncode == 0 else set()`, so a
    clone that cannot answer the question — depth 1 has no `origin/main`, so git exits
    128 with empty stdout — produced the SAME value a successful read of a diff that
    moves no cite produces. The arm below then reds on a claim that is TRUE there, in
    `test`, the one job that gates the merge.

    ⛔ THIS IS NOW THE FILE'S ONLY DEPTH-DEPENDENT LEG, AND IT SKIPS WITH A REASON
    (#13807 review, finding 1 — that finding measured this same leg rendering a failed read
    as "moved nothing" on the pre-merge branch, which `main`'s `#13795` `_Unmeasurable`
    already fixes; what this PR owes it is an accurate row, not a second repair).
    The `-- tortoise/` sibling that used to share this rule is GONE with its
    dependence: that arm is a TEXT ban and takes no measurement, so it fires in every
    shard. This one cannot — a re-point is a property of the DIFF, not of the document —
    so an unmeasurable head SKIPS here rather than being read as "moved nothing". Scoping
    that residual is finding 1's fix, not this PR's: converting this leg to a text ban is
    a separate change, because the token it checks is derived from the diff.
    """
    if proc is None:
        proc = subprocess.run(
            ["git", "diff", "-U0", "--word-diff=plain", "origin/main...HEAD", "--",
             "docs/00_index.md"],
            cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise _Unmeasurable(
            f"exit {proc.returncode}: {detail[0] if detail else '<no output>'}")
    return _added_cite_tokens(proc.stdout)


#: The spellings that assert THIS diff re-points a cite in `docs/00_index.md`. Each was
#: measured FALSE at the head the arm below was written for (#13746 review, round 3,
#: finding 4): the file's one changed line is the `#12296` row's DESCRIPTION of its own
#: `:27861` clause, and no `sdk.py:<n>` token moves.
#:
#: ⛔ THIS SIDE IS A FINITE LITERAL LIST, AND IT SAYS SO (#13795 review, round 2,
#: finding 3). Only the MEASUREMENT half of the arm reads the live diff; a re-worded
#: re-point claim is NOT detected, so the docstring below must not imply that it is. The
#: shape match the sibling `-- tortoise/` detector uses is the right fix and is filed as a
#: follow-up rather than smuggled into a docs PR.
#: The spellings that assert THIS diff re-points a cite in `docs/00_index.md`. Each was
#: measured FALSE at the head the arm below was written for (#13746 review, round 3,
#: finding 4): at THAT head the file's one changed line was the `#12296` row's
#: DESCRIPTION of its own `:27861` clause, and no `sdk.py:<n>` token moved.
#:
#: ⛔ PAST TENSE ON PURPOSE, AND THE READING CARRIES ITS HEAD (#13802 review, round 5,
#: P3 — MEASURED): this paragraph used to state that clause in the PRESENT tense, and at
#: this head `git diff --numstat origin/main...HEAD -- docs/00_index.md` prints NOTHING —
#: the file has no changed line at all, because `main` absorbed both re-points before this
#: branch was re-cut. The same file retracts the attribution a hundred lines below, so the
#: two statements contradicted each other inside one file and this comment was the one
#: rendered as truth beside the arm.
#:
#: ⛔ THIS SIDE IS A FINITE LITERAL LIST, AND IT SAYS SO (#13795 review, round 2,
#: finding 3). Only the MEASUREMENT half of the arm reads the live diff; a re-worded
#: re-point claim is NOT detected, so the docstring below must not imply that it is. The
#: shape match the sibling `-- tortoise/` detector uses is the right fix and is filed as a
#: follow-up rather than smuggled into a docs PR.
#: The spellings the arm above refuses wherever this tree has NO measurement showing
#: that this diff moves a cite (cite sets EQUAL, or the base ref unreadable —
#: `#13802` review P1). ⛔ Kept beside `_INDEX_REPOINT_CLAIMS` on purpose: on a head
#: whose cite sets DIFFER **and** whose base is readable, the arm above stands DOWN —
#: legitimately, because the attribution may be true there — and the claims list is the
#: only arm that still reads the document. So every phrase here must also be covered
#: there, which `test_every_attribution_spelling_is_also_a_live_diff_claim` MEASURES
#: rather than leaves to a reader. That arm exists because the pairing was
#: hand-maintained four review rounds running (`#13802` review, finding 2, residual):
#: the sixth phrase below had no counterpart at all, and the fourth had only the
#: PREFIXED claim `this repair re-pointed it once more` — which a document saying only
#: `re-pointed it once more` does not contain (the claims arm tests `claim in doc`, so
#: coverage needs the claim to be a substring of the PHRASE, not the other way round).
#:
#: ⛔ THE PAIR-PINNING ARM THAT USED TO REQUIRE THE FIRST PHRASE IS DELIBERATELY NOT
#: CARRIED (#13572/#13802 merge of `main`'s #13795). `main` kept
#: `test_the_bundles_stated_index_re_point_lands_in_the_file_it_re_points`, whose first
#: leg was `assert m` on `re-pointing that row's `sdk.py` cite from **`:OLD`** … to
#: **`:NEW`**` — i.e. it REQUIRED the false attribution to exist in the document, so the
#: sentence this PR deletes could not be deleted while that arm lived. It is replaced by a
#: SCOPED current-value leg inside
#: `test_the_cites_this_document_attributes_to_this_diff_are_measured_not_typed`, which
#: checks the CONTENT a cite resolves to wherever the file carries that cite, instead of
#: mandating a phrasing that attributes `main`'s move to this diff.
#:
#: ⛔ THE ABSENCE LEG IS RESTORED, AND `_RETIRED_INDEX_CITES` BELOW IS HOW (#13802
#: review, P2 — measured). The deleted arm also asserted the OLD cite was ABSENT, and the
#: merge that dropped it disclosed the hole without closing it: MEASURED at the pre-fix
#: head, injecting `sdk.py:21604` into `docs/00_index.md` ALONGSIDE the surviving
#: `sdk.py:21715` left the guard suite green (`18 passed`). The stated reason for not
#: re-adding it — that "`main` re-deriving that row again is the normal case this PR exists
#: to stop reding on" — does not hold for an ABSENCE assertion: a re-point moves a cite
#: FORWARD (`:21556` -> `:21604` -> `:21679` -> `:21715`), so a legitimate head can only
#: retire MORE of these, never restore one. Only a deliberate revert can, and a revert is a
#: decision a guard should make a lane state explicitly.
#:
#: ⛔ WHY A TYPED LITERAL IS RIGHT HERE IN A FILE WHOSE THESIS IS "TYPE NO HEAD-BOUND
#: ANSWER": these are not ANSWERS about the current head (which rot the moment `main`
#: moves) — they are RETIRED values, each falsified by CONTENT and each carrying the PR
#: that retired it. "The index does not carry this any more" is stable in exactly the way a
#: current address is not.
_RETIRED_INDEX_CITES = (
    # the `#7949`/`#9080` capstone row's `sdk.py` receipt cite: #13551 `:21556`->`:21604`,
    # #13755 `:21604`->`:21679`, #13750 `:21679`->`:21715`.
    "sdk.py:21556", "sdk.py:21604", "sdk.py:21679",
    # the `#12296` entity-verdict row's filter cite: #13551 `:28019`->`:28067`, then
    # `b68883c11b` ->`:28142`, then #13724 ->`:28178`.
    "sdk.py:28019", "sdk.py:28067", "sdk.py:28142",
)


#: ⛔ AND THAT IS STILL NARROWER THAN WHAT IT REPLACES — stated, not papered over (#13802
#: merge round P2). The rest of what the deleted arm did is not restored: it DERIVED the
#: old cite from the document's own `from **`:OLD`** … to **`:NEW`**` phrasing, which this
#: PR deletes, so the pair is a fixed list here and a NEWLY retired cite is not caught
#: until it is added above. The restoration lives in the arm that reads the document (not in
#: a comment beside a deleted function), because a reader must be able to see WHY `main`'s
#: arm is gone without diffing two heads.
_ATTRIBUTION_PHRASES = (
    "re-pointing that row's `sdk.py` cite from",
    "and THAT shift is this repair",
    "re-points it once more, to",
    "re-pointed it once more",
    "re-pointed that cite once more",
    "for exactly ONE cite",
)


_INDEX_REPOINT_CLAIMS = (
    "this diff carries ONE `docs/00_index.md` change — the capstone row's",
    "it IS in the set, for ONE cite",
    "for exactly ONE cite",
    "THIS repair re-points it once more, to",
    "this repair re-pointed that cite once more",
    # ⛔ The two the list above missed while the merged head still carried them
    # (#13746 review, round 3, finding 4): both say the capstone row's cite move is
    # THIS repair's, and the tree refutes both — ⛔ and `cmp` does NOT print nothing
    # (round 8, P1, MEASURED): it differs at line 45175. What carries the refutation is
    # the RANGE, not a whole-file identity: `:21715` and `:28178` both sit before 45175,
    # and the live three-dot diff adds no `sdk.py:<n>` token.
    "and THAT shift is this repair",
    "this repair re-pointed it once more",
    # ⛔ The two BARE spellings `_ATTRIBUTION_PHRASES` above carried with no counterpart
    # here (#13802 review, finding 2, residual): on a head whose cite sets DIFFER and
    # whose base is readable the attribution arm stands down, and without these entries
    # the live-diff arm below
    # did not know the phrasings either — so the attribution was covered by NEITHER arm,
    # which is the fail-open Finding 2 names in its second form. Both are absent here.
    "re-points it once more, to",
    "re-pointed that cite once more",
    # ⛔ The THIRD spelling with no counterpart (#13802 review, finding 2, residual — one
    # instance over from the two above): on a head whose cite sets DIFFER and whose base
    # is readable the `_attributions` arm stands down, and without this entry the
    # live-diff arm below does not know this phrasing either, so the attribution is
    # covered by NEITHER arm. Absent here, measured.
    "re-pointing that row's `sdk.py` cite from",
    # ⛔ The FOURTH, and the one the previous passes still missed (#13802 review, finding
    # 2, residual — the same class, one instance over again). The bare phrase in
    # `_ATTRIBUTION_PHRASES` had only the PREFIXED counterpart `this repair re-pointed it
    # once more`, and that is not a counterpart for the purpose this list serves: the arm
    # asks whether the CLAIM occurs in the document, so a document saying only
    # `re-pointed it once more` contains no claim and escapes both arms on a diff-set
    # head. Absent here, measured (`grep -cF` -> 0 at this head).
    "re-pointed it once more",
)


def test_every_attribution_spelling_is_also_a_live_diff_claim():
    """Every phrase the attribution arm refuses must ALSO be claimed-refutable.

    ⛔ The two arms are not interchangeable. `_ATTRIBUTION_PHRASES` is executed only
    where this tree has no measurement that supports an attribution (cite sets EQUAL, or
    the base ref unreadable); on a head whose cite sets DIFFER **and** whose base is
    readable it stands down — correctly, because the attribution may be true there — and
    `_INDEX_REPOINT_CLAIMS` is the only arm that still reads the document. That state is
    the normal one for a branch that has not been re-based after `main` re-derived a row
    (`#13617`/`#13675`, `#13724`, `#13750`, `#13724`, `#13790`). A phrase present in the
    first tuple and absent from the second is therefore covered by NEITHER arm there,
    with no red row — the fail-open `#13802` review finding 2 names, repaired by hand
    once per round (each pass adding only the instance in front of it).

    The coverage relation is a CONTAINMENT one and its direction is the whole point: the
    claims arm tests `claim in doc`, so a document containing only `PHRASE` is refused
    only if some claim is a SUBSTRING OF THE PHRASE (`claim in phrase`). The prefixed
    claim `this repair re-pointed it once more` does NOT cover the bare `re-pointed it
    once more`, which is how the fourth instance survived the pass that added the
    prefixed one — measured in-process against this head's document with the bare phrase
    injected: the claims list selects `[]` and the arm asserts nothing.

    NON-VACUITY, MEASURED: with the bare claim removed (the pre-fix state) this row reds
    on exactly `['re-pointed it once more']`; with it, no phrase is uncovered. The row
    needs no base ref, so it is live in the depth-1 checkout too — it is not one of the
    arms that the unreadable-base fallback can stand down.
    """
    uncovered = [
        phrase for phrase in _ATTRIBUTION_PHRASES
        if not any(claim in phrase for claim in _INDEX_REPOINT_CLAIMS)
    ]
    assert not uncovered, (
        "these phrasings assert that this diff re-points a `docs/00_index.md` cite and "
        "are refused by the attribution arm wherever this tree has no measurement "
        "supporting an attribution, but no `_INDEX_REPOINT_CLAIMS` entry is a SUBSTRING "
        "of them — so on a head whose cite sets DIFFER and whose base is readable (where "
        "that arm stands down) the attribution is covered by NEITHER arm and the row "
        f"stays green: {uncovered}. Add the bare spelling to `_INDEX_REPOINT_CLAIMS` "
        "(#13802 review, finding 2, residual)")


#: ⛔ THE `#13724` / `#13750` ATTRIBUTION SWAP — the D5 chain with the two commits in the
#: wrong roles (#13802 review, F2 — MEASURED at this head). The measured chain is
#: `:21556` →#13551→ `:21604` →#13617/#13675 `+75`→ `:21679` →**#13724 `+36`**→ `:21715`,
#: and `#13750` is the DOCUMENT re-point to `:21715` — it moves no `tortoise/` path at all:
#:
#:   git show aeb4882c18 --name-only --format='' | grep -c '^tortoise/'   -> 0     (#13750)
#:   git show e679d22286 --stat --format='' -- tortoise/sdk.py            -> 46 +, 10 -   (#13724)
#:   git show e679d22286^:tortoise/sdk.py | grep -n '"stats": _capture_stats_with_step_witness'  -> 21679
#:   git show e679d22286:tortoise/sdk.py  | grep -n …same…              -> 21715  (the `+36`)
#:   git show e679d22286 -- docs/00_index.md | grep 'sdk.py:'           -> 28142 -> 28178
#:
#: ⛔ A TEXT ban and not a measurement, for the reason the direction ban above states: those
#: `git show`s need history the depth-1 `test` job does not have, so an arm keyed on them
#: asserts NOTHING in the job that gates merges. The three spellings below are the ones that
#: actually shipped and survived a merge inside this PR; a RE-WORDED swap is the documented
#: residual, caught only by review.
_PR_ATTRIBUTION_SWAP = (
    "`#13750` (`aeb4882c18`) a further `+36`",
    "`#13750` moved the anchor `+36`",
    "`#13750`/`#13786`",
)


#: The corrected attribution that must STAY: a red row above must not be made green by
#: deleting the clause, which would drop the chain's provenance instead of fixing its roles.
_PR_ATTRIBUTION_CORRECT = "`#13724` (`e679d22286`) a further `+36`"


def test_the_13724_13750_attribution_is_not_swapped_in_the_verify_doc():
    """⛔ The sentence this PR exists to make true still gave `#13750` `#13724`'s `+36`.

    F2 of the #13802 review, and it is the SECOND round in which the document disagreed
    with the PR body on this pair — the body already said `#13724` `+36`, the shipped D5
    cell did not. The tree refutes the swap in four commands (see `_PR_ATTRIBUTION_SWAP`),
    and the module-level tuple records them because this arm cannot run them here.

    NON-VACUITY, MEASURED: against the PRE-FIX document (`git show
    ee699827b4:docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md`) the first
    and second entries hit the D5 cell and the third hits both `main`'s own
    `#13750`/`#13786` clauses, so this arm reds on exactly three sites there; against the
    fixed document all three are absent and `_PR_ATTRIBUTION_CORRECT` is present.
    Re-measure the commands, never the sentence: a RE-WORDED swap passes this ban, which is
    the stated residual.
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    swapped = [spelling for spelling in _PR_ATTRIBUTION_SWAP if spelling in doc]
    assert not swapped, (
        f"05-verify.md carries the #13724/#13750 ATTRIBUTION SWAP ({swapped[0]!r}): "
        "`#13750` (`aeb4882c18`) moves NO `tortoise/` path — it is the DOCUMENT re-point to "
        "`:21715` — while the `+36` code move to `:21715`, and the `#12296` row's "
        "re-derivation to `:28178`, are `#13724`'s (`e679d22286`). Re-derive with the four "
        "commands on `_PR_ATTRIBUTION_SWAP` and state each role from them "
        "(#13802 review, F2)")
    assert _PR_ATTRIBUTION_CORRECT in doc, (
        "05-verify.md no longer states the measured attribution "
        f"{_PR_ATTRIBUTION_CORRECT!r} — deleting the clause is not a fix for the swap "
        "above, it drops the chain's provenance. Restore it with its roles taken from "
        "`git show e679d22286^|:tortoise/sdk.py` (#13802 review, F2)")


def test_the_bundles_index_cite_re_point_claim_is_corroborated_by_the_live_diff():
    """⛔ The doc said this diff's ONE `docs/00_index.md` line WAS a cite re-point.

    It is not, and the commands printed beside the claim refute it: at `09f5c99afd`,
    the head this was measured on, `git diff --name-only origin/main...HEAD --
    docs/00_index.md` printed NOTHING (2-dot and 3-dot both empty there, and
    `git rev-parse origin/main:docs/00_index.md HEAD:docs/00_index.md` printed the same
    blob twice) — ⛔ re-run both at the head you merge: `main`'s own `#13824`
    (`9205cdc72c`) moved that file after that head, so the pair is a MEASUREMENT there
    and no id is typed here (#13802 review, round 8, P1), while
    `git diff -U0 --word-diff=plain origin/main...HEAD -- docs/00_index.md` moves no
    `sdk.py:<n>` token in either direction (`grep -c 'sdk.py:21679' docs/00_index.md` ->
    `0` here AND on `origin/main`; `sdk.py:21715` -> `1` on both). An earlier cut
    attributed that residual to the `#12296` entity-verdict row's DESCRIPTION of its
    own `:27861` clause; that edit is `origin/main`'s now.

    So "the `1 1` numstat IS this cite re-point" was the same defect class the last four
    review rounds kept finding, and it reached the rail because nothing read this
    section (#13746 review, round 3, finding 4). The arm READS the live diff instead of
    freezing which direction the answer points, because the residual moves whenever
    `main` re-derives a row.

    NON-VACUITY, MEASURED: `_added_cite_tokens` is exercised on a CRAFTED patch that
    DOES move a cite, so an empty reading of the real diff is a fact about this diff and
    not an inert extractor; and on the pre-fix document the three surviving spellings
    are present with `moved` empty, so the loop reds on the first one.

    ⛔ AND A DIFF THAT CANNOT BE TAKEN IS UNKNOWN, NOT EMPTY (main's #13795 review,
    finding 2 — `_measured_index_word_diff`), BUT THE ARM DOES NOT SKIP ON IT (#13802
    review, P1). Both legs that shell out to `git diff origin/main...HEAD` now report
    UNKNOWN rather than `set()`; where the read is UNKNOWN this leg still refuses a claim,
    because every job that runs a suite is `fetch-depth: 1`, and a skip there asserts
    nothing in the only job that gates the merge. Over-block, not false pass.
    """
    assert _added_cite_tokens("a [-sdk.py:21679-]{+sdk.py:21715+} b") == {"21715"}, (
        "_added_cite_tokens cannot read a word-diff patch that DOES move a cite, so the "
        "arm below would pass on an empty set for the wrong reason")
    with pytest.raises(_Unmeasurable) as _raised:
        _measured_index_word_diff(subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="",
            stderr="fatal: bad revision 'origin/main...HEAD'\n"))
    assert "128" in str(_raised.value), (
        "a failed `git diff` on `docs/00_index.md` is not reported as UNMEASURABLE with "
        "its exit code, so the reason a red row shows cannot distinguish a shallow "
        "clone from any other failed read (#13795 review, finding 2)")
    # ⛔ AN UNREACHABLE MEASUREMENT TAKES THE UNCONDITIONAL DIRECTION, NOT A SKIP
    # (#13802 review, P1 — the sister arm's `pytest.skip` was the same defect). This leg
    # is the ONLY one that still reads the document on a head whose cite sets DIFFER, and
    # every job that runs a suite checks out at `fetch-depth: 1`, so a skip here asserts
    # nothing wherever the rail reads it. With the base unreadable, NOTHING shows the diff
    # adds a `sdk.py:<n>` token — so an empty `moved` is not a measurement, and a claim may
    # not be carried on it. The `_why` it builds names which case it is, so an UNPROVABLE
    # reading is never passed off as a MEASURED one; the cost is an OVER-BLOCK on a head
    # that really does move a cite, which outranks a false PASS.
    try:
        moved = _measured_index_word_diff()
        _why = ("the live three-dot diff adds no such `sdk.py:<n>` token "
                f"(added: {sorted(moved) or 'none'})")
    except _Unmeasurable as _exc:
        moved = set()
        _why = (f"the live three-dot diff is UNPROVABLE here — `{_exc}`; "
                f"{_base_ref_absent_detail() or 'the base ref resolves but has no merge base with HEAD in this clone — `git fetch origin main` alone does not fix that, the HISTORY is what is missing'}, so "
                "nothing shows this diff adds a cite token at all")

    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    for claim in _INDEX_REPOINT_CLAIMS:
        if claim not in doc:
            continue
        window = doc[doc.index(claim):doc.index(claim) + 500]
        named = set(re.findall(r"`?:(\d+)`?", window))
        assert named & moved, (
            f"05-verify.md claims this diff re-points a `docs/00_index.md` cite "
            f"({claim!r}) and names {sorted(named)}, but {_why}. The claim is refuted "
            "by the command the document prints beside it: read the set from "
            "`git diff -U0 --word-diff=plain origin/main...HEAD -- docs/00_index.md` "
            "at the head being merged, and state the command rather than a head-bound "
            "pair (#13746 review, round 3, finding 4)")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
