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
  * `docs/epics/2026-08-03-tortoise-hosted-platform/.../08-hosted-prerequisites.md:133`
    — "cited by SYMBOL, never by line".

Sphinx's cross-reference guidance converges on the same rule from outside this
repo: explicit labels (`:ref:`) are preferred over fragile direct links because
labels survive edits and warn when they break.

This test does not ban line numbers — it refuses one that does not land on text.
Any surviving `:NN` pointer is therefore kept HONEST: insert a section above it
and this reds, naming the citing file and the dead target, instead of silently
redirecting a future reader to a blank line.

WHY THIS IS NOT A DUPLICATE OF THE EXISTING CITATION GUARDS

`tests/test_extractor_v4_citation_rot.py` (#8506) and
`tests/test_extractor_v4_doc_citations.py` (#8498) already hold this instrument,
but each is scoped to ONE document (`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`)
and resolves a different relation: a quoted `sym` against a `file.py:NNNN` whose
symbol must still exist in that source file. Neither reads a markdown-to-markdown
line pointer, and neither covers `docs/scoping/` or `docs/research/`, so the
regression above was invisible to both. This file is the same INSTRUMENT over the
missing subject, not a second implementation of theirs.

`tests/test_s2_decision_record_5087.py` (#10072) is the closest precedent for the
policy side: it pins that a document "must cite siblings by name, not ordinal".
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
     single `docs/scoping/*-317-*.md` / `docs/research/*-317-*.md` that exists.

STATED LIMITS — deliberate, and measured rather than guessed:

  * A bare ``:NN`` continuation (``(`:203`)``) carries no document name, so it
    cannot be resolved in isolation and is NOT checked. A citation meant to
    survive should carry its own name instead of leaning on a preceding one.
  * The guard is scoped to those two directories ON PURPOSE. `docs/` outside them
    held **10** further dead citations when this landed (ONTOLOGY.md ×3,
    `ops/registry-backup-dr.md` ×2, `infra/embedded-reaper-cron.md`,
    `architecture/STORAGE-ARCHITECTURE.md`, `plans/7714-data-model.md`,
    `epics/1402-eval-battery/01-align.md`), all pre-existing and none of them a
    target of #1165. A guard that shipped red would be disabled within a day, and
    repairing ten unrelated pointers is not this change's job — they are tracked
    in their own issue. Widening `_TARGET_SCOPE` is the follow-up once that
    backlog is paid.
"""
from __future__ import annotations

import re
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
_SHORT_REF = re.compile(r"`?(\d+)`?[-\s](scoping|research):(\d+)(?:-(\d+))?")


def _in_scope(target: Path, root: Path) -> bool:
    """Is this citation's target one this guard owns?"""
    parts = target.relative_to(root).parts if target.is_relative_to(root) else ()
    return bool(parts) and parts[0] == "docs" and len(parts) > 1 and parts[1] in _TARGET_SCOPE


def _dead_reason(lines: list[str], start: int, end: int | None) -> str | None:
    """The reason a citation is dead, or None when it lands on text."""
    last = end if end is not None else start
    if start < 1 or last > len(lines):
        return f"line {start} is outside the file ({len(lines)} lines)"
    if not lines[start - 1].strip():
        return f"line {start} is BLANK"
    return None


def _resolve_shorthand(
    number: str, kind: str, docs: Path
) -> tuple[Path | None, str | None]:
    """The single `docs/<kind>/*-<number>-*.md`, or (None, why not)."""
    candidates = sorted((docs / kind).glob(f"*-{number}-*.md"))
    if len(candidates) == 1:
        return candidates[0], None
    if not candidates:
        return None, f"no docs/{kind}/*-{number}-*.md exists"
    return None, f"ambiguous: {[c.name for c in candidates]}"


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


def test_the_real_docs_tree_is_not_empty():
    """A mis-rooted DOCS path would make every check above vacuous."""
    assert DOCS.is_dir()
    assert len(list(DOCS.rglob("*.md"))) > 100


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
