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
      (c) RETIRED SPELLINGS — the phrasings that were FALSE against this tree are
          refused by name, so a hand edit cannot quietly restore one. If a future head
          really does empty that path list, re-scope (c) deliberately instead of letting
          the old sentence back in.
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

    # (c) RETIRED SPELLINGS — measured FALSE against the tree, so the arm READS the tree
    # instead of freezing which direction the answer points.
    # ⛔ MEASURED (#13746 review, finding 7): this list was five literals assuming a NON-EMPTY
    # `-- tortoise/` set. `main`'s own `#13790`/`#13786` then re-took the same census figures,
    # so at this head the command prints NOTHING and every one of those five spellings became
    # the TRUE statement — the same class of error as the frozen sign this arm replaced
    # (a guard that pins a MEASUREMENT has to READ it). The prohibition is therefore applied
    # only where the measurement supports it.
    _tortoise_set = subprocess.run(
        ["git", "diff", "--name-only", "origin/main...HEAD", "--", "tortoise/"],
        cwd=REPO, capture_output=True, text=True, check=False).stdout.split()
    for _negation in (
        f"neither is `{_tortoise_cmd}`",
        f"{_tortoise_cmd}` is NOT empty",
        f"{_tortoise_cmd}` returns exactly those two",
        f"{_tortoise_cmd}` -> empty",
        f"{_tortoise_cmd}` prints NOTHING",
    ):
        if not _tortoise_set:
            # The empty claim is the measured one here; asserting it is not a defect.
            continue
        assert _negation not in doc, (
            f"05-verify.md asserts {_negation!r}. `{_tortoise_cmd}` prints "
            f"{' '.join(_tortoise_set)!r}, so the sentence is refuted by the very command "
            "it quotes (#13489 review, P1; #13750 review, cycle 1 — the empty claim was "
            "true of an earlier head and is not true of this one)")

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
        -> identical, so `#13724`'s move is already on the branch's base

    ⛔ At the head this arm now runs on, the file is not in the residual AT ALL —
    `git diff --name-only origin/main...HEAD -- docs/00_index.md` prints nothing and
    `git rev-parse origin/main:docs/00_index.md HEAD:docs/00_index.md` prints the same
    blob twice — so there is no "one changed line", and the `#12296` row's DESCRIPTION
    of its own `:27861` clause is INHERITED from `origin/main`, not this diff's. The
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
    # #13617/#13675, #13724, #13750/#13786, #13790) — i.e. exactly the heads where the
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
    _sdk = (REPO / "tortoise" / "sdk.py").read_text(encoding="utf-8").splitlines()
    for _cite, _needle in (
            ("sdk.py:21715", "_capture_stats_with_step_witness("),
            ("sdk.py:28178", "dedup_reviewed IS NULL")):
        assert _cite in index, (
            f"05-verify.md presents `{_cite}` as the row's current value; "
            "`docs/00_index.md` carries no such cite (#13746 review, round 3, P2)")
        _n = int(_cite.rsplit(":", 1)[-1])
        assert _needle in _sdk[_n - 1], (
            f"05-verify.md presents `{_cite}` as the row's current value, but "
            f"`tortoise/sdk.py:{_n}` is {_sdk[_n - 1].strip()!r} — the cite does not "
            "name the content the sentence says it names "
            "(#13746 review, round 3, P2)")

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
    # (`python-ci.yml:968` and `post-merge-validation.yml:96`/`:222` carry no `with:`),
    # so `origin/main` never resolves on the rail and a skip here means this arm
    # asserts NOTHING in the only place the rail reads it. MEASURED on a real depth-1
    # clone of this branch with the document's attribution flipped back to "and THAT
    # shift is this repair's, not `main`'s": `11 passed, 2 skipped` — green, defect
    # swallowed. The fallback is the UNCONDITIONAL direction instead, which is what
    # this file's own `config/ci-surfaces.yml` row already prescribes for the sibling
    # `-- tortoise/` leg: with the base unreadable, NOTHING shows this diff moves a
    # cite, so no attribution may be asserted. It keeps the file hermetic (its row
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


#: A `--numstat` figure is a head-bound number: `git diff --numstat origin/main...HEAD --
#: docs/00_index.md` prints NOTHING at this head, so a document that types what it printed
#: is refuted by the command it quotes beside the figure. The pre-fix document did exactly
#: that at four clauses (#13802 review, finding 1 — the D5 row and the file-table section),
#: so the pattern below is the armed detector for that defect class.
_TYPED_INDEX_NUMSTAT = re.compile(
    r"`git diff --numstat origin/main\.\.\.HEAD -- docs/00_index\.md`"
    r"\s*(?:->|prints)\s*`\d+\s+\d+`"
    # ⛔ THE SHORT FORM TOO (#13802 review, P2). The document's most-used spelling of
    # this clause is "…and its `--numstat` form both print NOTHING", typed FIVE times,
    # and a regex that knows only the long form walks straight past the figure that
    # replaces `NOTHING` there — measured on a depth-1 clone: `both print `1 1`` left
    # both arms green. The same command's short name is the same head-bound figure.
    r"|`--numstat`\s+form[^`]{0,60}?(?:->|prints?|both\s+print)\s*`\d+\s+\d+`")


def test_the_document_types_no_numstat_figure_for_the_index_residual():
    """⛔ The D5 row and the file-table rows typed `1 1` — refuted by their own command.

    `git diff --numstat origin/main...HEAD -- docs/00_index.md` prints NOTHING at this
    head, so a document that states what it `prints` is contradicted by the command it
    quotes in the same clause. That is precisely the defect class this file exists to
    catch — a typed head-bound figure — applied to the figure's own command, and it
    survived the earlier rounds because each guard spelled its own phrase list instead
    of recognizing the shape (#13802 review, finding 1).

    NON-VACUITY, MEASURED: on the pre-fix document this pattern matches FIVE times
    across FOUR clauses — the two `-> `1 1`` clauses (:108, :134) and the three
    `prints `1 1`` clauses (:114, :134 again, :143) — so the arm reds on the exact
    defect finding 1 names, and matches nothing on the fixed document.
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


def _added_cite_tokens(diff_text: str) -> set[str]:
    """The `sdk.py:<n>` tokens a `--word-diff=plain` patch ADDS to `docs/00_index.md`.

    Derived from the diff, never typed: `{+…+}` is word-diff's added span, so the set
    is empty exactly when the patch re-points no cite.
    """
    added = "".join(re.findall(r"\{\+(.*?)\+\}", diff_text, re.S))
    return set(re.findall(r"sdk\.py:(\d+)", added))


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
    # THIS repair's, and the tree refutes both — `git show e679d22286:tortoise/sdk.py
    # | cmp - tortoise/sdk.py` prints nothing, so `#13724`'s move is already on the
    # branch's base, and the live three-dot diff adds no `sdk.py:<n>` token.
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
    (`#13617`/`#13675`, `#13724`, `#13750`/`#13786`, `#13790`). A phrase present in the
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

    It is not, and the commands printed beside the claim refute it: at this head
    `git diff --name-only origin/main...HEAD -- docs/00_index.md` prints NOTHING (2-dot
    and 3-dot both empty, and `git rev-parse origin/main:docs/00_index.md
    HEAD:docs/00_index.md` prints the same blob twice), while
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
    """
    assert _added_cite_tokens("a [-sdk.py:21679-]{+sdk.py:21715+} b") == {"21715"}, (
        "_added_cite_tokens cannot read a word-diff patch that DOES move a cite, so the "
        "arm below would pass on an empty set for the wrong reason")

    proc = subprocess.run(
        ["git", "diff", "-U0", "--word-diff=plain", "origin/main...HEAD", "--",
         "docs/00_index.md"],
        cwd=REPO, capture_output=True, text=True, check=False)
    # ⛔ `else set()` WAS THE SAME P1 AS THE ARM ABOVE (#13802 review, finding 3 — the
    # root, in the sibling leg). A three-dot diff against a ref this checkout cannot
    # resolve exits non-zero, and folding that to an EMPTY set makes an UNPROVABLE
    # reading indistinguishable from a MEASURED one — so the moment a claim phrase is
    # present, a depth-1 checkout reds on a CORRECT artifact with the wrong accusation
    # ("the live three-dot diff adds no such token"). rc == 0 with NO added token is a
    # real measurement and still reds; only an UNREADABLE base skips, and it says so.
    if proc.returncode != 0:
        # ⛔ THE UNCONDITIONAL DIRECTION, NOT A SKIP (#13802 review, P1 — the same
        # defect as the sister arm's `pytest.skip`). This leg is the ONLY one that still
        # reads the document on a head whose cite sets DIFFER, and every job that runs a
        # suite checks out at `fetch-depth: 1`, so a skip here asserts nothing wherever
        # the rail reads it. With the base unreadable, NOTHING shows the diff adds a
        # `sdk.py:<n>` token — so an empty `moved` is not a measurement, and a claim may
        # not be carried on it. The message names which case it is, so an UNPROVABLE
        # reading is never passed off as a MEASURED one; the cost is an OVER-BLOCK on a
        # head that really does move a cite, which outranks a false PASS.
        moved = set()
        _why = (f"the live three-dot diff is UNPROVABLE here — `git diff -U0 "
                f"--word-diff=plain origin/main...HEAD` exits {proc.returncode} "
                f"({proc.stderr.strip()!r}); "
                f"{_base_ref_absent_detail() or 'the base ref does not resolve'}, so "
                "nothing shows this diff adds a cite token at all")
    else:
        moved = _added_cite_tokens(proc.stdout)
        _why = ("the live three-dot diff adds no such `sdk.py:<n>` token "
                f"(added: {sorted(moved) or 'none'})")

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
