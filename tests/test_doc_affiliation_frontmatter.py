"""Doc-affiliation frontmatter and copied-governance claims (#8738 review P2).

THE RULE THIS PINS
------------------
AGENTS.md requires every ``docs/`` file to declare its affiliation — the
``document-conventions`` required fields ``subjects.team`` (or ``ownedBy``),
``type``, ``domain``, ``doc_status`` and ``created``. ``scripts/check-doc-
affiliation.cjs`` enforces this at commit time, but it is NOT wired into CI, so
a file that drops a required field can still land green. That is exactly what
happened: the three ``*-CHANGELOG.md`` companions added by #8738 carried
``title``/``type``/``domain``/``doc_status`` but neither ``created`` nor a team
key, and the two under ``docs/architecture/`` broke that directory's own
convention (every pre-existing ``docs/architecture/*.md`` has both).

A second, adjacent claim defect landed with the same move: the STORAGE
changelog carried `ONTOLOGY.md`'s file-modification gate verbatim
(``no modification to this file is allowed without explicit approval…``). That
rule was never established for the storage document — its sibling EXTRACTOR
changelog carries none — and, read in a changelog, it gates the one file whose
purpose is to keep changing.

WHAT THIS PINS
--------------
1. The three changelog companions declare every required affiliation field.
2. Every ``docs/architecture/*.md`` declares them too — the directory
   convention, not just this change's three files.
3. A ``docs/architecture/*-CHANGELOG.md`` companion does not assert
   `ONTOLOGY.md`'s owner-approval gate; the rule stays in the one file it
   governs.
4. Every ``docs/evidence/*/README.md`` declares them — the CLASS, not one
   pinned bundle. The single-file pin added for #9110 was satisfied while a
   second bundle (#9664) landed with no frontmatter at all.
5. Every ``docs/research/**/*.md`` frontmatter block is **parseable YAML**, and
   the required fields survive the parse. The four arms above check that the
   keys are PRESENT as lines; a block can carry every key and still fail to
   parse, in which case ``tortoise.file_indexer.parse_frontmatter`` returns
   ``{}`` by design and the whole affiliation vanishes — met on paper only.
6. (#13452) A frontmatter VALUE **survives**, not merely its key: a value written
   as a plain scalar and containing a space-preceded `#` is silently TERMINATED
   there — the rest becomes a YAML comment — so the block parses, the key
   survives, and the value is short. Measured on ``main`` before this arm: 8 of
   68 blocks, every one an ``aboutObjects``, the merged dossier's value landing
   at 57 characters of its 141. Check 5 cannot see any of them, because presence
   is not survival.

Non-vacuity: on the pre-fix tree the two architecture companions carry neither
``created`` nor a team key, and the STORAGE companion carries the copied gate —
so checks 2 and 3 fail there; and ``docs/evidence/9664-capstone/README.md``
opens at ``# #9664 — capstone acceptance``, so check 4 fails there; and
``docs/research/2026-11-18-13420-vector-placement/dossier.md`` opened its
``aboutSubjects`` value with a backtick, so check 5 fails there; and eight
``aboutObjects`` values were terminated early at an inline ``#`` comment, so
check 6 fails there.

Static, no network / DB / graph.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The required `docs/` affiliation fields (AGENTS.md -> document-conventions).
REQUIRED_KEYS = ("type", "domain", "doc_status", "created")
TEAM_KEYS = ("subjects.team", "ownedBy")

# ── #13452: presence is not SURVIVAL ───────────────────────────────────────

#: A value whose text BEGINS with `#` is **entirely** a YAML comment: 100% of it
#: is gone. A SCALAR key then resolves to `None`; a SEQUENCE ITEM resolves to
#: `null` and leaves the key a list holding that null, so which unit is named
#: matters when the diagnosis is worded (review: "the key resolves to None" was
#: false for exactly the `- #11395` shape this arm cites as its mutant). This is
#: UNAMBIGUOUS — a key with a real value never resolves to `None` because of a
#: comment (`null`/`~` carry no `#`) — so no digit needs to appear for it to be a
#: defect. YAML ends a plain scalar at any `#` not preceded by a non-space
#: character (`ns-plain-char(c)`, YAML 1.2.2 §7.3.3; PyYAML here is 6.0.3, a
#: YAML 1.1 resolver whose [154]/[156] productions state the same rule).
_WHOLE_VALUE_COMMENT = "#"

#: `key: value` / `key:` / `key:   ` at column 0. Group 2 is the one-line value,
#: and it is OPTIONAL — a trailing space must not let a null-ish value slip past
#: enumeration, or check (D) below could be evaded by one keystroke (review).
_KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):[ \t]*(\S.*)?$")

#: A block-sequence item under a key (`- value`).
_ITEM = re.compile(r"^[ \t]*-[ \t]+(\S.*)$")

#: Characters that open a block scalar (`|`, `>`) or a flow collection (`[`, `{`).
_BLOCK_OR_FLOW = "|>[{ "

#: The two fields that carry the affiliation itself (#13452). PRESENCE is the
#: OTHER arms' business — they check ``REQUIRED_KEYS`` and ``TEAM_KEYS`` and not
#: these two, so deleting an affiliation key outright stays green here BY DESIGN
#: (measured in review: removing the `aboutObjects:` line leaves this arm at
#: ``1 passed``). What this arm measures is SURVIVAL: every one of these VALUES
#: must be measured by a named check, or the arm fails — an unmeasured affiliation
#: is exactly the defect (a value at 40% of its length keeps its key and its green).
AFFILIATION_KEYS = ("aboutSubjects", "aboutObjects")

#: Styles this arm declines to compare, as explicit ``(path, key)`` pairs.
#: DELIBERATELY EMPTY: #13452's target is zero truncated values AND zero silent
#: omissions, so an unmeasured affiliation FAILS (check D) until somebody writes
#: the pair here — a new exemption is a line a person had to add, never a blanket
#: rule that lets a whole style or file go unmeasured without saying so.
UNMEASURED: frozenset[tuple[str, str]] = frozenset()


def _type_mismatch(key: str, raw: str, got: object, unit: str) -> str:
    """Explain a value that resolved to a NON-STRING without misdescribing why.

    Two different mechanisms produce this shape, and they are oppositely bad:

    * **YAML implicit typing** — `13452` -> int, `2026-11-18` -> date, `true` ->
      bool. NOTHING is lost; the text is intact, just not a `str`. Reporting
      this as a truncation sends the reader after a defect that is not there.
    * **A space-preceded `#` ending the scalar** — the tail is discarded, and the
      REMAINDER may then resolve to a non-string (`13452 #11395 reopen, #13220`
      -> int `13452`, 22 author-written characters gone). This is exactly the
      #13452 defect, and calling it "implicit typing, not a truncation" would
      deny the very defect this guard exists for.

    Both were found in review, one in each direction, so the branch is decided
    from the RAW text rather than assumed: a ` #` in it means the tail was cut.
    The remedy is the same in both cases, and the arm still fails closed.
    """
    if " #" in raw:
        cut = raw.index(" #")
        return (
            f"`{key}` {unit} {raw!r} became {type(got).__name__} ({got!r}): a "
            f"space-preceded `#` ended it at character {cut} of {len(raw)}, so "
            f"the tail is GONE — this is a truncation, whatever type the "
            f"remainder resolved to. Quote the {unit}."
        )
    return (
        f"`{key}` {unit} {raw!r} resolves to {type(got).__name__} ({got!r}), "
        f"not a string — YAML implicit typing, nothing was lost. Quote the {unit}."
    )


def _frontmatter(path: Path) -> list[str]:
    """Return the lines of the leading YAML block (empty list if there is none)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return []


def _values(fm: list[str]) -> list[tuple[str, str, object]]:
    """Classify each top-level frontmatter value: ``(key, kind, payload)``.

    Kinds, each distinguishable WITHOUT re-implementing YAML:

    ``scalar``              one-line value; ``payload`` is its raw text.
    ``scalar_multiline``    a plain value continued on the next line — legal
                            YAML FOLDING, and the shape in which a continuation
                            line opening with `#` is swallowed (measured: the
                            value parses to its first line only). Folding cannot
                            be verified without becoming a YAML implementation,
                            so this arm REFUSES the style and asks for quotes.
                            A QUOTED value that CLOSES on its line is not
                            continued by an indented COMMENT line — the comment
                            is just a comment. ⚠️ That is NOT the same as "a
                            quoted value is always closed on its line": a quoted
                            scalar may legally continue onto the next line, which
                            this arm still refuses (see the multi-line residual in
                            the plan doc §9) — the narrower claim is the true one.
                            A PLAIN value can continue, and a continuation line
                            opening with `#` becomes a comment — so that shape is
                            refused.
    ``sequence``            ``key:`` then ``- item`` lines (which may sit at
                            column 0, as several files in this population do).
                            A `- #11395` item is a whole-value comment and parses
                            to ``None``; items are therefore checked ONE BY ONE —
                            the leading-`#` shape, the quoting shape, and (for the
                            affiliation keys) item-by-item value survival. A
                            shorter list or a changed item means the value did not
                            survive.
    ``sequence_multiline``  a sequence whose item is continued on a following
                            indented line: the same swallowing shape, refused.
    ``other``               block scalar / flow collection / nested mapping.
                            `|`/`>` preserve text by construction and a flow
                            collection is not this defect's shape, but these
                            reach NO check — so an AFFILIATION value in this kind
                            is reported by check (D), never passed quietly.
    """
    out: list[tuple[str, str, object]] = []
    i = 0
    while i < len(fm):
        m = _KV.match(fm[i])
        if not m:
            i += 1
            continue
        key, raw = m.group(1), m.group(2)
        if raw is not None:
            raw = raw.rstrip()
            if raw[:1] in _BLOCK_OR_FLOW:
                out.append((key, "other", raw))
                i += 1
                continue
            nxt = fm[i + 1] if i + 1 < len(fm) else ""
            # A QUOTED scalar that closes ON THIS LINE is closed: a following
            # indented line cannot be part of it, so an indented COMMENT is just a
            # comment. (A quoted scalar can legally continue onto the next line;
            # that shape is refused elsewhere, see the plan doc §9 residual — this
            # branch must not be read as claiming every quoted value is closed
            # here.) A PLAIN scalar can
            # continue — and a continuation line opening with `#` becomes a
            # comment, which is the truncation this arm refuses. Getting this
            # backwards was measured both ways: treating every indented `#` line
            # as a fold reddened a value YAML reads intact, and skipping every
            # `#` line let a truncated continuation through green.
            quoted = raw[:1] in ('"', "'")
            continued = (
                not quoted
                and nxt[:1] in (" ", "\t")
                and bool(nxt.strip())
                and not _ITEM.match(nxt)
                and not _KV.match(nxt.strip())
            )
            out.append((key, "scalar_multiline" if continued else "scalar", raw))
            i += 1
            continue
        items: list[str] = []
        continued = False
        j = i + 1
        while j < len(fm):
            im = _ITEM.match(fm[j])
            if im:
                items.append(im.group(1).rstrip())
                j += 1
                continue
            if items and fm[j][:1] in (" ", "\t") and fm[j].strip():
                continued = True
                j += 1
                continue
            break
        if items:
            out.append((key, "sequence_multiline" if continued else "sequence", items))
            i = j
        else:
            out.append((key, "other", ""))
            i += 1
    return out


def _closing_quote(raw: str) -> int:
    """Index of ``raw``'s closing quote, or ``-1`` when the scalar never closes.

    Scanned by PARITY, not searched for a ``q`` at the end of the text before a
    `` #``: a single-quoted body ending in the ``''`` escape leaves a quote right
    before such a `` #``, and the old
    ``raw[:i].rstrip().endswith(q)`` test read that escape as the close (found in
    review, measured: ``'the ''KEEP'' #11395 ruling'`` parses INTACT to
    ``the 'KEEP' #11395 ruling``, yet the arm red it as an unbalanced quote and
    told the author to *remove the inner quote* — advice to delete a legal
    escape). A double-quoted body is scanned with backslash escapes skipped.
    """
    q = raw[0]
    i = 1
    while i < len(raw):
        ch = raw[i]
        if q == '"' and ch == "\\":
            i += 2
            continue
        if ch == q:
            if q == "'" and raw[i : i + 2] == "''":
                i += 2  # YAML's only single-quote escape: two quotes are one
                continue
            return i
        i += 1
    return -1


def _split_comment(raw: str) -> str:
    """A QUOTED value's text with a trailing `` <space>#…`` comment removed.

    Only quoted values reach this: the split happens solely after the closing
    quote, so a ` #` inside the quotes stays content. A PLAIN value is
    deliberately NOT split — YAML ends it at the `#`, and the caller compares the
    parse against the RAW text, so a shortened value FAILS instead of quietly
    matching a value that was shortened the same way. (An earlier draft DID split
    plain values here, which made the survival comparison tautological and let it
    report coverage it did not have — found in review.)

    The close is located by ``_closing_quote``, so only a ` #` that sits AFTER
    the true closing quote is treated as a comment. A split at any earlier ` #`
    is refused, which keeps (C)'s truncation case red: `"a, "#1" KEEP"` has no
    ` #` after its close, so the raw text survives to (C) and still reports the
    unescaped inner quote.
    """
    close = _closing_quote(raw)
    if close == -1:
        # ⛔ THE `-1` CASE **IS REACHED** — any never-closing quoted value (`"abc`,
        # `"a\"`) returns through this branch. Said plainly because an earlier
        # version called it "UNREACHABLE in practice", which is false in the one
        # way that matters: a reader acting on it would delete a line that RUNS.
        # What is unreachable is a different **outcome**. `raw[0]` is a quote and
        # every ` #` sits at `i >= 1`, so `len(raw[:i].rstrip()) - 1` can never be
        # `-1`: the loop below cannot match and falls through to the same
        # `return raw`. So this branch is BEHAVIOURALLY REDUNDANT, not dead code —
        # deleting it leaves the file at `2 failed, 16 passed` (re-measured at
        # `dad7aea959`: the 2 pre-existing absence reds and nothing else), which is
        # why no test CAN pin it, not because the line never executes. The caller
        # is unaffected either way — `_quoted_style` re-runs the same parity scan
        # and reports `malformed` for a value with no close, rather than trusting
        # `endswith`, since a body whose last character is an ESCAPED quote ends in
        # the quote char anyway.
        return raw
    i = raw.find(" #")
    while i != -1:
        if len(raw[:i].rstrip()) - 1 == close:
            return raw[:i].rstrip()
        i = raw.find(" #", i + 1)
    return raw


def _has_unescaped_dq(body: str) -> bool:
    """True when ``body`` holds a `"` that is not escaped by a backslash."""
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == '"':
            return True
        i += 1
    return False


def _unkeepable_quoted(subject: str, style: str) -> str:
    """The diagnosis for a quoted value this arm does not compare, BY VERDICT.

    ``broken`` and ``malformed`` have DIFFERENT causes, so they need different
    remedies — and one shared sentence gave both the truncation remedy, *"the
    value is TERMINATED there. Remove the inner quote"*. That is true of
    ``broken`` (YAML really does stop at an inner quote) and FALSE of
    ``malformed``, whose members split into two groups the shared sentence damaged
    in DIFFERENT ways (lumping them was itself a small false claim — a member of
    this family is a parse error, so "YAML keeps every member whole" is not true):

    * YAML KEEPS THESE WHOLE — `"x"# note` (a trailing comment with no space
      before it) and a MULTI-LINE quoted scalar. Here *"remove the inner quote"*
      asks the author to delete a character the value needs: the same
      false-remedy class as the `''`-escape finding this PR exists to remove.
    * A scalar that never closes is a PARSE ERROR (`ScannerError`), so YAML keeps
      NONE of it. The shared sentence was merely INAPPLICABLE there — there is no
      inner quote to remove — so this member needs the new remedy for a different
      reason, not because the old one would have destroyed something.

    No verdict changes: both arms still refuse the file.

    ⛔ NEITHER message offers "use a block scalar", and the reason is this module's
    own invariant rather than a preference: ``_values`` classifies `|`/`>` as
    `kind == "other"`, and (D) records ``measured`` only for ``kind != "other"``
    (the `if kind != "other" and key in AFFILIATION_KEYS` guard in
    `test_research_doc_frontmatter_parses_as_yaml` — cited by NAME, because
    appending a test moves that line number), so an affiliation value written as
    a block scalar reaches NO check and reds as *"NOT measured by ANY check —
    quote the value (plain or simply-quoted is measured)"*. Recommending it
    swaps this red for a second one whose own remedy is to quote — the exact
    false-remedy class this guard exists to remove. Both messages below name
    only edits that CLEAR the red.
    """
    if style == "broken":
        return (
            f"{subject} is broken — a quoted value YAML cannot keep whole is "
            "TERMINATED at an inner quote. Remove the inner quote."
        )
    return (
        f"{subject} is malformed — this arm will not compare it. Either the quoted "
        "scalar never closes (a value continued onto the next line is refused here "
        "too), or its closing quote is not the last thing in the value: a comment "
        "with no space before the `#` (put one space before the `#`) or other text "
        "after the close. Keep the whole value on one line inside its quotes."
    )


def _quoted_style(raw: str) -> tuple[str, str | None]:
    """``(style, body)`` for a quoted value; ``("plain", None)`` when unquoted.

    ``broken`` is the TRUNCATION check (C) exists for: the scalar closes at an
    inner quote, so YAML ends the value there — an UNESCAPED inner `"`, or an
    unbalanced `'` (measured: `"a, "#1" KEEP"` parses to `'a, '`). The
    unescaped-quote test runs BEFORE the escape branch, because an escaped body can
    still carry an unescaped quote and an earlier order let that truncation through
    green (found in review).

    ``malformed`` is the no-WHOLE-scalar check, a different shape, and it has TWO
    conjuncts that are neither interchangeable nor redundant: no quote in the value
    terminates the scalar (``_closing_quote`` returns ``-1``), **or** the close it
    finds is not at the END of the splittable text. `"x"# note` has its close at
    index 2, so only the end-of-text conjunct fires there and YAML keeps the value
    (`x`); `"a\\"` closes nowhere, so only the parity scan fires and YAML keeps
    none of it. Deciding wholeness by ``endswith`` ALONE is the historical defect —
    a body whose last character is an ESCAPED quote ends in the quote char yet
    never closes, and that sent it to ``escaped``, where the comparison is declined
    (found in review: the shape escaped measurement entirely).

    ``escaped`` declines the value comparison (hand de-quoting cannot reproduce
    `\\t`, `\\uXXXX`) but has already been checked for ``broken`` and
    ``malformed``.
    """
    if raw[:1] not in ('"', "'"):
        return "plain", None
    q = raw[0]
    value = _split_comment(raw)
    # Wholeness is decided by the SAME parity scan that locates the close. A
    # truncation shape still closes at an inner quote, so `_closing_quote` finds
    # it (`"a, "#1" KEEP"` -> 4) and it keeps reaching `broken` below.
    if _closing_quote(value) == -1 or not value.endswith(q):
        return "malformed", None
    body = value[1:-1]
    if q == '"':
        if _has_unescaped_dq(body):
            return "broken", None
        if "\\" in body:
            return "escaped", None
        return "double-quoted", body
    if re.search(r"(?<!')'(?!')", body):
        return "broken", None
    return "single-quoted", body.replace("''", "'")


#: The changelog companions this change adds — the files the review flagged.
COMPANIONS = (
    "docs/ONTOLOGY-CHANGELOG.md",
    "docs/architecture/EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md",
    "docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md",
)


def _missing(path: Path) -> list[str]:
    fm = _frontmatter(path)
    missing = [
        key
        for key in REQUIRED_KEYS
        if not any(ln.startswith(f"{key}:") for ln in fm)
    ]
    if not any(any(ln.startswith(f"{key}:") for ln in fm) for key in TEAM_KEYS):
        missing.append("subjects.team|ownedBy")
    return missing


@pytest.mark.parametrize("rel", COMPANIONS)
def test_companion_changelogs_declare_the_required_frontmatter(rel: str) -> None:
    missing = _missing(ROOT / rel)
    assert not missing, (
        f"{rel} must declare {missing} — the AGENTS.md doc-filing rule; a "
        "changelog companion is still a `docs/` file."
    )


def test_architecture_docs_declare_the_required_frontmatter() -> None:
    failures = {
        str(path.relative_to(ROOT)): _missing(path)
        for path in sorted((ROOT / "docs" / "architecture").glob("*.md"))
        if _missing(path)
    }
    assert not failures, (
        f"docs/architecture/*.md missing required affiliation frontmatter: {failures}"
    )


def test_ops_docs_declare_the_required_frontmatter() -> None:
    """#5045 review P2: every ``docs/ops/*.md`` declares its affiliation.

    ``docs/ops/`` is a directory whose own convention is stronger than the rule
    (all ten pre-existing files carry ``title``, ``type``, ``domain``,
    ``doc_status``, ``subjects.team``, ``aboutSubjects``, ``aboutObjects``,
    ``created``, ``updated``), yet nothing pinned it — so a new operator doc
    (``cost-dimensions.md``) landed with NO frontmatter at all and merged green,
    leaving the two subjects and the object that block exists to carry recorded
    nowhere. Same shape as the ``docs/architecture/`` arm above.

    Non-vacuity: at the pre-fix tree ``docs/ops/cost-dimensions.md`` starts at
    ``# Cost dimensions …``, so this reds on it.
    """
    paths = sorted((ROOT / "docs" / "ops").glob("*.md"))
    assert paths, "docs/ops/ has no markdown files — re-point this guard"
    failures = {
        str(path.relative_to(ROOT)): _missing(path)
        for path in paths
        if _missing(path)
    }
    assert not failures, (
        f"docs/ops/*.md missing required affiliation frontmatter: {failures}"
    )


#: `ONTOLOGY.md`'s rule for itself. It is a fact of that file alone; a companion
#: that repeats it asserts an approval gate that was never established for it.
ONTOLOGY_OWNER_GATE = (
    "no modification to this file is allowed without explicit approval of the "
    "exact changes by Daniel Ospina"
)


def test_architecture_changelogs_do_not_assert_the_ontology_owner_gate() -> None:
    """A changelog must not copy `ONTOLOGY.md`'s file-modification gate.

    Review finding 2: the new STORAGE companion carried the ontology rule
    verbatim, which (a) contradicts its sibling EXTRACTOR companion, which has
    none, and (b) gates modification of the one document whose purpose is to
    keep changing. The gate belongs to `ONTOLOGY.md`, the file it names.

    Non-vacuity: at the pre-fix sink `STORAGE-ARCHITECTURE-CHANGELOG.md`
    carried the line verbatim, so this reds on it. The positive arm keeps the
    guard from passing vacuously if `ONTOLOGY.md` ever loses its own rule.
    """
    ontology = (ROOT / "docs" / "ONTOLOGY.md").read_text(encoding="utf-8")
    assert ONTOLOGY_OWNER_GATE in ontology, (
        "ONTOLOGY.md no longer states its own approval rule — re-point this "
        "guard before trusting its greenness (#8745)."
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in sorted((ROOT / "docs" / "architecture").glob("*-CHANGELOG.md"))
        if ONTOLOGY_OWNER_GATE in path.read_text(encoding="utf-8")
    ]
    assert not offenders, (
        "a docs/architecture changelog asserts ONTOLOGY.md's file-modification "
        "gate, which was never established for it and contradicts a changelog's "
        "purpose: " + ", ".join(offenders)
    )


#: `docs/` file that predates the affiliation rule (and already carries baseline
#: `docs`-job findings). Listed, never silently skipped.
GRANDFATHERED = "docs/evidence/3770-embedded-lane-preexisting-failures/README.md"


def test_every_evidence_bundle_readme_declares_its_affiliation() -> None:
    """#9664 review P2: close the CLASS, not one pinned bundle.

    ``tests/test_doc_affiliation_frontmatter.py``'s #9110 arm pins exactly one
    file (``docs/evidence/7949-capstone/E1-E2.md``), and #9664's bundle landed
    with NO frontmatter block while that pin stayed green. Verified with the
    repo's own checker: ``check-doc-affiliation.cjs --files
    docs/evidence/9664-capstone/README.md`` → ``ERROR: Missing front matter
    block``. The checker is NOT wired into CI (``.github/workflows/ci.yml``
    says so), so only a test over the population closes it.

    ``GRANDFATHERED`` is the one bundle that predates the rule; it is listed
    explicitly (not silently skipped) so the exception is a decision this test
    records rather than an accident of the glob.

    Non-vacuity: at the pre-fix tree ``docs/evidence/9664-capstone/README.md``
    opens at ``# #9664 — capstone acceptance``, so this reds on it.
    """
    assert _missing(ROOT / GRANDFATHERED), (
        "the grandfathered bundle now HAS frontmatter — drop it from the "
        "exception list instead of leaving a dead exemption behind"
    )
    paths = [
        path
        for path in sorted((ROOT / "docs" / "evidence").glob("*/README.md"))
        if str(path.relative_to(ROOT)) != GRANDFATHERED
    ]
    assert paths, "docs/evidence/ has no README.md files — re-point this guard"
    failures = {
        str(path.relative_to(ROOT)): _missing(path)
        for path in paths
        if _missing(path)
    }
    assert not failures, (
        "docs/evidence/*/README.md missing required affiliation frontmatter: "
        f"{failures} — AGENTS.md's Documentation Filing Protocol; copy the "
        "block from docs/evidence/7950-capstone/README.md"
    )


def test_research_doc_frontmatter_parses_as_yaml() -> None:
    """#13432 F1 + #13452: presence is not PARSEABILITY, and neither is SURVIVAL.

    #13432: a YAML plain scalar may not begin with a backtick, so a block can
    carry every required key as a LINE (which ``_missing`` sees, and which
    ``scripts/check-doc-affiliation.cjs`` sees too) and still fail to parse.
    ``parse_frontmatter`` catches the error and returns ``{}`` **by design**, so
    the failure is silent — the consumer sees no frontmatter at all.

    #13452: a value can also be silently SHORT. A plain scalar containing a
    space-preceded `#` ends at that `#` (YAML admits `#` in a plain scalar only
    with a non-space lookbehind), so
    `aboutObjects: A, #11395 reopen, #13220` parses to `A,` — key present, block
    parseable, seven references gone. On `main` **before `#13476` landed** —
    `035ed183fc`, which is `a5f1e7db46^` — that probe measured 68 blocks, 8
    truncated, every one an `aboutObjects`; the merged dossier's landed at 57 of
    141 chars. ⛔ The figure is TIED to that head on purpose: `#13476` is on
    `main` now, so the same probe over the same glob at this head reports
    **0** truncated values (`files: 103 blocks: 68 broken(truncated): 0`,
    re-measured at `10a3d694`). Stating `8` as a present-tense `main` measurement
    would be the exact defect this module exists to prevent — the same scoping
    the plan doc §1 already carries, which this docstring did not.
    Presence, and even parseability, is not survival.

    Both are read through the SAME consumer the index pipeline uses, so this
    measures the real degradation rather than a re-implementation.

    The affiliation VALUE is measured five ways, because one is provably not
    enough — each of the first three was demonstrated to miss a real truncation:

    (A) a value whose text BEGINS with `#` is entirely a comment: 100% of it is
        gone. A SCALAR key then resolves to ``None``; a SEQUENCE ITEM resolves to
        ``null`` and leaves the key a list holding that null — and in a MIXED list
        the other items still survive, so neither "the key is entirely a comment"
        nor "the key resolves to None" is true of that shape. Checked for EVERY key
        and for sequence items; unambiguous, so it needs no heuristic.
        (`isinstance(value, str)` comparisons miss this — ``None`` is not a str.)
    (B) SURVIVAL for the affiliation keys: the parsed value must equal the RAW
        text (plain style) or the de-quoted body (quoted style). A plain value is
        compared against its RAW text precisely so a shortened value fails; this
        is the check that fires on all 8 pre-fix values (len 82 → 48 … 141 → 57).
    (C) quoting integrity: an unescaped inner `"`, an unbalanced `'`, or a
        missing close TERMINATES the value (measured: `"a, "#1" KEEP"` → `'a, '`).
    (D) no silent omission: every declared affiliation value must have been
        measured, or the arm FAILS — a style nobody taught the arm about
        (`|`, `>`, `[...]`, `{...}`) is a failure, not a pass.
    (E) SEQUENCES, item by item: a `sequence` affiliation value is not covered
        by (A) alone, which sees only an item whose text BEGINS with `#`. Every
        item NOT already named by (A) is checked in turn — (C)'s quoting check,
        then a (B)-style value comparison — and a length mismatch fails. Two
        deliberate limits, stated so the sentence is not read as more than it is:
        the item loop STOPS at the key's first defect (one value reports one
        reason rather than a cascade), and an `escaped` item is cleared by (C)
        as far as C reaches and then NOT compared, because the parser has
        already performed the transform `_quoted_style` would have to undo. A
        mid-item truncation (`, #4333 census, #5380`) and a quoted item cut at
        an inner quote are both green under (A)-(D) alone (found in review; the
        population carries 3 affiliation sequences).

    Multi-line plain values and continued sequence items are REFUSED (they are
    legal folding, and a continuation line opening with `#` is silently
    swallowed); a character-level typo inside a quoted value is NOT a truncation
    and stays green — claiming otherwise would be a false claim of coverage.

    Non-vacuity: at the pre-fix tree ``docs/research/2026-11-18-13420-vector-
    placement/dossier.md`` opens its ``aboutSubjects`` value with a backtick, so
    ``parse_frontmatter`` returns ``{}`` and the PARSE arm reds it — on the
    ``continue`` below, before (A)/(B) are ever reached (review: this line
    claimed (A)/(B) caught it, which the control flow cannot do).
    """
    from tortoise.file_indexer import parse_frontmatter

    paths = sorted((ROOT / "docs" / "research").glob("**/*.md"))
    assert paths, "docs/research/ has no markdown files — re-point this guard"
    failures: dict[str, str] = {}
    declared: set[tuple[str, str]] = set()
    measured: set[tuple[str, str]] = set()
    compared = 0
    for path in paths:
        # No block at all is OUTSIDE this arm's declared scope — and, unlike the
        # architecture/ops/evidence populations above, NOTHING else asserts
        # PRESENCE for docs/research/**: 35 of its 103 files carry no block and
        # stay green across this whole module (measured at this commit). An
        # earlier version of this comment said presence was "the OTHER arms'
        # business"; there is no such arm, so that was a false coverage claim and
        # is corrected rather than carried.
        #
        # This arm is about blocks that EXIST and do not parse / do not survive.
        fm = _frontmatter(path)
        if not fm:
            continue
        rel = str(path.relative_to(ROOT))
        parsed = parse_frontmatter(path.read_text(encoding="utf-8"))
        if not parsed:
            failures[rel] = "frontmatter block does not parse (degraded to {})"
            continue
        missing = [key for key in REQUIRED_KEYS if key not in parsed]
        if missing:
            failures[rel] = f"parsed, but drops {missing}"
            continue
        # DECLARED is the PARSED block, not only what `_values` could lex.
        # `_KV` is a lexer for the spellings this population uses (key at column
        # 0, colon immediately after it), but YAML also accepts `aboutObjects :`,
        # `"aboutObjects":` and an indented block — all three parse fine, all
        # three keep the key, all three truncate the value at ` #`, and `_KV`
        # matches NONE of them. Deriving `declared` from `_values` alone left
        # both `declared` and `measured` empty for such a file, so (D) — the arm
        # whose whole job is "no affiliation value escapes measurement" — had
        # nothing to compare and passed a 57-of-141-char value GREEN under the
        # full seven-arm suite (measured: `aboutObjects : <unquoted>`).
        # `parsed` is the authoritative list of declared keys; uniting it here is
        # what makes a spelling the lexer cannot see FAIL rather than vanish.
        declared |= {(rel, k) for k, _, _ in _values(fm) if k in AFFILIATION_KEYS}
        declared |= {(rel, k) for k in AFFILIATION_KEYS if k in parsed}

        bad: list[str] = []
        for key, kind, payload in _values(fm):
            if kind != "other" and key in AFFILIATION_KEYS:
                measured.add((rel, key))
            texts: list[str] = []
            if kind in ("scalar", "scalar_multiline"):
                texts = [str(payload)]
            elif kind in ("sequence", "sequence_multiline"):
                texts = [str(it) for it in payload]  # type: ignore[union-attr]

            # (A) the whole value is a comment — 100% of it is gone.
            #
            # The texts (A) has already diagnosed are remembered, because a
            # 100 %-lost value also resolves to ``None`` and would otherwise
            # reach the non-string branch below — which explains a NON-string as
            # "YAML implicit typing, nothing was lost" and so contradicted (A)
            # inside one failure string (found in review: the `- #11395` item
            # shape, where both messages were printed for one mechanism).
            #
            # EVERY diagnosed text is recorded, not just the first: the message
            # is appended ONCE per key (a list of issue refs can hold several),
            # while the skip has to cover all of them or the second and later
            # items still collect the false clause.
            wholly_comment: set[str] = {
                text for text in texts if text.lstrip().startswith(_WHOLE_VALUE_COMMENT)
            }
            #
            # (A)'s TEXT is kind-aware, because one sentence cannot describe both
            # parse outcomes (found in review): a wholly-commented SCALAR key
            # resolves to `None`, while a wholly-commented sequence ITEM resolves
            # to `null` and leaves the KEY a list holding that null — and in a
            # mixed list a value SURVIVES, so "the key resolves to None" is false
            # for exactly the `- #11395` shape this arm cites as its mutant. The
            # remedy is per-unit too: quoting the VALUE cannot clear an item-level
            # loss (this file's own `_remedy` rule — name the edit that ACTUALLY
            # clears the red); the edit that clears it is quoting the ITEM, which
            # is what `_type_mismatch` already says for the same shape.
            if wholly_comment and kind == "sequence":
                lost = [text for text in texts if text in wholly_comment]
                if len(lost) == 1:
                    head = f"`{key}` sequence item {lost[0]!r} is ENTIRELY a YAML comment"
                    subject = "that item"
                    tail = "Quote the item."
                else:
                    head = (
                        f"`{key}` sequence items "
                        f"{', '.join(repr(text) for text in lost)} are ENTIRELY "
                        "YAML comments"
                    )
                    subject = "each"
                    tail = "Quote the items."
                bad.append(
                    f"{head} — everything after the leading `#` is discarded, so "
                    f"{subject} resolves to null and the key keeps a null element "
                    f"in its place. {tail}"
                )
            elif wholly_comment and kind == "scalar":
                bad.append(
                    f"`{key}` is ENTIRELY a YAML comment — everything after "
                    f"the leading `#` is discarded and the key resolves to "
                    f"None. Quote the value."
                )
            if kind in ("scalar_multiline", "sequence_multiline"):
                # NO (A) string for the multiline kinds (review): a value can
                # SURVIVE past a commented first line — measured, `aboutObjects:
                # #11395` plus an indented continuation parses to
                # 'continuation' — so "100% of it is gone" would be false there,
                # and appending both strings gave ONE value TWO reasons, against
                # this arm's own rule. The MULTI-LINE reason is the true one.
                bad.append(
                    f"`{key}` is a MULTI-LINE value — YAML folds it, and a "
                    "continuation line opening with `#` is silently swallowed; "
                    "quote the whole value instead"
                )
                continue

            if kind == "sequence":
                # A sequence is only PARTLY covered by (A): an item truncated
                # mid-text, or a quoted item cut at an inner quote, keeps its
                # leading character and sails past it. Counting such a value as
                # "measured" while no value-comparing check touches it is the
                # exact fail-open this arm exists to close (found in review: a
                # mid-item `, #4333 census, #5380` tail was green, and the
                # population carries 3 affiliation sequences).
                if key not in AFFILIATION_KEYS:
                    continue
                got_list = parsed.get(key)
                if not isinstance(got_list, list) or len(got_list) != len(texts):
                    bad.append(
                        f"`{key}` does not SURVIVE the parse: {len(texts)} item(s) "
                        f"{texts!r} became {got_list!r}"
                    )
                    continue
                # `strict=True` (#13452 + this branch added it independently, for
                # the same reason): the length guard above already `continue`d when the
                # lists differ, so an unequal pair here is unreachable — and if that
                # guard is ever weakened, this must RAISE rather than silently compare
                # a prefix (the fail-open this arm exists to close). It also gives
                # ruff B905 its explicit argument (#13492 merge, inherited red on
                # `main`).
                for raw_item, got_item in zip(texts, got_list, strict=True):
                    if raw_item in wholly_comment:
                        # (A) already named this item's loss; a second reason from
                        # _type_mismatch would contradict it in the same string.
                        continue
                    style, expected = _quoted_style(raw_item)
                    if style in ("broken", "malformed"):
                        bad.append(
                            _unkeepable_quoted(f"`{key}` item {raw_item!r}", style)
                        )
                        break
                    if style == "escaped":
                        continue
                    if expected is None:
                        expected = raw_item
                    compared += 1
                    if not isinstance(got_item, str):
                        bad.append(_type_mismatch(key, expected, got_item, "item"))
                        break
                    if got_item != expected:
                        bad.append(
                            f"`{key}` item {raw_item!r} (len {len(expected)}) "
                            f"became {got_item!r} (len {len(got_item)}) — a plain "
                            "sequence item on one line is not folded, so this "
                            "is a truncation. Quote the item."
                        )
                        break
                continue

            if kind != "scalar":
                continue

            # (B)/(C) are the affiliation's own instruments: a value the
            # affiliation carries is the one this issue is about.
            style, expected = _quoted_style(texts[0])
            if style in ("broken", "malformed"):
                bad.append(_unkeepable_quoted(f"`{key}`", style))
                continue
            if key not in AFFILIATION_KEYS:
                continue
            if style == "escaped":
                continue  # declined: (C) already cleared it, (B) cannot de-quote
            if expected is None:
                expected = texts[0]  # plain: YAML performs no transform
            got = parsed.get(key)
            if not isinstance(got, str):
                if texts[0] in wholly_comment:
                    continue  # (A) already named the 100 % loss; no second reason
                bad.append(_type_mismatch(key, expected, got, "value"))
                continue
            compared += 1
            if got != expected:
                bad.append(
                    f"`{key}` does not SURVIVE the parse: raw "
                    f"{expected!r} (len {len(expected)}) became "
                    f"{got!r} (len {len(got)})"
                )
        if bad:
            failures[rel] = "; ".join(bad)

    # (D) NO SILENT OMISSION: every declared affiliation value must have been
    # measured, unless it is an explicit per-value entry above. A style or a
    # block that silently escapes measurement is the defect this arm exists for,
    # so it fails here rather than passing quietly.
    #
    # The REMEDY has to be the one that actually clears the red (review): this arm
    # now reaches a value `_values` could not lex at all (it is declared from the
    # PARSED block too), and for that pair "quote the value" is advice that cannot
    # work — an already fully-quoted value is still unlexable while the KEY is
    # spelled `key :`, as a quoted key, or inside an indented block. Measured:
    # `aboutObjects : "a, #11395 KEEP, #13220"` parses INTACT and reds here, so
    # the message names the respelling instead of advice that does nothing.
    unmeasured = sorted(declared - measured - UNMEASURED)

    def _remedy(rel: str, key: str) -> str:
        """Name the edit that ACTUALLY clears this red, not the one that sounds right.

        (D) reaches a pair in two very different situations, and naming only one
        of them is how a message tells a reader to do something that cannot work
        (found in review, twice):

        * the value's STYLE escapes every check (a block scalar, a flow
          collection) — quoting the value clears it;
        * the KEY is spelled so `_KV` cannot lex it — quoting the value changes
          NOTHING, because the pair is unmeasurable while the key is unlexable.

        Three key spellings do that, and each needs its OWN remedy. The third is
        the surprising one: a frontmatter block indented off column 0 is legal
        YAML (the root node may be indented) and it PARSES, so it reaches (D)
        rather than the parse arm — measured: uniformly indenting every key of
        `dossier.md`'s block still returns all 10 keys from
        `parse_frontmatter`. Only dedenting the WHOLE block clears it; dedenting
        the one key instead produces mixed indentation, which is a parse error
        with a less useful message.
        """
        block = _frontmatter(ROOT / rel)
        if key in {k for k, _, _ in _values(block)}:
            return (
                f"`{rel}`: `{key}` quote the value (plain or simply-quoted is "
                "measured)"
            )
        if any(re.match(rf"^[ \t]+{re.escape(key)}[ \t]*:", line) for line in block):
            return (
                f"`{rel}`: `{key}` is declared inside an INDENTED frontmatter "
                "block — YAML accepts an indented root, so the block parses and "
                "this pair reaches no check; quoting the value will NOT clear "
                "this. Dedent the whole block to column 0 (dedenting only this "
                f"key makes the indentation mixed, which is a parse error) and "
                f"spell the key `{key}:`"
            )
        if any(re.match(rf"^{re.escape(key)}[ \t]+:", line) for line in block):
            return (
                f"`{rel}`: `{key}` has a SPACE before its colon — legal YAML, "
                "which this arm's lexer does not accept, and quoting the value "
                f"will NOT clear this. Respell the key as `{key}:`, no space "
                "before the colon"
            )
        if any(re.match(rf"^[\"']{re.escape(key)}[\"']", line) for line in block):
            return (
                f"`{rel}`: `{key}` is a QUOTED key — legal YAML, which this "
                "arm's lexer does not accept, and quoting the value will NOT "
                f"clear this. Unquote the key: write `{key}:`"
            )
        return (
            f"`{rel}`: `{key}` quote the value (plain or simply-quoted is "
            "measured)"
        )

    assert not unmeasured, (
        "these affiliation values were NOT measured by ANY check — a silently "
        "unmeasured affiliation is the #13452 defect: "
        + "; ".join(_remedy(rel, key) for rel, key in unmeasured)
        + ". If the style is deliberate, add the (path, key) pair to UNMEASURED "
        "with a reason."
    )
    assert measured, "measured no affiliation value at all — re-point this guard"
    assert compared, "compared no value at all — re-point this guard"
    assert not failures, (
        "docs/research/**/*.md frontmatter is unparseable or does not SURVIVE "
        f"the parse ({len(measured)} affiliation values measured, {compared} "
        f"compared): {failures}"
    )


#: A frontmatter block the research arm accepts, so a test that injects it can
#: isolate the ONE affiliation shape under test (used by the diagnosis test below).
_FM_HEAD = (
    "---\ntype: research\ndomain: research\ndoc_status: draft\n"
    "created: 2026-11-18\n"
)


def test_a_wholly_commented_value_gets_ONE_TRUE_diagnosis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#13452 review (2 × P2): the surviving (A) diagnosis must be TRUE, and a
    committed assertion must notice if it stops being.

    Both halves were review findings against this arm, and both are pinned here
    because neither was reachable by any existing row:

    * the fix that made a 100 %-lost value report ONE reason was **verdict
      neutral** — (A)'s own string is what keeps the file in ``failures``, so
      deleting the skip reddened no row and the only evidence it worked was a
      by-hand paste in the PR body (and the same defect had already regressed
      once inside the PR, found by a reviewer reading prose, not by a test);
    * the string it left standing was **kind-blind**: at the ``- #11395`` mutant
      it said "the key resolves to None" about a key that resolves to ``[None]``
      and told the author to "Quote the value", an edit that cannot clear an
      item-level loss.

    So each case below asserts the diagnosis COUNT and its CONTENT. Non-vacuity:
    at the pre-fix head (``24fbd46102``) case (a)/(b)/(c) carry the false "the key
    resolves to None" clause and the ``Quote the value`` remedy, and case (d) —
    the multiline shape, where a value can SURVIVE a commented first line —
    carries TWO reasons for one value; every assertion here reds on that tree.

    The docs tree is a ``tmp_path`` one (the arm resolves ``ROOT/docs/research``
    through the module constant, so it is monkeypatched), and each run carries a
    control file whose value IS compared: otherwise ``assert compared`` fires
    before ``assert not failures`` and masks the diagnosis under test.
    """

    def red(body: str) -> str:
        """Run the arm over a one-case tmp docs tree and return its failure text."""
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        (root / "docs" / "research").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "research" / "control.md").write_text(
            _FM_HEAD + "aboutObjects: tortoise-platform, tortoise-hosted\n---\n\n# c\n",
            encoding="utf-8",
        )
        (root / "docs" / "research" / "case.md").write_text(
            _FM_HEAD + body + "---\n\n# case\n", encoding="utf-8"
        )
        monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
        with pytest.raises(AssertionError) as excinfo:
            test_research_doc_frontmatter_parses_as_yaml()
        # pytest appends its own rewritten-assert explanation to the exception
        # text, and that explanation REPEATS the failures dict — so take the
        # arm's own message (always one line) or every count here doubles.
        return str(excinfo.value).split("\n", 1)[0]

    def diagnoses(msg: str) -> int:
        """Every reason this arm emits starts by naming the key."""
        return msg.count("`aboutObjects`")

    # (a) ONE wholly-commented item: the ITEM is null, the KEY is `[None]`.
    msg = red("aboutObjects:\n  - #11395\n")
    assert diagnoses(msg) == 1, msg
    assert "resolves to null" in msg, msg
    assert "Quote the item." in msg, msg
    assert "the key resolves to None" not in msg, msg

    # (b) a MIXED list: two items are wholly comments and one value SURVIVES, so
    # "the key is ENTIRELY a comment / resolves to None" is doubly false here.
    msg = red(
        "aboutObjects:\n  - #11395\n  - tortoise-hosted-platform\n"
        "  - #13082 KEEP ruling\n"
    )
    assert diagnoses(msg) == 1, msg
    assert "tortoise-hosted-platform" not in msg, msg  # the survivor is not a defect
    assert "the key resolves to None" not in msg, msg
    assert "Quote the items." in msg, msg

    # (c) a comment item followed by a GENUINELY truncated one: the skip must not
    # be too wide — both true diagnoses survive, (A)'s and the truncation's.
    msg = red(
        "aboutObjects:\n  - #11395\n"
        "  - tortoise-hosted-platform, #4333 census, #5380\n"
    )
    assert diagnoses(msg) == 2, msg
    assert "Quote the item." in msg, msg
    assert "(len 45)" in msg and "(len 25)" in msg, msg
    assert "a plain sequence item on one line is not folded" in msg, msg

    # (d) a MULTILINE value whose first line is a comment: a continuation makes a
    # value SURVIVE, so (A)'s "100% of it is gone" cannot be claimed, and one
    # value must not collect two reasons.
    msg = red("aboutObjects: #11395\n  continuation\n")
    assert diagnoses(msg) == 1, msg
    assert "MULTI-LINE" in msg, msg
    assert "the key resolves to None" not in msg, msg

    # (e) a wholly-commented SCALAR — the one shape where the original sentence
    # is true, so its wording and remedy are pinned rather than dropped.
    msg = red("aboutObjects: #11395\n")
    assert diagnoses(msg) == 1, msg
    assert "the key resolves to None" in msg, msg
    assert "Quote the value." in msg, msg


def test_a_single_quoted_escape_before_a_hash_is_measured_not_broken(tmp_path) -> None:
    """YAML's only single-quote escape is ``''``; it is NOT an unbalanced quote.

    ``'the ''KEEP'' #11395 ruling'`` is legal, intact YAML — ``parse_frontmatter``
    returns ``the 'KEEP' #11395 ruling`` — but the ``''`` escape leaves a quote
    immediately before the `` #``, so a close located by
    ``raw[:i].rstrip().endswith(q)`` fires at the WRONG `` #`` and (C) reports
    *an unbalanced quote … remove the inner quote*: advice to delete a legal
    escape. Nothing can ship on this — the arm is fail-closed — but a false
    remedy is the same defect rounds 3 and 4 fixed in (D) and the sequence
    branch, so it is fixed where it lives and pinned here.

    Non-vacuity: on the pre-fix helper ``_quoted_style`` returns
    ``('broken', None)`` for this value, so the ``style`` assertion below fails
    (the ``classified`` assertion before it passes: this branch edited
    ``_values``' TEXT only — two prose blocks, a DOCSTRING hunk and a ``#``-comment
    hunk, with behaviour identical and the classification path untouched, so that
    assertion does not depend on the fix. ⛔ It read "``_values`` is unchanged by
    this fix" until review round 5, which the diff refutes; round 6 then caught the
    first repair calling BOTH hunks "comments" when ``@@ -166`` is inside the
    docstring that opens at ``:158`` — so the claim is about the PATH, and the two
    hunks are text, not both comments).

    This value pins the close-POSITION half of the fix. The parity half — the
    ``''`` skip in ``_closing_quote`` — is NOT exercised by it: here the escape
    sits immediately before the `` #``, so the position test alone refuses the
    wrong split. It has its own pin in
    ``test_a_single_quoted_escape_mid_body_under_a_trailing_comment_is_measured``
    (found in review: deleting the parity skip left this test green).
    """
    from tortoise.file_indexer import parse_frontmatter

    value = "'the ''KEEP'' #11395 ruling'"
    doc = tmp_path / "dossier.md"
    doc.write_text(
        f"---\ntitle: x\naboutObjects: {value}\n---\n\nbody\n", encoding="utf-8"
    )
    classified = {(key, kind): payload for key, kind, payload in _values(_frontmatter(doc))}
    assert ("aboutObjects", "scalar") in classified, (
        "`_values` no longer classifies this as a scalar — re-point this guard: "
        f"{classified!r}"
    )
    style, expected = _quoted_style(classified[("aboutObjects", "scalar")])
    assert style == "single-quoted", (
        f"a `''` escape before a ` #` is legal YAML, but this arm reports "
        f"{style!r} — the remedy it prints must not ask for a legal escape to "
        f"be deleted"
    )
    assert expected == "the 'KEEP' #11395 ruling", expected
    # …and the measured expectation is the value the real consumer parses, so
    # the arm is not merely self-consistent.
    assert parse_frontmatter(doc.read_text(encoding="utf-8"))["aboutObjects"] == expected


def test_a_single_quoted_escape_mid_body_under_a_trailing_comment_is_measured(
    tmp_path,
) -> None:
    """The ``''`` PARITY skip, pinned separately from the close-position test.

    ``'it''s' # comment`` is legal YAML (``parse_frontmatter`` returns ``it's``)
    and the ``''`` sits MID-body, so the close-POSITION test (``len(raw[:i]
    .rstrip()) - 1 == close``) cannot decide it — only ``_closing_quote``'s skip
    of a doubled quote can say where the scalar really ends. Without that skip
    the close is found INSIDE the body (index 3, not 6), so NO `` #`` in this
    value can satisfy the split predicate: ``_split_comment`` returns the raw
    text unstripped, and because that text no longer ends in a quote the arm
    reports ``('malformed', None)`` — the same *delete a legal escape* class of
    false remedy this whole change removes. The mechanism is worth stating
    exactly, because it is NOT the pre-fix helper's: there the split FIRED at a
    wrong `` #``, here it does not fire at all, which is also why the
    close-position test alone stays green under this mutant (found in review:
    the earlier wording claimed the split landed on the wrong `` #``, and the
    measurement above refutes it).

    Why it needs its own test: measured with the skip deleted, the sibling
    single-quote test stays GREEN (its escape sits immediately before the
    `` #``), so the parity half shipped unpinned. ⛔ NOT *"would reintroduce the
    false block with green CI"* — an earlier version of this paragraph said that,
    and it is falsified by measurement: with the skip deleted the file is
    `5 failed, 13 passed` at `dad7aea959`, i.e. this pin AND
    `test_a_never_closing_quoted_scalar_is_malformed_not_escaped` AND
    `test_no_GREEN_quoted_verdict_survives_a_parser_refusal` all red (the other 2
    are the pre-existing absence rows). A future simplification therefore CANNOT
    land green. This test is the FIRST custodian of the parity skip, not its only
    one — the same correction the backslash-skip docstring below already carries.

    Non-vacuity: with the ``''`` skip removed, ``_quoted_style`` returns
    ``('malformed', None)`` here and the ``style`` assertion below fails.
    """
    from tortoise.file_indexer import parse_frontmatter

    value = "'it''s' # comment"
    doc = tmp_path / "dossier.md"
    doc.write_text(
        f"---\ntitle: x\naboutObjects: {value}\n---\n\nbody\n", encoding="utf-8"
    )
    classified = {(key, kind): payload for key, kind, payload in _values(_frontmatter(doc))}
    assert ("aboutObjects", "scalar") in classified, (
        "`_values` no longer classifies this as a scalar — re-point this guard: "
        f"{classified!r}"
    )
    style, expected = _quoted_style(classified[("aboutObjects", "scalar")])
    assert style == "single-quoted", (
        "a `''` escape MID-body is legal YAML, but this arm reports "
        f"{style!r} — the parity scan lost the close and the printed remedy "
        "would ask for a legal escape to be deleted"
    )
    assert expected == "it's", expected
    assert parse_frontmatter(doc.read_text(encoding="utf-8"))["aboutObjects"] == expected


def test_a_double_quoted_backslash_escape_before_a_comment_is_measured(
    tmp_path,
) -> None:
    """The ``"`` BACKSLASH skip, pinned separately from the ``''`` parity skip.

    ``"a\"" # c`` is legal YAML — ``parse_frontmatter`` returns ``a"`` — and the close
    sits immediately before the `` #`` (the escaped quote is at index 3, the true
    close at 4). Without the skip the close is found ON that escaped quote (index
    3), so NO `` #`` satisfies the split predicate: ``_split_comment`` returns the
    raw text unstripped, that text no longer ends in a quote, and the arm reports
    ``('malformed', None)`` — the *remove the inner quote* class of false remedy
    this whole change removes. The mechanism is stated exactly because it is NOT
    a mis-landed split: no split fires at all (measured; the same distinction the
    sibling parity test records).

    This is the one arm the PRE-fix helper already got right (it never scanned
    for a close at all — it cut at the `` #`` whose left side ended in the quote
    char, which this shape satisfies), so the skip is old behaviour preserved by
    a NEW branch. When it goes, this test is NOT the only one that reds:
    ``test_a_never_closing_quoted_scalar_is_malformed_not_escaped`` also asserts
    ``_closing_quote('"a\\"') == -1``, and that is exactly what the same skip
    produces, so it reds alongside this one (re-measured at ``1c956076``:
    ``4 failed, 12 passed`` — the 2 pre-existing absence reds plus those 2).

    Non-vacuity: deleting the two-line backslash skip in ``_closing_quote``
    makes ``_quoted_style`` return ``('malformed', None)`` here, so the ``style``
    assertion below is the FIRST of two things that notice, not the only one —
    ``test_a_never_closing_quoted_scalar_is_malformed_not_escaped`` reds
    alongside it, which the paragraph above already measures. (The PAIR count
    above is tied to the head that measured it; §7 item 2 carries the file-wide
    baseline, and any count printed here goes stale the moment another test is
    appended to this file — which has already happened twice.)
    """
    from tortoise.file_indexer import parse_frontmatter

    value = '"a\\"" # c'
    doc = tmp_path / "dossier.md"
    doc.write_text(
        f"---\ntitle: x\naboutObjects: {value}\n---\n\nbody\n", encoding="utf-8"
    )
    classified = {(key, kind): payload for key, kind, payload in _values(_frontmatter(doc))}
    assert ("aboutObjects", "scalar") in classified, (
        "`_values` no longer classifies this as a scalar — re-point this guard: "
        f"{classified!r}"
    )
    style, expected = _quoted_style(classified[("aboutObjects", "scalar")])
    assert style == "escaped", (
        "a backslash-escaped quote before a trailing comment is legal YAML, but "
        f"this arm reports {style!r} — the close scan skipped no escape and the "
        "printed remedy would ask for a legal escape to be deleted"
    )
    assert expected is None, (
        "an `escaped` body declines the value comparison by design (hand "
        f"de-quoting cannot reproduce `\\t`/`\\uXXXX`) — got {expected!r}"
    )
    # …and the consumer agrees the value is whole, which is what `malformed` denies.
    assert parse_frontmatter(doc.read_text(encoding="utf-8"))["aboutObjects"] == 'a"'


def test_an_unescaped_quote_is_broken_even_beside_a_legal_escape() -> None:
    """(C) runs BEFORE the escape branch — and that ORDER was the only thing pinning it.

    ``_quoted_style`` returns ``broken`` for an UNESCAPED inner ``"`` and only then
    considers ``escaped``. That order is load-bearing, not cosmetic: an escaped body
    can still carry an unescaped quote, and when the escape branch ran first the
    truncation went through GREEN (found in review of #13476; the module docstring at
    ``_quoted_style`` records it).

    ⛔ This is the one branch of this helper whose loss is a FAIL-OPEN, not a false
    remedy. Measured, with ``if _has_unescaped_dq(body):`` mutated to ``if False:``,
    THREE pins red: THIS one, ``test_a_malformed_value_names_a_true_remedy`` (its
    case (c) asserts ``"broken" in msg`` for a shape whose verdict this order
    decides) and ``test_no_GREEN_quoted_verdict_survives_a_parser_refusal`` (its
    ``refused_but_green`` assertion fires on the same mutant — measured, NOT its
    ``mismatched`` one: with the ordering gone, a shape the parser REFUSES comes
    back with a COMPARING verdict, which is that pin's first direction) —
    ``5 failed, 13 passed`` of
    18 collected at ``fcf5d9cd72``, the other 2 being the pre-existing absence rows
    that red identically on ``origin/main``. Named as well as totalled because an
    earlier revision of this paragraph claimed "one other pin" and a
    ``4 failed, 13 passed`` total, which is arithmetically impossible for an
    18-item file and omitted the third pin. These two values, both TRUNCATED by the
    consumer, stop being reported:

        `"a, "#1" KEEP"`            yaml -> `'a, '`        head `('broken', None)` -> `('double-quoted', …)`
        `"a\\tb\\" "#1" KEEP"`      yaml -> `'a\\tb" '`     head `('broken', None)` -> `('escaped', None)`

    ⛔ Only the SECOND of those is a fail-open, and the difference has to be stated
    because the first shape still reds — with a different message. Under the mutant
    `"a, "#1" KEEP"` is measured as `('double-quoted', 'a, "#1" KEEP')`, so the scalar
    branch still COMPARES it and still reports *does not SURVIVE the parse* (`len 12 →
    3`) where it used to say TERMINATED: the verdict changes, the comparison does not
    disappear. The second shape carries a legal backslash escape, so it reaches
    `('escaped', None)` and the `escaped` branch `continue`s BEFORE any comparison —
    THAT is the value which escapes measurement, and end-to-end a `docs/research`
    dossier carrying that value passes the arm under the mutant and reds it at this
    head, which is exactly the #13452 defect this guard exists to prevent. At the
    head this was written the two assertions below were the ONLY thing that noticed;
    they no longer are — `test_a_malformed_value_names_a_true_remedy` and
    `test_no_GREEN_quoted_verdict_survives_a_parser_refusal` also red under that
    mutant now (re-measured at `fcf5d9cd72`: `5 failed, 13 passed`,
    `test_a_malformed_value_names_a_true_remedy`'s case (c) asserting
    `"broken" in msg`), so this test is the first custodian of the
    ordering, not the sole one.

    Before this pin existed the same mutant left the WHOLE FILE green apart from the two
    pre-existing absence rows — measured at ``3b8de63b8a``, when the file held one test
    fewer — which is exactly why this test exists. That measurement is quoted WITH its
    head, and so is every count in this docstring: a count without one has gone stale
    twice already, because it moves whenever another test is appended.
    """
    # An unescaped inner quote, with no escape anywhere in the body.
    assert _quoted_style('"a, "#1" KEEP"') == ("broken", None), (
        "an unescaped inner quote TERMINATES the scalar (YAML reads `a, `), so this "
        "must be `broken` — not measured as a whole double-quoted value"
    )
    # …and the SAME shape once a legal escape is also present: the escape branch must
    # not be reached first, or `escaped` declines the comparison and the truncation
    # goes unreported.
    assert _quoted_style('"a\\tb\\" "#1" KEEP"') == ("broken", None), (
        "a body with BOTH an escape and an unescaped quote is still TERMINATED at "
        "that quote (YAML reads `a\\tb\" `) — `escaped` would skip the comparison "
        "and let the truncation through"
    )


def test_a_never_closing_quoted_scalar_is_malformed_not_escaped() -> None:
    """A quote the parity scan never closes is `malformed` — never `escaped`.

    Wholeness used to be decided by ``value.endswith(q)`` alone, and that test is
    FOOLED by a double-quoted body whose last character is an ESCAPED quote: the
    value ends in the quote char, so it passed the wholeness test, reached the
    escape branch and was reported ``('escaped', None)`` — a style that DECLINES the
    value comparison. A scalar YAML cannot close therefore escaped measurement
    while (D) still counted it as measured (found in review; the false statement
    this falsified was `_split_comment`'s `-1` arm, which claimed the caller
    refused these shapes and did not).

    YAML agrees the shape is unparseable, not merely uncomparable: a quoted scalar
    requires a MATCHING closing quote, and PyYAML raises *while scanning a quoted
    scalar, found unexpected end of stream*. So the verdict is `malformed` — the
    same one the helper already gave the plainly truncated shape.

    Non-vacuity: replaying the WHOLE pre-fix helper — ``value.endswith(q)``
    alone, *and* ``_split_comment``'s ``raw[:i].rstrip().endswith(q)`` predicate
    in place of the ``_closing_quote`` gate — the FIRST entry below fails:
    ``_quoted_style`` returned ``('escaped', None)`` for it, while the other two
    returned ``('broken', None)`` and ``('malformed', None)``. TWO other pins red
    on that replay, and both are named because this file must not claim otherwise:
    ``test_a_single_quoted_escape_before_a_hash_is_measured_not_broken``
    (``('broken', None)``; its own docstring owns that measurement) and
    ``test_no_GREEN_quoted_verdict_survives_a_parser_refusal`` — ``5 failed, 13
    passed`` of 18 at ``fcf5d9cd72``, the other 2 being the pre-existing absence
    reds. An earlier revision said "exactly ONE other pin" and quoted the
    ``4 failed, 12 passed`` measured at ``1c956076``: true there, stale here,
    because the class pin below landed AFTER that head.
    The truncation pins stay GREEN under it: those shapes DO close at an inner
    quote, so ``_closing_quote`` finds the close and the wholeness test never
    changes their verdict.
    """
    from tortoise.file_indexer import parse_frontmatter

    # `_closing_quote` returns -1 for every one of these; the FIRST is the escape
    # from measurement, because it is the only one that still ENDS in the quote
    # char while having no terminating quote.
    for raw in ('"a\\"', "'''", '"abc\\'):
        assert _closing_quote(raw) == -1, (
            f"{raw!r} is expected to have NO closing quote — re-point this guard"
        )
        assert _quoted_style(raw) == ("malformed", None), (
            f"{raw!r} never closes, so YAML keeps none of it — `escaped` would "
            f"decline the comparison and let it through; got {_quoted_style(raw)!r}"
        )

    # …and the consumer agrees, which is what makes this classification safe to
    # tighten: the PARSE arm (`parse_frontmatter` degrading to `{}`) is the check
    # that reds such a file, so narrowing `escaped` invents no red the pipeline
    # lacks.
    assert parse_frontmatter('---\ntitle: x\naboutObjects: "a\\"\n---\n') == {}


def test_the_e1_e2_evidence_bundle_declares_its_affiliation() -> None:
    """#9110 review P2: the evidence bundle landed with no frontmatter at all.

    ``docs/evidence/7950-capstone/README.md`` — the bundle this one copies — carries
    the full document-conventions block, and AGENTS.md's Documentation Filing
    Protocol requires it of every ``docs/`` file. The epic/issue affiliation is
    exactly the metadata an evidence artifact exists to carry.

    Non-vacuity: at the pre-fix tree ``E1-E2.md`` opens at ``# E1 + E2 on one run``,
    so this reds on it.
    """
    bundle = ROOT / "docs/evidence/7949-capstone/E1-E2.md"
    assert bundle.exists(), "the E1+E2 evidence bundle moved — re-point this guard"
    missing = _missing(bundle)
    assert not missing, (
        f"docs/evidence/7949-capstone/E1-E2.md must declare {missing} — the "
        "AGENTS.md doc-filing rule and the convention of the bundle it copies."
    )


def test_a_malformed_value_names_a_true_remedy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`malformed` and `broken` no longer share a remedy — one of them was false.

    Both verdicts refuse the file, which is correct: nothing can ship on either.
    What was wrong was the PRINTED REMEDY, which was the truncation sentence for
    both — *"the value is TERMINATED there. Remove the inner quote"*. For
    `broken` that is true (YAML stops at the unescaped inner quote). For
    `malformed` it is false for the members YAML KEEPS WHOLE — a space-less
    trailing comment and a continued quoted scalar — where the sentence tells the
    author to delete a character the value needs: the same false-remedy class as
    the `''`-escape finding this PR exists to remove. The third member (a scalar
    that never closes) is a PARSE ERROR, so the sentence was merely inapplicable
    there; it gets the same new remedy for a different reason.

    The whole family is recorded in the plan doc §9 as *recorded, not fixed*,
    with one reason: clearing the VERDICT means a lexer widening (accepting a
    trailing comment that is not space-separated). The remedy is a different
    fix and needs no lexer change: name the cause the author can act on. That is
    what this test pins, and it pins the `broken` sentence too, so a future
    simplification cannot flatten the two back into one.

    Non-vacuity: on the pre-fix arm every ``"Remove the inner quote" not in msg``
    assertion below fails, because both verdicts carried that sentence.
    """

    def red(body: str) -> str:
        """Run the arm over a one-case tmp docs tree and return its failure text."""
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        (root / "docs" / "research").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "research" / "control.md").write_text(
            _FM_HEAD + "aboutObjects: tortoise-platform, tortoise-hosted\n---\n\n# c\n",
            encoding="utf-8",
        )
        (root / "docs" / "research" / "case.md").write_text(
            _FM_HEAD + body + "---\n\n# case\n", encoding="utf-8"
        )
        monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
        with pytest.raises(AssertionError) as excinfo:
            test_research_doc_frontmatter_parses_as_yaml()
        # The rewritten-assert explanation repeats the failures dict, so take the
        # arm's own single-line message.
        return str(excinfo.value).split("\n", 1)[0]

    from tortoise.file_indexer import parse_frontmatter

    # (a) LEGAL YAML refused as `malformed`: a trailing comment with no space
    # before it. YAML keeps `x`, so "remove the inner quote" is advice to delete
    # a character the value needs; the remedy that clears it is the SPACE.
    assert parse_frontmatter(
        '---\ntitle: x\naboutObjects: "x"# note\n---\n'
    )["aboutObjects"] == "x", "control: `\"x\"# note` must load whole"
    msg = red('aboutObjects: "x"# note\n')
    assert "malformed" in msg, msg
    assert "Remove the inner quote" not in msg, msg
    assert "put one space before the `#`" in msg, msg
    # The OPENING clause must not contradict the case it is printing for. This
    # shape HAS a closing quote — measured just below, not assumed — and the arm
    # FINDS it, so BOTH "no closing quote on that line" and its replacement "this
    # arm cannot read where the value closes" were false in the very sentence that
    # went on to name the space fix. The second one survived a round that fixed the
    # first, which is why it is pinned by ABSENCE: a corrected clause is not a
    # corrected claim, and the "Remove the inner quote" / "put one space"
    # assertions above are both satisfied by a message whose first clause lies.
    assert _closing_quote(_split_comment('"x"# note')) == 2, (
        "re-point this pin: the close is located at index 2, and that measurement "
        "is what makes the two clauses below false"
    )
    assert "no closing quote on that line" not in msg, msg
    assert "cannot read where the value closes" not in msg, msg
    # …and the TRUE cause is named: the close is located, but it is not the last
    # thing in the value, so the arm refuses to compare it.
    assert "not the last thing in the value" in msg, msg

    # (b) LEGAL YAML refused as `malformed`: a quoted scalar continued on the
    # next line. The remedy names the one-line form and NOTHING that cannot clear
    # the red: a block scalar reaches no check (`_values`' `other` kind), so it
    # would swap this red for (D)'s "not measured" red — pinned below.
    assert parse_frontmatter(
        '---\ntitle: x\naboutObjects: "a\n  b"\n---\n'
    )["aboutObjects"] == "a b", "control: a continued quoted scalar loads whole"
    msg = red('aboutObjects: "a\n  b"\n')
    assert "malformed" in msg, msg
    assert "Remove the inner quote" not in msg, msg
    assert "one line inside its quotes" in msg, msg
    # A remedy that cannot clear the red is the defect class this guard removes, so
    # both arms are pinned against the one that LOOKS right: a block scalar escapes
    # (C)/(B) but reaches no check, and (D) then asks for a quoted value.
    assert "block scalar" not in msg, msg

    # (c) the truncation verdict is UNCHANGED and keeps its own sentence: here
    # YAML really does stop at the inner quote, so the deletion advice is right.
    msg = red('aboutObjects: "a, "#1" KEEP"\n')
    assert "broken" in msg, msg
    assert "TERMINATED at an inner quote" in msg, msg
    assert "Remove the inner quote" in msg, msg
    assert "put one space before the `#`" not in msg, msg
    assert "block scalar" not in msg, msg  # cannot clear: see (b)'s note above


def test_an_unlexable_key_is_what_makes_the_fail_open_a_RED(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(D)'s ONE fail-open, pinned — the branch was recorded and left untested.

    The plan doc §9 calls this line *"a FAIL-OPEN in check (D) … the exact
    #13452 defect the guard exists to close, reachable with a one-keystroke
    typo"* and then records it as fixed with no pin. It was the only fix in this
    PR whose revert left the whole file GREEN.

    ``declared`` is the authoritative list of keys the guard owes a measurement
    for. ``_values`` can only lex ``key:``, so a legal-but-unlexable key
    spelling (a space before the colon, a quoted key, an indented block) is
    invisible to it; unioning in the PARSED block is what makes that pair FAIL
    instead of vanish. Without it the pair is in neither `declared` nor
    `measured`, (D) has nothing to complain about, and a value YAML truncates
    from 14 characters to 2 sails through green.

    Non-vacuity, measured at `38df4e93a4`: deleting
    ``declared |= {(rel, k) for k in AFFILIATION_KEYS if k in parsed}`` takes the
    file from `2 failed, 15 passed` to ``3 failed, 14 passed`` — the two
    pre-existing absence reds AND this pin, while the arm goes GREEN on the case
    below. That is why the pin exists rather than a comment. (Before this pin
    existed, the same deletion left the file at `2 failed, 14 passed` — "the
    absence reds and NOTHING else". That figure is superseded by this test's own
    arrival: quoting it here would say the deletion is unnoticed by a pin added
    precisely to notice it.)
    """

    def red(body: str) -> str:
        """Run the arm over a one-case tmp docs tree and return its failure text."""
        root = tmp_path / str(len(list(tmp_path.iterdir())))
        (root / "docs" / "research").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "research" / "control.md").write_text(
            _FM_HEAD + "aboutObjects: tortoise-platform, tortoise-hosted\n---\n\n# c\n",
            encoding="utf-8",
        )
        (root / "docs" / "research" / "case.md").write_text(
            _FM_HEAD + body + "---\n\n# case\n", encoding="utf-8"
        )
        monkeypatch.setattr(sys.modules[__name__], "ROOT", root)
        with pytest.raises(AssertionError) as excinfo:
            test_research_doc_frontmatter_parses_as_yaml()
        return str(excinfo.value).split("\n", 1)[0]

    from tortoise.file_indexer import parse_frontmatter

    # (a) SPACE before the colon. The shape is a REAL truncation — YAML ends the
    # plain scalar at the ` #` — so a green arm here is a shipped defect, not a
    # false block. Control first, so the test cannot pass by refusing a value
    # that was never broken.
    assert parse_frontmatter(
        "---\ntitle: x\naboutObjects : a, #11395 KEEP\n---\n"
    )["aboutObjects"] == "a,", "control: ` #` truncates this plain scalar"
    msg = red("aboutObjects : a, #11395 KEEP\n")
    assert "has a SPACE before its colon" in msg, msg
    assert "quote the value" not in msg.split("Respell")[0], msg

    # (b) a QUOTED key: also legal YAML, also invisible to `_values`, and it
    # needs its OWN remedy — "quote the value" cannot clear it.
    assert parse_frontmatter(
        "---\ntitle: x\n\"aboutObjects\": a, #11395 KEEP\n---\n"
    )["aboutObjects"] == "a,", "control: ` #` truncates this plain scalar too"
    msg = red('"aboutObjects": a, #11395 KEEP\n')
    assert "QUOTED key" in msg, msg


#: The alphabet every quoting arm in this file turns on. The sweep below is
#: EXHAUSTIVE over it, so the pin is a CLASS rather than one more hand-picked
#: shape — this change has cleared one shape per review round, and each round the
#: NEXT shape was what red.
_SHAPE_ALPHABET = ("'", '"', "\\", "#", " ", "a")
#: Length bound, stated rather than discovered. At 6 the sweep starts finding FIVE
#: PRE-EXISTING discrepancies this change neither causes nor clears — all five
#: reproduce IDENTICALLY through `origin/main`'s `_split_comment` + `_quoted_style`
#: (measured shape by shape). ⛔ That pairing is named precisely: `_closing_quote`
#: does NOT exist on `main` — it is introduced by THIS branch — so a reader who
#: greps main for it finds nothing and concludes the replay was fabricated. What
#: is replayed is main's SCALAR VERDICT path, which is where these five are
#: decided. The exclusion is about ownership, not severity.
#: ⛔ They are NOT all the same kind, and naming only the harmless one would tell a
#: lane that raises this bound that 5→6 is safe for a reason it is not:
#:
#: * ONE is a mis-slice — `''''#'` is loadable YAML (`'`) and the arm's body is
#:   `''#`, because `body = value[1:-1]` is not sliced at the close the parity scan
#:   already located. False red → it fails CLOSED. It is fixable (`body = value[1:close]`
#:   clears it and adds 0 wrong bodies on YAML-accepted input) but it cannot land ALONE:
#:   measured, it turns 122 further parser-REFUSED shapes at bound 5 into comparing
#:   verdicts (`277 → 422`), which REDS this very pin. §9 carries the measurement.
#: * FOUR are the pin's FIRST and most severe direction — REFUSED-BUT-GREEN, i.e.
#:   a comparing verdict on input a real parser REJECTS — and all four are of the
#:   `''''X'` form: `''''"'`, `''''\'`, `'''' '`, `''''a'`.
#:
#: None of the four is an END-TO-END fail-open, which is why this pin's bound is the
#: right place to exclude them rather than the lexer: `parse_frontmatter` returns
#: `{}` for every one of them, so the PARSE arm refuses the file before a comparing
#: verdict is ever consumed (measured).
_SHAPE_MAX_LEN = 5


def test_no_GREEN_quoted_verdict_survives_a_parser_refusal() -> None:
    """Differential: a real YAML parser is the oracle, ``_quoted_style`` the subject.

    Every quoting defect this branch fixed was found ONE SHAPE AT A TIME, and each
    round produced the next shape. This sweeps EVERY string over the alphabet the
    arms turn on, up to five characters (3110 quoted values), and asserts the two
    directions in which a bad value gets THROUGH:

    * a shape a real parser REFUSES must not come back green (``single-quoted`` /
      ``double-quoted``) — a green verdict is the one the caller COMPARES, so a
      refusal reported green can pass; and
    * a green verdict's ``body`` must equal what that parser returns, or the
      comparison is made against the wrong text.

    Measured at this head: 3110 shapes, 277 green, **0** refusals reported green,
    0 body mismatches. Non-vacuity: the same sweep driven through ``main``'s SCALAR
    VERDICT path — ``_split_comment`` + ``_quoted_style``, which is where the
    compared verdict is decided — finds **1**: `'''''` (five quotes), which YAML
    refuses as an unterminated scalar and ``main`` reports as ``single-quoted`` with
    body ``''`` — so this pin reds on the PRE-fix helper and is green only because
    the parity scan refuses to close an unterminated scalar. ⛔ ``_closing_quote``
    does NOT exist on ``main`` (this branch introduces it), so an earlier revision of
    THIS paragraph replayed a function a reader cannot find there; the pairing above
    is the one that was measured, and re-measuring it still finds exactly one shape.

    The two counters are constant-derived (alphabet x bound), never tree-derived,
    so they cannot red on a sparse tree: they exist so that an edit to those two
    constants cannot leave a sweep that checks nothing.
    """
    shapes = green = 0
    refused_but_green: list[tuple[str, str, object]] = []
    mismatched: list[tuple[str, str, object, object]] = []
    # Local imports, like the sibling pins' `parse_frontmatter`: a module-level
    # import here would shift EVERY line number below it, and the plan doc cites
    # lines in this file (the shift that cost a whole round already).
    import itertools

    import yaml

    for length in range(1, _SHAPE_MAX_LEN + 1):
        for chars in itertools.product(_SHAPE_ALPHABET, repeat=length):
            raw = "".join(chars)
            if raw[:1] not in ('"', "'"):
                continue
            shapes += 1
            try:
                parsed = yaml.safe_load(f"aboutObjects: {raw}\n")
                if not isinstance(parsed, dict):
                    raise yaml.YAMLError("a quoted scalar must still parse to a mapping")
                value, refused = parsed.get("aboutObjects"), False
            except yaml.YAMLError:
                value, refused = None, True
            style, body = _quoted_style(raw)
            if style not in ("single-quoted", "double-quoted"):
                continue
            green += 1
            if refused:
                refused_but_green.append((raw, style, body))
            elif body != value:
                mismatched.append((raw, style, body, value))
    assert shapes > 3000, (
        "the sweep shrank — `_SHAPE_ALPHABET` / `_SHAPE_MAX_LEN` no longer generate "
        f"the population this pin is calibrated on ({shapes} quoted shapes)"
    )
    assert green > 200, (
        f"only {green} of {shapes} shapes reach a COMPARING verdict, so the two "
        "assertions below would pass vacuously — re-point this pin"
    )
    assert not refused_but_green, (
        "a quoted shape YAML REFUSES was reported with a COMPARING verdict, so a "
        "truncated or unterminated value can pass the survival check: "
        f"{refused_but_green!r}"
    )
    assert not mismatched, (
        "a green verdict's body is not the text YAML reads, so the value is "
        f"compared against the wrong text: {mismatched!r}"
    )
