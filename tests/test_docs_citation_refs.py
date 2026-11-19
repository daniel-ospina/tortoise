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
    `"returns exactly those two"` and marks it FALSE in every half, and ⛔ it DID carry a
    second one — `... and only refuses those spellings when the command actually prints a
    path`, the answer sitting mid-sentence — which is quoted here as the historical example
    it now is (round 6 review, P3 — MEASURED: `grep -c 'actually prints a path'` on
    05-verify.md is 0 at this head, because the sentence described a RETIRED guard and was
    removed). Reading either as a live membership claim would red the document for
    retracting a falsehood. So the class covered here is "the same answer,
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
    # review): a HARD-WRAPPED answer is not detected — the window is cut at the first
    # newline, so `prints only the\ntwo comment-only modules` and `returns\nnothing` are
    # skipped. Pre-existing, and the live document does not wrap; it is named here so the
    # closure claim above is read as "closed under formatting ON ONE LINE".
    # ⛔ AND A SECOND LIMIT, measured by TWO review cycles on the SAME sentence family
    # (#13845): a claim that names the command by PRONOUN — "the command this clause
    # prescribes returns NOTHING AT ALL" — carries no command token, so no shape rule can
    # reach it; and a command SHORTENED to a fragment is reachable only if the caller passes
    # that fragment as `cmd`. Both are handled by the callers (the two literal tripwires in
    # the claim list, and the second `_typed_answers_to_tortoise_cmd` call over
    # `` `-- tortoise/` ``), NOT by this function — so this docstring states the boundary
    # rather than implying the class is closed over every way a document can point at the
    # command. The residual is a sentence that refers to the measurement without naming it
    # at all; the live document states those as PAST TENSE with their head named, which is
    # a convention kept by reading, not by an assertion (the label exemption that tried to
    # enforce it mechanically was measured fail-open and false-blocking, and is deleted).
    for match in re.finditer(
            r"`?" + re.escape(cmd)
            + r"[`_\s*]*(?:(?:still|now|actually|also)\s+)?"
              r"(prints|returns|yields|reports|gives|outputs|carries|lists|shows|"
              r"names|includes|contains|has|->|→|is|=|:)", doc, flags=re.IGNORECASE):
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
        # So an occurrence that is the PATHSPEC of a longer invocation is skipped: the
        # writer named the revs, which is exactly what this document's own rule asks for,
        # and this arm cannot fetch that head. A STANDALONE short-form claim is still graded
        # (the witness below pins that half, so the skip cannot widen into silence), and a
        # full `… origin/main...HEAD -- tortoise/` claim is still graded — by the caller's
        # `cmd` pass, which is the spelling that names THIS head.
        # RESIDUAL, stated: a standalone short-form claim about ANOTHER head is graded
        # against this head, because nothing here reads which head a bare `-- tortoise/` is
        # about. Such a claim has to carry its head in the full command form, which this rule
        # then skips — which is why the live document spells its own history that way.
        # The executed test is stated with the code below (a `git diff` with NO backtick and
        # no sentence break between it and this occurrence); it was tightened twice after
        # being measured fail-open, and each tightening's measurement is recorded there.
        # ⛔ AND THE INVOCATION MUST NOT CROSS A SENTENCE BREAK (#13845 review, cycle 5,
        # MEASURED): the first cut of this skip was `git\s+diff\b[^`\n]*$`, which matched
        # ANY unbackticked `git diff` earlier on the same LINE, so
        # `Some history here. We ran git diff earlier. `-- tortoise/` prints ONE path at this
        # head.` returned `[]` and the arm went GREEN while the same claim with the prose
        # changed returned an answer and RED — one dropped backtick defeated it.
        # (Requiring the text to END with the `-- ` separator does not work: the separator is
        # the START of this match, so it is never in `_before` — measured, the rule matched
        # nothing and the true head-named history was refused again.)
        # What separates a pathspec of a live invocation from a claim about one is that the
        # invocation runs on: no sentence boundary (a `.` followed by whitespace) between
        # `git diff` and the occurrence. `origin/main...2a7f24e241` has dots, but none
        # followed by a space, which is exactly the discrimination wanted.
        # ⛔ AND A BACKTICK BETWEEN THEM ENDS IT (#13845 review, cycle 6, MEASURED — this
        # is the rule the first cut had and the sentence-break test replaced; BOTH are
        # needed, and the measurements say why). The sentence-only test still skipped a
        # claim that merely SHARED a sentence with another invocation:
        #
        #   "See `git diff origin/main...HEAD -- docs/` for the docs; `-- tortoise/` prints
        #    the two COMMENT-ONLY modules at this head."            -> [] (skipped)  WRONG
        #   "…`-- tortoise/` prints ONE path at this head" on the LIVE document (:134,
        #    93 chars after a `-- docs/00_index.md` command, no `. ` between)  -> GREEN WRONG
        #
        # In both, a backtick sits between `git diff` and the occurrence: the first command's
        # code span CLOSED, so this occurrence starts its own span and its own claim. Ten of
        # the document's twenty-one `-- tortoise/` occurrences are that class. The true
        # pathspec case (`(`git diff … -- tortoise/` names exactly …)`) has NO backtick
        # between, because both sides are inside ONE span.
        _before = doc[max(0, match.start() - 200):match.start()]
        _invocation = re.search(r"git\s+diff\b", _before)
        if _invocation:
            _gap = _before[_invocation.start():]
            if "`" not in _gap and not re.search(r"\.\s", _gap):
                continue
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
    return answers


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
      (c) TYPED ANSWERS — a typed answer to the `-- tortoise/` command, refused by TWO
          layers, and the difference is which checkout each holds in:

          (c1) INLINE, base-free, ALWAYS LIVE: any present-tense typed answer to that command
               — emptiness OR membership, through BOTH detectors below — is refused
               unconditionally, because such an answer is head-bound by construction. This
               is the layer that runs in the depth-1 `test` job.

          (c2) MEASURED, in `test_the_bundles_tortoise_set_answers_are_not_typed_against_the
               _tree`: it reads the set from `git diff origin/main...HEAD` and refuses only
               the direction the measurement REFUTES, so a TRUE answer is allowed there. Its
               direction comes from a subprocess a depth-1 checkout cannot answer (rc=128,
               empty stdout), so it SKIPS there — which is exactly why (c1) exists. ⛔ No
               count is typed here: it moves whenever this file gains a test, which is this
               document's own subject.
          The spellings measured FALSE are refused by name AND by SHAPE (the command
          followed by one of the LISTED answer forms, then an answer), so neither a frozen
          sign nor a re-wording of the ANSWER — a different listed verb, or a different
          membership word (`one` as readily as `two`) — can quietly restore a claim the
          tree refutes (#13728 review, findings 1 and 4; #13795 review, finding 3). The
          class stays FINITE and the docstring above says so: a verb outside the list, or
          an answer carrying none of the listed words, is not detected. ⛔ The MEASURED half
          lives in its OWN test —
          `test_the_bundles_tortoise_set_answers_are_not_typed_against_the_tree` — because
          its direction comes from a subprocess this file cannot run in every checkout, and
          an UNMEASURABLE set now SKIPS that test instead of being read as "empty"
          (#13795 review, findings 1 and 2).
    """
    from tools.hosted_api_cite_census import _note_paths, _sweep, census

    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")

    # (a) AGREEMENT, measured in-process by the checker the document quotes.
    verdict = _sweep(census(), refresh=False)
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
    _tortoise_cmd = "git diff --name-only origin/main...HEAD -- tortoise/"
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
    # `outputs`/`→` all match it here. Its ANSWER class is a strict SUBSET of the
    # detector's — measured, `returns none` is read by the detector and not by this regex,
    # which is exactly what the `config/ci-surfaces.yml` row for this file states — so the
    # detector is still asked separately for its direction LOGIC and its wider answer list
    # (#13802 review, P2: this comment claimed EQUALITY of both classes, which the config
    # row in the same diff contradicted).
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
    # ⛔ AND A LEDGER OF THE SPELLINGS THAT ACTUALLY SHIPPED AND SURVIVED A MERGE — carried
    # over from `origin/main`'s `#13807` arm at the merge of this branch (#13802 merge).
    # The shape matcher above anchors on the command or on `the `tortoise/` set`, so a
    # verdict whose SUBJECT is a deictic that names neither — the document's own
    # "the three table rows … are NOT in the diff at this head (measured above: that command
    # returns nothing for all three)" — reaches NO verb form of it, and MEASURED at the
    # pre-merge heads it was GREEN through both of this branch's arms. `main` retires that
    # class BY NAME because a name list is the only arm that can see a subject the shape
    # cannot anchor; it is a SUBSTRING ban, so it also fires on a QUOTED RETRACTION — the
    # over-block `main`'s docstring accepts and documents. The two arms are kept BESIDE each
    # other (as they are on `main`) because neither is a superset: the shape half reads a
    # RE-WORDING of a known form, and this half reads an OLD form under a subject no shape
    # rule reaches.
    _surviving = _census_membership_verdicts(doc)
    assert not _surviving, (
        f"05-verify.md spells a RETIRED `-- tortoise/` membership verdict: {_surviving!r}. "
        f"`{_tortoise_cmd}` is HEAD-BOUND — the set is a property of the head it was run "
        "at — so the document states the COMMAND and lets the reader read it at the head "
        "being merged. These are the exact spellings that were FALSE at a head and "
        "survived anyway (#13746 review, finding 7; #13807 review, F1), so the fix for a "
        "hit is to delete the typed membership and keep the command")
    # (c1) above is the always-live refusal and it lives HERE, base-free. The MEASURED
    # classifier (c2) still lives in
    # `test_the_bundles_tortoise_set_answers_are_not_typed_against_the_tree`, deliberately
    # NOT here: its direction is read from `git diff origin/main...HEAD`, which a depth-1
    # `test` job cannot answer at all (rc=128, empty stdout) — and a `pytest.skip` raised
    # from HERE would also silence (a) AGREEMENT and (b) POINTERS in every job that cannot
    # measure, i.e. in the only job that gates the merge. That skip is why (c1) has to
    # carry the coverage on the rail (#13795 review, finding 1; #13802 merge round P2).

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
    """A `git diff origin/main...HEAD` reading could not be TAKEN here, so its value is UNKNOWN.

    ⛔ IT HAS TWO SUBJECTS, AND NAMING ONE IS HOW THIS DOCSTRING WENT WRONG (#13802 merge
    round P2): `_measured_tortoise_set` (the `-- tortoise/` set) and
    `_measured_index_word_diff` (the `sdk.py:<n>` tokens this diff adds to
    `docs/00_index.md`) both raise it. The rule they share is the only thing that matters
    here: a FAILED read is UNKNOWN and must never be rendered as the empty value a
    successful read of nothing produces.
    """


def _measured_tortoise_set(proc: subprocess.CompletedProcess | None = None) -> list[str]:
    """The `-- tortoise/` set at this head, or `_Unmeasurable` — never "empty" by default.

    ⛔ A FAILED `git diff` IS *UNKNOWN*, NOT "prints NOTHING" (#13795 review, finding 1).
    The cut before this one read `subprocess.run(...).stdout` ONLY, so a clone that cannot
    answer the question — depth 1 has no `origin/main`, so git exits 128 with empty stdout
    — arrived at the caller as `[]`, the same value a SUCCESSFUL read that found nothing
    produces. The arm then rendered that as the measurement `"NOTHING"` and adjudicated the
    document against it: a false "prints NOTHING" was permitted and a true "prints
    `tortoise/x.py`" was refused, in the one job that gates the merge
    (`.github/workflows/python-ci.yml`'s `test` job is a bare `actions/checkout@v4`, and
    `.github/workflows/ci.yml` states the consequence of its `fetch-depth: 1` grafted
    graph: "the object is usually absent (exit 128)"). `proc` is injectable so the
    distinction is testable without a shallow clone.
    """
    if proc is None:
        proc = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD", "--", "tortoise/"],
            cwd=REPO, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise _Unmeasurable(
            f"exit {proc.returncode}: {detail[0] if detail else '<no output>'}")
    return proc.stdout.split()


def test_an_unmeasurable_tortoise_set_is_not_rendered_as_empty():
    """⛔ A measurement and a FAILED measurement must not share a value.

    Pre-fix there was no second value to inspect: the subprocess's stdout WAS the set, so
    `rc=128, stdout=""` and `rc=0, stdout=""` were the same input, and the only reading of
    it was "prints NOTHING". This is the `(c)` arm's own precondition, asserted where the
    measurement lives rather than inferred from the arm's silence (#13795 review,
    finding 1).
    """
    ok = subprocess.CompletedProcess(
        args=["git"], returncode=0, stdout="tortoise/a.py\ntortoise/b.py\n", stderr="")
    assert _measured_tortoise_set(ok) == ["tortoise/a.py", "tortoise/b.py"]
    empty = subprocess.CompletedProcess(args=["git"], returncode=0, stdout="", stderr="")
    assert _measured_tortoise_set(empty) == []
    failed = subprocess.CompletedProcess(
        args=["git"], returncode=128, stdout="",
        stderr="fatal: bad revision 'origin/main...HEAD'\n")
    with pytest.raises(_Unmeasurable) as _raised:
        _measured_tortoise_set(failed)
    assert "128" in str(_raised.value), (
        "the unmeasurable error does not name the exit code, so the reason a reader sees "
        "cannot tell `origin/main` is absent from a failed read of any other kind")


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
    """⛔ Refuse a typed answer to the `-- tortoise/` command in the REFUTED direction.

    This is the `(c)` arm of `test_the_bundle_does_not_report_a_census_verdict_it_did_not
    _measure`, split out because its DIRECTION comes from a subprocess and a `pytest.skip`
    in the sibling would silence the AGREEMENT and POINTERS arms too (#13795 review,
    finding 1). The set is measured; when it CANNOT be measured the direction is UNKNOWN
    and this test SKIPS with the reason, rather than adjudicating the document against a
    failure that happened to look like an empty set.

    ⛔ THE NON-VACUITY WITNESSES RUN FIRST, BEFORE THE MEASUREMENT, on purpose: they are
    direction-free, so they still prove the detector works in a checkout where the diff
    cannot be taken. Four paths need a witness, and the MEMBERSHIP branch needs its own —
    it has one now (#13795 review, finding 1: deleting or inverting it used to red NOTHING,
    because every document match took the emptiness path).
    """
    doc = (DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone"
           / "05-verify.md").read_text(encoding="utf-8")
    _tortoise_cmd = "git diff --name-only origin/main...HEAD -- tortoise/"

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

    # (c) THE MEASUREMENT — taken, or UNKNOWN. Never a silent "empty".
    try:
        _tortoise_set = _measured_tortoise_set()
    except _Unmeasurable as _unmeasured:
        pytest.skip(
            "the `-- tortoise/` set cannot be MEASURED in this checkout ("
            f"`git diff origin/main...HEAD` {_unmeasured}). The `test` job checks out at "
            "`fetch-depth: 1`, whose grafted graph does not carry `origin/main` or the merge "
            "base, so the direction of the answer is UNKNOWN here — NOT empty. Skipping "
            "rather than rendering a failed read as the measurement "
            "(#13795 review, finding 1)")
    _measured = " ".join(_tortoise_set) if _tortoise_set else "NOTHING"

    # ⛔ THE HEAD-LABEL EXEMPTION IS GONE, AND THAT IS THE FIX (#13845 review, cycle 4,
    # MEASURED BOTH WAYS). It existed so a TRUE claim that names its head would not be
    # refused by THIS head's measurement — and it failed on both edges:
    #
    #   FAIL-OPEN  `[0-9a-f]{7,40}` matches a plain NUMBER and hex-shaped WORDS, so
    #              `The repair landed 1234567 commits ago and the `tortoise/` set above is
    #              EMPTY.` was GREEN — any nearby number exempted a refuted claim. The word
    #              `defaced` did the same.
    #   FALSE BLOCK  the positional rule took `re.search`'s EARLIEST match, which was the
    #              preceding clause's `NOT at this head:`, and refused a claim the document
    #              had genuinely labelled.
    #
    # Refining it a third time would be the same bet twice more. The convention that makes it
    # unnecessary is the one the live document already follows: history is spelled so the arm
    # cannot read it as a live answer — a PAST-TENSE verb (`returned`, `named`, `printed`),
    # which is not in the verb list, or a restructure that puts the named head first and the
    # command after it (46f3e9ff5b, and the pathspec skip in the short-form arm). The COST is
    # declared rather than hidden: a head-labelled PRESENT-tense claim is still refused in
    # BOTH directions — an emptiness claim on a non-empty head AND a membership claim on
    # an empty one (`at `2a7f24e241`, `-- tortoise/` names exactly those two modules` is
    # true at that head and refused here, measured). The live document carries none: its
    # history is past tense, or a kept pathspec of a longer invocation.
    for _claim, _claim_is_empty in (
            *((_c, True) for _c in (
                f"neither is `{_tortoise_cmd}`",
                f"{_tortoise_cmd}` -> empty",
                f"{_tortoise_cmd}` prints NOTHING",
                # ⛔ AND THE PARAPHRASE THAT NAMES NO VERB (#13845 review, P2) — measured:
                # the three command-shaped spellings above were all the arm read, and the
                # live document stated the same refuted direction as "the `tortoise/` set
                # above is EMPTY", which no entry matches: the shape detector needs an
                # answer form immediately after the command, so a sentence that refers to
                # the set by NAME rather than re-running the command is invisible to it.
                # Mutation-pinned: restoring that spelling in a doc whose set is non-empty
                # reds HERE and nowhere else.
                "the `tortoise/` set above is EMPTY",
                # …and the two the SECOND review cycle measured, both abbreviations of
                # the same command: one names it by pronoun ("the command this clause
                # prescribes"), the other shortens it to `` `-- tortoise/` ``. Pinned as
                # literals because no shape rule can reach a pronoun — see the declared
                # limit in the module docstring.
                "returns NOTHING AT ALL (the diff carries no `tortoise/` path)",
                "`-- tortoise/` returns NOTHING",
            )),
            *((_c, False) for _c in (
                f"{_tortoise_cmd}` is NOT empty",
                f"{_tortoise_cmd}` returns exactly those two",
                f"{_tortoise_cmd}` prints the two COMMENT-ONLY modules",
                f"{_tortoise_cmd}` prints exactly `tortoise/activation_scorecard.py`",
                "the `tortoise/` set above is the two COMMENT-ONLY modules",
                "which two `tortoise/` paths this diff carries",
            )),
    ):
        # contradiction iff the claim's direction EQUALS the measurement's (a member claim
        # when the set is empty, or an emptiness claim when it is not) — so `_consistent` is
        # the case where they differ, written out rather than inlined, because the first cut
        # of this guard negated the whole disjunction and let an UNLABELLED emptiness claim
        # through (measured: the mutation below stayed green, which is a bypass, not a fix).
        # ⛔ AND THERE IS NO HEAD-LABEL EXEMPTION (see the convention stated above): a
        # membership/emptiness claim in the refuted direction is refused whether or not a
        # head is named, because the exemption that was supposed to tell "history about
        # another head" from "a live claim" was measured fail-open (a plain number, or the
        # word `defaced`, counted as a label) AND false-blocking. The document states history
        # so this arm cannot read it as a live answer instead — past-tense verbs, or the
        # named head first and the command after it.
        _consistent = _claim_is_empty != bool(_tortoise_set)
        assert _consistent or _claim not in doc, (
            f"05-verify.md asserts {_claim!r} — an answer to `{_tortoise_cmd}` in the "
            f"direction the tree refutes at this head, where that command prints "
            f"{_measured}. A guard that pins a MEASUREMENT must not pin its SIGN either "
            "(#13489 review, P1; #13750 review, cycle 1; #13728 review, P1)")
    # ⛔ AND THE ABBREVIATED SPELLING (#13845 review, cycle 2): the document shortens the
    # command to `` `-- tortoise/` `` in several places, so an answer typed against the
    # SHORT form was invisible to the arm above — the same half-closure one layer down.
    # Both spellings are read; a claim naming the command by pronoun is not, and that
    # residual is declared in the module docstring rather than left implied.
    # ⛔ DECLARED RESIDUAL, #13818 (recorded there with the measurement): this arm knows ONE
    # pathspec — `-- tortoise/` and its abbreviation. The D5 row's stated authority is the
    # PER-ROW form (`-- <that row's own path>`), and nothing reads it, so a claim of that
    # shape passes green whatever the tree says: measured on this PR's review cycle 3, two
    # such claims (`05-verify.md:102` naming no pathspec, `:134` naming
    # `-- tests/test_7949_capstone_measure.py`) were false at that head and invisible here.
    # The repair is a general arm deriving the pathspec from the claim, not N more literals.
    # ⛔ PINNED IN BOTH DIRECTIONS (#13845 review, cycle 3, P1), because the fix that stops
    # the arm over-blocking is exactly the kind that turns into a silent hole:
    #   * the pathspec of a LONGER invocation is SKIPPED — this is the discrimination the
    #     pre-fix arm lacked, and both spellings below returned a claim there (measured on
    #     the pre-fix code: two answers, both collateral, neither about this head);
    #   * a STANDALONE short form is still GRADED — the control, without which a skip that
    #     widened to every occurrence would look green while grading nothing.
    assert _typed_answers_to_tortoise_cmd(
        "(`git diff --name-only origin/main...2a7f24e241 -- tortoise/` names exactly "
        "`tortoise/activation_scorecard.py`)", "-- tortoise/") == [], (
        "the short-form arm grades the pathspec of a command pinned to ANOTHER rev against "
        "THIS head's measurement, so true, head-named history is refused on any empty head "
        "(#13845 review, cycle 3, P1)")
    assert _typed_answers_to_tortoise_cmd(
        "(`git diff --name-only e61694255a...2584a86ec1 -- tortoise/` prints FIVE paths "
        "there)", "-- tortoise/") == [], (
        "the same skip must cover a SECOND rev range — the shape is 'the pathspec of a "
        "longer invocation', never that one particular range (#13845 review, cycle 3, P1)")
    assert _typed_answers_to_tortoise_cmd(
        "and `-- tortoise/` prints NOTHING at this head", "-- tortoise/") == [
            ("prints NOTHING at this head", True)], (
        "the skip widened to a STANDALONE short-form claim — the control this witness exists "
        "for: a rule that silences the live question hides every refuted claim "
        "(#13845 review, cycle 3, P1)")
    # ⛔ AND THE LIVE DOCUMENT'S OWN PINNED COMMANDS ARE SKIPPED — read off the document, so
    # the witness tracks its spelling instead of quoting one sentence and rotting. This is the
    # measurement that made the P1 a red build rather than a theory: before the boundary,
    # `_typed_answers_to_tortoise_cmd(doc, "-- tortoise/")` returned FOUR answers on this
    # document, two of them true history about other revs refused on any empty-`tortoise/`
    # head (`returns ONE module`, `names exactly …`) and one its own head had already
    # falsified (`returns ONE path, tortoise/record_vector.py`).
    _live_invocations = re.findall(r"\(`git diff [^`]*-- tortoise/`", doc)
    assert _live_invocations, (
        "the live document no longer spells a `git diff … -- tortoise/` invocation, so this "
        "witness reads nothing — restore the shape or retire the witness, do not leave it "
        "vacuous (#13845 review, cycle 3, P1)")
    for _invocation in _live_invocations:
        assert _typed_answers_to_tortoise_cmd(
            _invocation + " names exactly `tortoise/activation_scorecard.py`",
            "-- tortoise/") == [], (
            f"the short-form arm grades {_invocation!r} — a command pinned to something other "
            "than this head — against THIS head's measurement, so the document's correctly "
            "head-pinned history is refused on any head where `-- tortoise/` is empty and "
            "passes only by luck elsewhere (#13845 review, cycle 3, P1)")
    for _answer, _claims_empty in (
            *_typed_answers_to_tortoise_cmd(doc, _tortoise_cmd),
            *_typed_answers_to_tortoise_cmd(doc, "-- tortoise/")):
        assert _claims_empty != bool(_tortoise_set), (
            f"05-verify.md types {_answer!r} as the answer to `{_tortoise_cmd}`, which "
            f"prints {_measured} at this head — a typed present-tense ANSWER to a head-bound "
            "measurement, in the direction the measurement refutes (#13728 review, "
            "finding 4)")


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


#: ⛔ A DIRECTION CLAIM ABOUT THE `-- tortoise/` SET IS THE SAME HEAD-BOUND FIGURE
#: (#13802 review, findings 2 and 3 — P2, and the reason the arm is a TEXT ban). The
#: earlier cut of that leg carried FIVE literal spellings of the claim and refused only
#: where the diff measured non-empty, which made it inert on the depth-1 rail and a
#: false red elsewhere; a five-string blacklist is also one synonym away from the defect
#: — the document itself already carried FOUR spellings the list never saw
#: (`prints NO path at all`, `prints NO path at this head`, `prints no path at all`,
#: `the \`tortoise/\` set above is EMPTY`), measured by the reviewer as
#: `1 passed, 13 deselected` on an injected claim. So the class is a PATTERN, not a
#: list: the command (or `tortoise/` set) followed by a present-tense direction verb and
#: an emptiness word. The HISTORICAL form ("printed no path at the head this was written
#: for") is past tense and deliberately allowed — that is the same line the numstat arm
#: draws between `printing `1 1` at a head of its own` and `both print `1 1``.
#: The command whose output the `#7949` bundle's census paragraph quotes. Named here (never
#: interpolated from a measurement) because every arm below is a TEXT ban.
_TORTOISE_CENSUS_CMD = "git diff --name-only origin/main...HEAD -- tortoise/"


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



_TORTOISE_NAMED_HEAD = re.compile(r"(?:`[0-9a-f]{7,40}`|\b[0-9a-f]{7,40}\b|#\d{3,6})")


class _HeadBoundBan:
    """The direction ban, minus HISTORICAL mentions.

    A sentence that NAMES the head it is about is not a head-bound claim about the head you
    merge: the document is allowed to say "`#10215`'s `8bc2fbed1` touches no `tortoise/`
    file", and the witness list pins the past-tense form as MUST-NOT-REFUSE. A regex
    lookbehind cannot express that (variable width), so the allowance is a post-filter on
    the match's preceding window (#13802 review, P2 — measured: three named-head emptiness
    sentences were refused by the unanchored verb-before-path alternative, one of them
    already shipped in `docs/evidence/7949-capstone/README.md`).
    """

    __slots__ = ("_rx",)

    def __init__(self, rx):
        self._rx = rx

    def _claimed(self, m, text):
        return not _TORTOISE_NAMED_HEAD.search(text[max(0, m.start() - 80):m.start()])

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
    # ANCHOR — the command (long spelling or the `-- tortoise/` shorthand), the
    # `tortoise/` SET, or the DEICTIC reference that names no command at all.
    r"(?:(?:git diff --name-only origin/main\.\.\.HEAD -- tortoise/`"
    r"|`-- tortoise/`"
    r"|`?tortoise/`?\s+(?:set|side|part)[^`]{0,40}?"
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
    r"(?:\s+there)?(?:\s*,?\s*[\w-]+,?){0,6}\s*"
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
    r"is|[=:])\s*`?"
    # ⛔ AND A PREPOSITION BETWEEN THE VERB AND THE ANSWER (`comes back WITH an empty
    # list`, `resolves to ZERO`), measured GREEN before this line (#13802 review, round 7)
    r"(?:(?:with|to|as|into|out|of)\s+)?"
    r"(?:(?:NOT|not)\s+)?"
    # AND THE ANSWER FORMS that detector knows, in the same widening: `one path` as
    # readily as `exactly two`, `both modules` as readily as `TWO modules`, and a
    # MODIFIED membership count ("the two COMMENT-ONLY modules") as readily as a bare one.
    r"(?:NOTHING|NO paths?|no (?:`?tortoise/[^`\s]*`?\s*)?(?:paths?|files?|entries|diffs?|hits?)"
    r"|empty|EMPTY|non-empty|nonempty|not empty"
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
    r"|`?\[\]`?|none|None|zero|ZERO|nothing|Nothing"
    # ⛔ AND THE `an empty list` / `single` / BARE-DIGIT tails (#13802 review, round 7, P1 —
    # each measured GREEN here while the detector read it; for the DEICTIC and `set` anchors
    # this ban is the ONLY layer, because the detector anchors on a command token)
    r"|(?:an?\s+)?(?:empty|blank|EMPTY)\s+(?:list|set|array|result|output)"
    r"|single|SINGLE|\b\d+\b"
    r"|(?:exactly\s+)?(?:one|two|three|four|five|six|seven|eight|nine|ten|a|an|the)\s+"
    r"(?:[\w-]+\s+)?(?:files?|paths?|modules?|edits?)"
    r"|(?:[a-z''-]+\s+){0,4}comment[^.]{0,40}?(?:only|text|edits?)"
    # ⛔ AND THE VERB-BEFORE-PATH PARAPHRASES that carry the answer as a NOUN PHRASE
    # (`paths only as comment/docstring text`) — no verb after the anchor at all.
    r"|paths?\s+only\s+as[^.]{0,60}"
    # ⛔ AND THE NO-DIFFERENCE NOUN-COMPLEMENT (#13802 review, round 10, P2): the `set`
    # ANCHOR reaches a verb, but `the `tortoise/` set is untouched` needs these words in the
    # ANSWER class too — measured GREEN with only the bare-`tortoise/` alternative added.
    r"|untouched|unchanged|unmodified|byte-identical|identical|the\s+same|no\s+code"
    r"|`?tortoise/[A-Za-z0-9_./-]+\.py`?)"
    r"|neither is `git diff --name-only origin/main\.\.\.HEAD -- tortoise/`)"
    # ⛔ AND THE "which N paths" SHAPE (#13802 review, P1 — MEASURED): the measured leg's own
    # claim list carries `which two `tortoise/` paths this diff carries`, and this ban could
    # not see it — an anchor-less membership question in the document's own idiom.
    r"|which (?:one|two|three|four|five)\s+`?tortoise/[^`]*`?\s+paths?"
    # C) ⛔ AND THE VERB-BEFORE-THE-PATH FAMILY (#13802 review, P1 — 15 freshly-invented
    # spellings measured GREEN): `this diff carries no `tortoise/` path at all`, `this diff
    # touches no `tortoise/` file`, `the `tortoise/` set holds no path here`. Shape A cannot
    # see any of these — it anchors on the COMMAND or on `the `tortoise/` set` and then
    # needs a verb, while here the subject is the DIFF and the path comes AFTER the verb.
    # This alternative matches only a LIMITING qualifier (`no`/`zero`/`only`) between the
    # verb and the path, so an ordinary sentence ("this diff carries `tortoise/x.py`
    # because …") is untouched.
    r"|\b(?:carries|touches|has|holds|contains|includes|names|lists|reads|changes"
    r"|is|are|gets|returns|yields|prints|adds|removes|edits)\s+"
    r"(?:no|zero|only)\s+`?tortoise/[^`]*`?"
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
    r"(?:untouched|unchanged|unmodified|identical|byte-identical|the\s+same|no\s+code)"))


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
    this branch's range produces that census (re-measured with the CURRENT pattern at
    `origin/main`: **SIX matches across FIVE lines**, three of them the `--numstat` form
    and three the ``docs/00_index.md`` + pair form). Re-run the pattern at the head you
    are reviewing and read the map from that, rather than trusting a figure written here
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

    ⛔ SAME RULE AS `_measured_tortoise_set`, ON PURPOSE (#13795 review, finding 2): a
    FAILED `git diff` is UNKNOWN, never "moved nothing". The cut before this one wrote
    `moved = _added_cite_tokens(proc.stdout) if proc.returncode == 0 else set()`, so a
    clone that cannot answer the question — depth 1 has no `origin/main`, so git exits
    128 with empty stdout — produced the SAME value a successful read of a diff that
    moves no cite produces. The arm below then reds on a claim that is TRUE there, in
    `test`, the one job that gates the merge. Both legs that shell out to
    `git diff origin/main...HEAD` now share this rule; `_measured_tortoise_set` is the
    other, and a change that fixes one of them without the other re-opens this class.
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
#: ⛔ AND THAT IS NARROWER THAN WHAT IT REPLACES — stated, not papered over (#13802 merge
#: round P2). The deleted arm also asserted the OLD cite was ABSENT; nothing enforces that
#: now, measured: injecting `sdk.py:21604` into `docs/00_index.md` ALONGSIDE the surviving
#: `sdk.py:21715` leaves the suite green. It is not re-added here because "is it absent" is
#: a property of `docs/00_index.md`'s own content rather than a claim about this diff, and
#: because `main` re-deriving that row again is the normal case this PR exists to stop
#: reding on. Recorded here rather
#: than in a comment beside a deleted function, because a reader must be able to see WHY
#: `main`'s arm is gone without diffing two heads.
_ATTRIBUTION_PHRASES = (
    "re-pointing that row's `sdk.py` cite from",
    "and THAT shift is this repair",
    "re-points it once more, to",
    "re-pointed it once more",
    "re-pointed that cite once more",
    "for exactly ONE cite",
)


#: The spellings that assert THIS diff re-points a cite in `docs/00_index.md`. Each is
#: measured FALSE at the head this arm runs on (#13746 review, round 3, finding 4):
#: `git diff --name-only origin/main...HEAD -- docs/00_index.md` prints NOTHING (and the
#: two blobs at `origin/main` and `HEAD` are the same), so the file is not in the
#: residual at all and no `sdk.py:<n>` token moves. An earlier cut attributed the
#: residual to "the `#12296` row's DESCRIPTION of its own `:27861` clause" — that edit
#: is `origin/main`'s now (#13802 review, finding 1).
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
