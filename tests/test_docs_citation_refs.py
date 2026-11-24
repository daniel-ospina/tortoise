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
    of the file. At the head the ten was measured at (`e66450bb`) one of its rows was
    exactly that: ``ONTOLOGY.md:1515`` against that head's shorter file, cited from
    `docs/plans/2026-09-10-ws-c-numeric-normalization.md:501`. `docs/ONTOLOGY.md` itself has
    since grown well past `:1515` (1,472 lines then, 1,566 here), so the row is IN RANGE
    at this head and the arm has no live
    example here — it is kept because its reachability argument is about the CODE (a
    `start > len(lines)` is the only way in), not about today's population. Cite a row
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
    widening against `origin/main` and gets **15**, so do not
    "correct" this 10 to that 15: they are two different heads and neither is the
    live one; paths below are relative to `docs/`: `ONTOLOGY.md` ×4 — three blank
    lines plus one PAST-EOF cite, which is a different repair from a blank
    one — `ops/registry-backup-dr.md` ×2, `infra/embedded-reaper-cron.md`,
    `architecture/STORAGE-ARCHITECTURE.md`,
    `epics/2026-08-03-tortoise-hosted-platform/plans/7714-data-model.md`,
    `epics/1402-eval-battery/01-align.md`. The five-row difference is itself
    head-bound and is stated so nobody re-derives it: `ONTOLOGY.md` grew from four
    flagged rows to seven (`:975` accounts for four of them, cited once each by
    `00-align.md`, `00-plan.md` twice and `01-research-brief.md`), one
    `ops/registry-backup-dr.md` row was repaired above,
    `architecture/STORAGE-PICTURE-2026-11-17.md` is new, and two
    `docs/infra-runbook.md` rows arrived with the `#7879` postgres-record epic — one
    cited by `08-hosted-prerequisites.md` and one by
    `11-residual-plan-and-decomposition.md`, both landing on a blank runbook line (the
    TARGET lines move with `main`; the citing files do not) —
    `10 - 1 + 3 + 1 + 2 = 15`). ⛔ WIDENING `_TARGET_SCOPE` ALONE CANNOT
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
    (`ONTOLOGY.md:833` is now a row of the `status` table, not the "Tombstone"
    controlled term — `passes_frequency_gate` is at `:841` there) and
    `docs/scoping/2026-10-13-8497-selfhost-journal.md:36`
    (`STORAGE-ARCHITECTURE.md:365` is now the "⚠️ And bound the growth" paragraph,
    not "a wiring gap, not a design gap" — the *three movements* "Write:" bullet is
    at `:394`), because `main` inserted text ABOVE both
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

WHAT THIS FILE NO LONGER CARRIES — EXCEPT THE ONE ARM THAT GUARDS A FALSE PASS
(2026-11-23, the test-bureaucracy cut)

Between this instrument's own arms lived ~8,850 lines that graded something else
entirely: the PROSE of the `#7949` capstone STATUS bundle (`docs/epics/2026-11-04-
7949-extractor-v4-capstone/05-verify.md`, its `D5` row, its tortoise-set and
numstat attributions, its index re-point sentences), with text bans, spellings
ledgers and mutant harnesses for each. That class is `B` by the audit's own test
(`#11081`, comment 7107649723, row B4): *the claim guarded is prose in a status
report*, and the failure it prevents is a sentence that is out of date — never a
product break. It was also the file's whole cost: the sweep is ONE test
(single-digit seconds of call time — it walks the whole `docs/` tree, so the figure
moves with the host's load; measure it with `--durations` rather than trusting a
number here), while the module's committed `durations` weight is still the PRE-CUT
18.3 s the 40-test module carried (`config/ci-surfaces.yml`'s row for this file —
another lane owns that block), and the arms rot on every `main` advance that
re-derives a figure the prose quotes.

THE COUNT, so nobody re-derives it: this file is 9,382 lines / 40 tests at
the base this cut was taken from (`989765162f`), and 743 / 11 here — 29 arms
removed, and TWO of the 31 kept: `test_the_bundle_does_not_report_a_census_verdict_it_did_not_measure`,
which compares `05-verify.md`'s claim about `hosted_api_cite_census.py --check`
against the checker's OWN `_sweep` (a green claim stays only while the verb exits 0
and a red claim stays out while it does, so neither sign is carryable), and its pivot
control
`test_the_agreement_arm_reads_the_derived_pivot_not_the_frozen_default`, without
which the first could pass on an arm that ignores what `--check` derives. ⛔ That
pair is KEPT BECAUSE IT IS NOT PROSE-GRADING: the failure it prevents is a FALSE
PASS — the bundle reported that verb as green while it exited 1, and nothing read
those cells for several review rounds (`#13489` review, P0) — which is category A,
and A is never cut, whatever the batch's row says. It is also the only merge-path
guard that verb has: its CI job is nightly
(`.github/workflows/main-health-nightly.yml`).

So the REST are gone, and NOT replaced by a cheaper spelling of the same thing: a
guard on a status report's SENTENCES is the thing that was cut. What remains is
the sweep and its own negative controls — the two properties a reader needs, that
a citation lands on text and that this guard can FAIL — plus the two arms above,
which measure rather than read prose. Do not reintroduce the
bundle arms here; a document that must not type an unmeasured figure belongs in
that document's own review, not in this file's suite.

⚠️ ONE deleted arm graded a DIFFERENT document, and it is named here rather than
left to be discovered:

    test_the_cypher_inventory_types_no_numstat_pair_for_a_range_re_taken_here

It read `docs/architecture/CYPHER-INVENTORY.md` and refused a typed
`range`/`numstat` pair for a range that document says is re-taken. It is
`B` by the same test (a number typed into a document, never a product break), and
this file's surviving scope is the `file:line` sweep, not that document's prose —
so the arm went with the rest. ⛔ That leaves the numstat pair over
`CYPHER-INVENTORY.md` UNGRADED BY ANY TEST, and the document is still committed:
`#11081`'s audit assigns its own cut the row (B1) that removes the document rather
than the pin, and that cut has not landed — so do not read this deletion as the
document being gone. Its generator checks live with it
(`tools/cypher_inventory.py --check`), and this greps to nothing:

    grep -n numstat tests/test_13098_cypher_inventory.py
"""
from __future__ import annotations

import re
from pathlib import Path

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
#: EVERY `docs/research/` record carrying the `-13-` token — **8** at this head
#: (`2026-08-13-265/309/316/317/388/405-*`, plus `2026-11-13-12298-m3-fts-leg` and
#: `2026-11-13-12360-underscore-or-terms`) — and reds a correct sentence naming any of
#: them (`docs/scoping/` holds **17** such records, several dated `2026-10-13`/
#: `2026-11-13`, so the count is per-kind and not a constant — and a count typed here
#: re-stales with the next record added).
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
    EVERY `docs/research/` record carrying the `-13-` token — 8 at this head (and 17
    in `docs/scoping/`, measured; the `-13-` token spans the `2026-08-13-*` AND the
    later `2026-11-13-*` records, so it is not one date) — and reds a CORRECT
    sentence naming those files. The guard must
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


def _census_agreement_verdict() -> int:
    """The verdict `tools/hosted_api_cite_census.py --check` prints, on the SAME path it takes.

    ⛔ `census()` DEFAULT-ARGUMENTS the frozen `DEFAULT_PIVOT`, while the verb's `--check`
    path calls `census(sites[0])` with `sites = lifetime_query_sites()`. Measuring `census()`
    therefore asserts agreement on a path the named verb does not take: MEASURED (#13807
    review round 7, P1), with the frozen literal one line behind the tree, `_sweep(census())`
    -> `0` while `_sweep(census(lifetime_query_sites()[0]))` -> `1` — "agreement" reported
    over a tree the verb it names reports as STALE. The shape `--check` refuses (more than
    one site) is refused here rather than silently graded on the wrong pivot.
    """
    from tools.hosted_api_cite_census import _sweep, census, lifetime_query_sites

    sites = lifetime_query_sites()
    assert len(sites) == 1, (
        f"{len(sites)} `query(LIFETIME_MEMORY_QUERY)` sites in `tortoise/hosted_api.py` "
        f"({sites}). `--check` refuses to run on that shape, so this arm cannot claim the "
        "verdict that verb prints")
    return _sweep(census(sites[0]), refresh=False)


def test_the_agreement_arm_reads_the_derived_pivot_not_the_frozen_default(monkeypatch):
    """The agreement arm must measure the pivot `--check` DERIVES, not the frozen literal.

    ⛔ NON-VACUITY IS THE POINT, so the probe does not assume which path is green: it
    replaces `lifetime_query_sites` with a value one line away from `DEFAULT_PIVOT` and
    records the pivot the arm actually passes to `census`. Both legs are required — (a) the
    probe must SEPARATE the two paths, or the test would pass on an arm that ignores the
    pivot, and (b) the arm must take the DERIVED one. Restoring `verdict = _sweep(census())`
    reds (b), which is the regression a union resolution (`49fe860faa`) introduced.
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
        "refuse")


def test_the_bundle_does_not_report_a_census_verdict_it_did_not_measure():
    """⛔ A document reported a checker's EXIT CODE as green while the checker was RED.

    `docs/epics/2026-11-04-7949-extractor-v4-capstone/05-verify.md` stated, in three cells,
    that `tools/hosted_api_cite_census.py --check` "exits 0" / "reports every stated figure
    `== measured`". MEASURED at that head the verb exits **1** with twelve `STALE` lines, so
    no landing of that branch could have made the claim true — and NOTHING read those cells:
    this file resolves POINTERS, and `test_7949_capstone_measure.py`'s D5 arms are scoped to
    D5's own line, so a FALSE PASS survived several review rounds inside the document whose
    subject is stale claims (#13489 review, P0).

    ⛔ THIS IS THE ONE ARM OF THE `#7949` PROGRESS-DOC CLASS THAT SURVIVED THE BUREAUCRACY
    CUT, because it is the one that is not prose-grading: it takes a MEASUREMENT with the
    checker's own `_sweep` and compares the document's claim against it. The other `#7949`
    arms (text bans, spelling ledgers, numstat mutants) were removed by the owner-approved
    cut (`#11081`, comment 7107649723, row B4); this one was KEPT because the failure it
    prevents is a false PASS, which is category A, and A is never cut.
    `hosted_api_cite_census.py --check` reaches the merge path only through this arm — its
    CI job is nightly (`.github/workflows/main-health-nightly.yml`).

    ⛔ BOTH DIRECTIONS BIND, AND THE ASYMMETRY BETWEEN THEM IS STATED RATHER THAN SMOOTHED
    OVER (the first compact rewrite claimed a symmetry it had not earned; review caught both
    halves of this): a GREEN claim stays in the document only while the verb exits 0, and a
    RED claim stays out while it does. The GREEN side has a live witness at this head — the
    document really does state `exits 0` and carry the verb's output line — while NO
    `_red_claims` spelling occurs in it, so that half is a live GUARD whose only firing edit
    is one a lane would have to type. Each side is a FINITE, disclosed list, so a re-wording
    outside both is not detected; that is the same finite-class boundary the removed spelling
    ledgers stated, and stating it is what keeps this arm from describing itself as complete.

    ⛔ WHAT WAS DROPPED WITH THE CUT, named so it is not discovered later: the base arm also
    asserted (b) POINTERS — that the document still names the verb, and names every path
    `hosted_api_cite_census._note_paths()` returns — and (c) TYPED-ANSWER bans over the
    recorded tortoise-set spellings. Both grade SPELLINGS in prose, i.e. the B4 class, and
    both were removed with it. TWO halves of the (b) leg are KEPT here because they cost
    nothing and are not head-bound: the document must still NAME the verb (deleting a false
    verdict has to send the reader to the command, never away from it), and a green claim
    must carry the verb's own output line. What is no longer checked is the `_note_paths()`
    MEMBERSHIP list.

    ⛔ ONE OVER-BLOCK, NAMED RATHER THAN CARRIED SILENTLY: at a RED head the branch below
    refuses every green spelling, INCLUDING a head-labelled provenance quote —
    `05-verify.md` keeps `At 2a7f24e241 it exits 0 and prints …`, which is TRUE of the head
    it names and would nevertheless red. The arm cannot tell a present-tense assertion from
    a quote without parsing prose, so it errs toward refusing; the edit it asks for (state
    the command, read it at the head you merge) is one line, and the alternative is a
    matcher permissive enough to reintroduce the false PASS above.
    """
    doc = (
        DOCS / "epics" / "2026-11-04-7949-extractor-v4-capstone" / "05-verify.md"
    ).read_text(encoding="utf-8")
    verdict = _census_agreement_verdict()
    # The spellings the document actually uses. ⛔ The GREEN side has a live witness in
    # `05-verify.md` today (`exits 0` ×1, the output line ×2); NONE of `_red_claims`
    # occurs there at this head — that half fires only if a lane TYPES a red, which is
    # exactly the edit it exists to refuse, so it is a live guard and not a live witness.
    _green_witness = "every stated figure is the measured one"
    _green_claims = (
        "--check` exits 0",
        "--check` reports every stated figure `== measured`",
        "--check` reports both figures `== measured`",
        "exits 0",
    )
    _red_claims = (
        "--check` exits 1",
        "--check` is RED",
        "reports twelve `STALE` lines",
    )
    # ⛔ THE POINTER HALF OF THE BASE ARM'S (b) LEG, kept because it is not head-bound: the
    # document must still name the verb. Deleting a false verdict sends the reader to the
    # command; deleting the POINTER hides that there is a command to run at all.
    assert "tools/hosted_api_cite_census.py --check" in doc, (
        "05-verify.md no longer names `tools/hosted_api_cite_census.py --check` anywhere. "
        "Removing a verdict must send the reader to the verb, not delete the pointer to it")
    if verdict == 0:
        # ⛔ A GREEN CLAIM, IF THE DOCUMENT MAKES ONE, MUST BE THE VERB'S OWN OUTPUT LINE. The
        # arm does NOT require the document to TYPE a verdict: this document's own policy is
        # "state the command, read it at the head you merge", so demanding a head-bound number
        # would red the correct edit (round 25, P2 — the earlier form asserted a witness
        # presence, which passed only because two HISTORICAL sentences happen to quote it).
        if any(green in doc for green in _green_claims):
            assert _green_witness in doc, (
                "05-verify.md states a green claim about "
                "`tools/hosted_api_cite_census.py --check` but does not carry the verb's own "
                f"output line {_green_witness!r}. A green that is not the verb's output is a "
                "claim about a head nobody measured; quote the line or state the command")
        # ⛔ THE OTHER SIGN, which the first compact rewrite left carryable: a RED claim in a
        # document whose checker is GREEN sends the next reader to repair drift that does not
        # exist. It is refused ONLY when the verb is green — run unconditionally it would also
        # forbid the sentence a RED head must be able to carry, which is the same defect with
        # the sign flipped (round 24, P1: measured — the unconditional loop made "delete the
        # verdict" the only edit that passed at a red head).
        for red in _red_claims:
            assert red not in doc, (
                f"05-verify.md asserts {red!r} while `--check` exits 0 here (measured "
                "in-process with the module's own `_sweep`). Read the verdict at the head "
                "being merged and state THAT; a document frozen red over a current tree is "
                "the same defect as a frozen number, with the sign flipped")
    else:
        for green in (*_green_claims, _green_witness):
            assert green not in doc, (
                f"05-verify.md asserts {green!r} while `--check` exits {verdict} here "
                "(measured in-process with the module's own `_sweep`). Landing this would "
                "put a false PASS claim into a document beside a checker that refutes it. "
                "State the COMMAND and the head it must be read at — never a green")


def test_the_real_docs_tree_is_not_empty():
    """A mis-rooted DOCS path would make every check above vacuous."""
    assert DOCS.is_dir()
    assert len(list(DOCS.rglob("*.md"))) > 100
