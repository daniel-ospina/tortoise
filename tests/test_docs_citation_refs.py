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


#: ⛔ ONE GAP, FIVE UNIT KINDS, NO BUDGET — AND `_GAP_UNIT` IS THE LIST (#13921 review, F5).
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
#: gone: aliasing both to `_GAP` made the selection between them a no-op that kept these
#: paragraphs alive.
#:
#: The gap is a run of FIVE UNIT KINDS and nothing else. Listing them is not an enumeration of
#: SPELLINGS — the earlier attempts died because they enumerated those — it is the closure
#: itself, stated once, and each kind is defined structurally:
#:
#:   1. a single character — a non-word one, plus `_`, which Python counts as a word character
#:      and markdown emphasis depends on. `&`, `<` and `[` are EXCLUDED from this class: each of
#:      them has its own unit below, so each has exactly one way to parse;
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
#:      form of this unit dropped it, and ``... -- tortoise/` R&D prints TWO paths`` then
#:      returned `[]` — a claim left GREEN. `&` is a character `origin/main`'s gap already
#:      admitted, and a closure that drops a form the previous rule had is a regression wearing
#:      a widening's clothes.
#:   5. an UNTERMINATED bracket — `_OPEN_BRACKET` below, a `[` with no reachable `]`.
#:
#: ⛔ `_OPEN_BRACKET` IS THE ONE SAME-FIRST-CHARACTER PAIR, AND ITS LOOKAHEAD IS WHY THE RUN IS
#: STILL LINEAR (#13921 review, P2-3, MEASURED). `_BRACKET` and `_OPEN_BRACKET` both begin `[`,
#: so the reason this block used to give — "each kind begins with a unique first character" — is
#: FALSE, and a stale reason in this file is the defect class it keeps reviewing itself for. The
#: real reason is that the two are DISJOINT by construction: `_BRACKET` matches only when a
#: closing `]` is reachable, and `_OPEN_BRACKET` is guarded by `(?![^\]\n]*\])`, i.e. it matches
#: only when none is. The one input where both can start is a bracket whose interior crosses a
#: soft break (`[a\n]`): `_BRACKET` takes it whole while the lookahead, being line-bounded,
#: cannot see the `]` — the same bounded residual as the unterminated `<` run below, not an
#: exponential. MEASURED at this head: 200 x `[` -> 0.0009 s, 40 x `[\]` -> 0.0000 s.
#:
#: A SOFT BREAK is not a paragraph break (`7557a52cf5`'s measurement, kept): a single newline is
#: crossed, a BLANK line is the boundary — and a blank line ends the gap with no guard of its
#: own, because `_SOFT` refuses to cross one and the single-character unit excludes `\n`.
#: ⛔ `<` IS NOT AMBIGUOUS ANY MORE (#13921 review, P2-3): the single-character unit excludes
#: it, so `<` reaches only `_TAG` — the old note that "`<` can begin both a tag and a
#: single-character unit" describes a class member that no longer exists. What remains is the
#: cost of a tag probe on an UNTERMINATED `<` run (measured 28 characters -> 0.19 s, growing
#: about x1.7 per character), which is a DECLARED residual pinned by its own timing witness, not
#: a claim of linearity. Otherwise the measured cost is linear: 8/16/28/40/80/200 spaces and 40
#: repeated units of each kind -> 0.000-0.002 s; the ambiguous form `7557a52cf5` replaced took
#: 15.8 s at 24 spaces.
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
#: `]` it protects cannot end the run.
_BRACKET = r"\[(?:\\.|[^\]\n]|" + _SOFT + r")*\]"
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
_OPEN_BRACKET = r"\[(?![^\]\n]*\])"
#: ⛔ ONE GAP, ONE UNIT LIST, AND THE UNITS ARE WHAT THE MATCHER WALKS (#13921 review, F5).
#: `_GAP_UNIT` is the single alternation every separator character goes through; `_GAP` is its
#: `*`-closure and is kept as the STATEMENT of the separator for readers. The matcher no longer
#: relies on a greedy `_GAP*` to skip the separator — it walks `_GAP_UNIT` itself, so the
#: separated position list is the thing that decides where a verb may start (see the scan below).
_GAP_UNIT = (r"(?:[^\w<&[\n]|_|" + _TAG + r"|" + _BRACKET + r"|" + _AMP + r"|"
             + _OPEN_BRACKET + r"|" + _SOFT + r")")
_GAP = _GAP_UNIT + r"*"

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
    #  * ⛔ A SLICE FULL-MATCH IS NOT ENOUGH TO SAY "UNIT BOUNDARY" (#13921 review, F4): a unit's
    #    LOOKAHEAD cannot see past the slice it is handed, so `[is]` would let `[` match the
    #    UNTERMINATED-bracket unit on the prefix `" ["` and grade the `is` INSIDE the bracket (and
    #    a tag's quoted `title="a: b"` would do the same through its `:`). So the boundaries are
    #    ENUMERATED from the document itself, walking `_GAP_UNIT` with `match` — which is exactly
    #    the parse the greedy closure took, since each alternative requires a character and they
    #    are tried in order — and a candidate that is not one of those positions is not this
    #    occurrence's verb. That enumeration is also what replaces the blank-line `break` this
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
        # command and the verb is a run of separator UNITS (the four kinds stated at the
        # constants, not a character class), and the budget and the boundary differ BY SUBJECT:
        # ⛔ NO BUDGET, NO SECOND GAP, AND NO `;`/`|` TERMINATORS (#13921 review, F5). This
        # sentence used to say "the FULL command admits at most 40 units and the ABBREVIATION is
        # unbounded; in both, `;` and `|` stop the wide gap and a `.` does not" — all four
        # clauses false since the prose admission was retired: there is ONE gap, it has no
        # budget, and `;`, `|` and `.` are ordinary single characters. What DOES hold, and is
        # what the witnesses pin: a SOFT break is crossed and a BLANK line is the boundary. The
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
    # ⛔ `"this diff carries no `tortoise/` path"` IS DELIBERATELY **NOT** A ROW, and the
    # reason is a live counterexample rather than taste: that exact string still occurs in
    # the document exactly once, as a QUOTED RETRACTION (“‘this diff carries no `tortoise/`
    # path’ — which the `--refresh` above made FALSE”). Retiring it would red the document
    # for retracting a falsehood, i.e. the over-block this ledger already documents. The
    # clause that ASSERTED it was rewritten to a structural statement (`#12943` landed on
    # `main`, so it sits on both sides of the merge) that needs no reading of the set at all.
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
    from tools.hosted_api_cite_census import _note_paths, _sweep, census, lifetime_query_sites

    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")

    # (a) AGREEMENT, measured in-process by the checker the document quotes.
    # ⛔ THE PIVOT IS RE-DERIVED, BECAUSE `--check` RE-DERIVES IT (#13807 review round 7,
    # P1 — MEASURED). `census()`'s default is the FROZEN `DEFAULT_PIVOT`; the verb's
    # `--check` path calls `census(sites[0])` with `sites = lifetime_query_sites()` and is a
    # SINGLE PASS (`main()` returns on the first `rc`, so only `--refresh` iterates — corrected
    # in round 8, which is why this arm needs no loop). Measuring `census()` here therefore
    # asserted agreement on a path `--check` does not take: MEASURED at `c2e8b42336`,
    # `_sweep(census())` -> `0` while `_sweep(census(lifetime_query_sites()[0]))` -> `1`, so
    # this arm reported "agreement" over a tree the verb it names reported as STALE —
    # precisely the false PASS the arm exists to refuse, reproduced inside the arm. The shape
    # `--check` refuses (more than one site) is refused here too rather than silently graded
    # on the wrong pivot.
    _lifetime_sites = lifetime_query_sites()
    assert len(_lifetime_sites) == 1, (
        f"{len(_lifetime_sites)} `query(LIFETIME_MEMORY_QUERY)` sites in `tortoise/hosted_api.py` "
        f"({_lifetime_sites}). `--check` refuses to run on that shape, so this arm cannot "
        "claim the verdict that verb prints (#13807 review round 7, P1)")
    verdict = _sweep(census(_lifetime_sites[0]), refresh=False)
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


class _Unmeasurable(RuntimeError):
    """A MEASURED reading could not be taken here, so its direction is UNKNOWN.

    ⛔ IT NO LONGER DESCRIBES A `-- tortoise/` SET (#13807 review round 4, finding 2). The
    census arm takes no measurement at all now, so nothing measures that set; the one
    surviving reader is `_measured_index_word_diff`, over `docs/00_index.md`. The old
    docstring named a mechanism this file no longer has, which is how a description rots
    into a false claim about the code beside it.
    """


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
    # ⛔ AND THE FOUR SEPARATOR UNITS, EACH PINNED (#13900 review, cycles 9-13). The unit list IS
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
        "declared limit at `_GAP`, so the declaration must change with it")
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
    # ⛔ AND THE RETRY EARNS ITS OWN WITNESS, WHICH ONLY A VERB *INSIDE* A UNIT CAN GIVE
    # (#13921 review, F2/F4, MEASURED). Reducing the scan to its FIRST candidate reddened nothing
    # in this file, because under the old anchor the first candidate was already the occurrence's
    # verb. Now that the scan starts at the command, a verb sitting INSIDE a unit (`[is]`, a tag's
    # quoted `title="a: b"`) comes first and its separator IS a full gap match — it is only not a
    # UNIT BOUNDARY — so the scan must continue past it. Each row must grade the OUTER verb.
    # Non-vacuity: a slice full-match instead of the boundary list grades the inner verb here
    # (measured: `[is]` then yields `('is ] prints TWO paths', False)`).
    for _inner, _row in (("[is]", "`-- tortoise/` [is] prints TWO paths"),
                         ('<span title="a: b">',
                          '`-- tortoise/`<span title="a: b">prints TWO paths')):
        _seen_inner = _typed_answers_to_tortoise_cmd(_row, "-- tortoise/")
        assert _seen_inner and _seen_inner[0][0].strip() == "prints TWO paths", (
            f"the arm took a verb INSIDE the unit {_inner}: expected the outer `prints`, got "
            f"{_seen_inner!r} — a separator full-match cannot tell a unit boundary from a "
            "position inside a unit, because a unit's lookahead cannot see past the slice it is "
            "given (#13921 review, F4)")
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
    # claim parses only if `_BRACKET` is consumed whole. Dropping it reds this row and nothing
    # else (measured). (#13900 verification: this row used to carry prose — "in the note [a; b]
    # above" — which is the declared out-of-scope form now that the gap is units-only, so the
    # prose is gone and the property it was testing, the bracket unit, is what remains.)
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
    # …and the run stays LINEAR when no verb follows, which is what `7557a52cf5` measured as
    # exponential in the earlier ambiguous form. Bounded here so a regression reds instead of
    # hanging: 28 spaces is the size that took 15.8 s there.
    import time as _time
    _t0 = _time.monotonic()
    _typed_answers_to_tortoise_cmd("`-- tortoise/`" + " " * 28 + "no verb here", "-- tortoise/")
    assert _time.monotonic() - _t0 < 2.0, (
        "the separator's alternatives are ambiguous again — 28 spaces after the subject took "
        "longer than 2 s, and at `d38f9152af` 24 spaces alone took 15.8 s (measured)")
    # ⛔ AND THE SAME BOUND FOR AN AMBIGUOUS UNIT, WHICH THE SPACES ROW CANNOT SEE (#13900
    # review, verification round, P2, MEASURED). Spaces are unambiguous, so timing them proves
    # nothing about the alternations that ARE ambiguous: `\\.` and `[^\]\n]` both consume a
    # backslash-led pair, and the tag catch-all admits `'` that the quoted branch also consumes.
    # MEASURED growth of an unterminated `<a''''…` run (no verb, so the gap is fully explored):
    # n=20 0.004 s, n=24 0.024 s, n=26 0.068 s, n=28 0.194 s — ≈×1.7 per character.
    # ⚠️ DECLARED RESIDUAL, not a fixed hole: that cost is EXPONENTIAL in the run length and is
    # left as one, deliberately — making the alternations disjoint (excluding `\` and `'` from
    # the single-character classes) CHANGES which inputs parse and is therefore a behaviour change
    # this cycle did not measure. What is pinned is that it does not get WORSE, and what is
    # declared is that it exists: the live tree's slowest file is 16.7 ms and 855 files take
    # ~1.0 s, so the hazard is a future document with a long unterminated tag or label, not today's
    # tree. A `docs/` file that reds this row is a RESOURCE regression to be measured, not a red
    # to be silenced.
    _t0 = _time.monotonic()
    _typed_answers_to_tortoise_cmd("`-- tortoise/`<a" + "'" * 28, "-- tortoise/")
    assert _time.monotonic() - _t0 < 2.0, (
        "an ambiguous UNIT's alternatives exploded — an unterminated 28-character tag run took "
        "longer than 2 s; measured 0.194 s at this head, growing ≈×1.7 per character "
        "(#13900 review, verification round, P2)")
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
    m = re.search(
        r"re-pointing that row's `sdk\.py` cite from \*\*`:(\d+)`\*\*.*?"
        r"to \*\*`:(\d+)`\*\*",
        doc, re.S)
    assert m, (
        "05-verify.md no longer states the index row's re-point as "
        "`from **`:OLD`** ... to **`:NEW`**`. State it with both ends named, so this "
        "arm can check the pair against the file the re-point lands in "
        "(#13489 review, cycle 5)")
    old, new = m.groups()
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
_INDEX_REPOINT_CLAIMS = (
    "this diff carries ONE `docs/00_index.md` change — the capstone row's",
    "it IS in the set, for ONE cite",
    "for exactly ONE cite",
    "THIS repair re-points it once more, to",
    "this repair re-pointed that cite once more",
)


def test_the_bundles_index_cite_re_point_claim_is_corroborated_by_the_live_diff():
    """⛔ The doc said this diff's ONE `docs/00_index.md` line WAS a cite re-point.

    It is not, and the command printed beside the claim refutes it: the arm MEASURES
    `git diff --numstat origin/main...HEAD -- docs/00_index.md` and
    `git diff -U0 --word-diff=plain origin/main...HEAD -- docs/00_index.md` at the head it
    is run against instead of freezing a figure, and the WORD DIFF is what decides — it moves no
    `sdk.py:<n>` token in either direction (`grep -c 'sdk.py:21679' docs/00_index.md` ->
    `0` here AND on `origin/main`; `sdk.py:21715` -> `1` on both). ⛔ **no numstat figure is
    typed here (#13807 review, round 6)**: at the head the mistaken sentence was written
    against it printed `1 1`, and at this head it prints NOTHING — `main`'s bundle merge
    absorbed the two re-points this branch carried — which is this file's own thesis about
    stored figures, applied to the paragraph that states it. The line it rewrites
    is the `#12296` entity-verdict row's DESCRIPTION of its own `:27861` clause.

    So "the `1 1` numstat IS this cite re-point" was the same defect class the last four
    review rounds kept finding, and it reached the rail because nothing read this
    section (#13746 review, round 3, finding 4). The arm READS the live diff instead of
    freezing which direction the answer points, because the residual moves whenever
    `main` re-derives a row.

    NON-VACUITY, MEASURED: `_added_cite_tokens` is exercised on a CRAFTED patch that
    DOES move a cite, so an empty reading of the real diff is a fact about this diff and
    not an inert extractor; and on the pre-fix document the three surviving spellings
    are present with `moved` empty, so the loop reds on the first one.

    ⛔ AND IT SKIPS WHEN THE DIFF CANNOT BE TAKEN (#13795 review, finding 2): the ONE leg
    still shelling out to `git diff origin/main...HEAD` reports UNKNOWN on a failed read, so
    the depth-1 `test` job skips this arm instead of reding a true claim. (It said "both
    legs" until #13807 review round 4, finding 2: the `-- tortoise/` measurement it counted
    as the second is GONE — this file's only `subprocess.run` call site is the one below.)
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
        "its exit code, so the reason a skipped arm shows cannot distinguish a shallow "
        "clone from any other failed read (#13795 review, finding 2)")
    try:
        moved = _measured_index_word_diff()
    except _Unmeasurable as exc:
        pytest.skip(
            f"`git diff -U0 --word-diff=plain origin/main...HEAD -- docs/00_index.md` "
            f"is unmeasurable here ({exc}), so whether this diff moves a cite is "
            "UNKNOWN and this arm does not adjudicate against a failure")

    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    for claim in _INDEX_REPOINT_CLAIMS:
        if claim not in doc:
            continue
        window = doc[doc.index(claim):doc.index(claim) + 500]
        named = set(re.findall(r"`?:(\d+)`?", window))
        assert named & moved, (
            f"05-verify.md claims this diff re-points a `docs/00_index.md` cite "
            f"({claim!r}) and names {sorted(named)}, but the live three-dot diff adds "
            f"no such `sdk.py:<n>` token (added: {sorted(moved) or 'none'}). The "
            "claim is refuted by the command the document prints beside it: read the "
            "set from `git diff -U0 --word-diff=plain origin/main...HEAD -- "
            "docs/00_index.md` at the head being merged, and state the command rather "
            "than a head-bound pair (#13746 review, round 3, finding 4)")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
