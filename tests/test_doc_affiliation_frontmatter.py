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
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The required `docs/` affiliation fields (AGENTS.md -> document-conventions).
REQUIRED_KEYS = ("type", "domain", "doc_status", "created")
TEAM_KEYS = ("subjects.team", "ownedBy")

# ── #13452: presence is not SURVIVAL ───────────────────────────────────────

#: A value whose text BEGINS with `#` is **entirely** a YAML comment: the key
#: resolves to `None` and 100% of the value is gone. This is UNAMBIGUOUS — a key
#: with a real value never resolves to `None` because of a comment (`null`/`~`
#: carry no `#`) — so no digit needs to appear for it to be a defect. YAML ends a
#: plain scalar at any `#` not preceded by a non-space character
#: (`ns-plain-char(c)`, YAML 1.2.2 §7.3.3; PyYAML here is 6.0.3, a YAML 1.1
#: resolver whose [154]/[156] productions state the same rule).
_WHOLE_VALUE_COMMENT = "#"

#: `key: value` / `key:` / `key:   ` at column 0. Group 2 is the one-line value,
#: and it is OPTIONAL — a trailing space must not let a null-ish value slip past
#: enumeration, or check (D) below could be evaded by one keystroke (review).
_KV = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):[ \t]*(\S.*)?$")

#: A block-sequence item under a key (`- value`).
_ITEM = re.compile(r"^[ \t]*-[ \t]+(\S.*)$")

#: Characters that open a block scalar (`|`, `>`) or a flow collection (`[`, `{`).
_BLOCK_OR_FLOW = "|>[{ "

#: The two fields that carry the affiliation itself (#13452). The arms above
#: check that the KEYS are present; here every one of these VALUES must be
#: measured by a named check, or the arm fails: an unmeasured affiliation is
#: exactly the defect (a value at 40% of its length keeps its key and its green).
AFFILIATION_KEYS = ("aboutSubjects", "aboutObjects")

#: Styles this arm declines to compare, as explicit ``(path, key)`` pairs.
#: DELIBERATELY EMPTY: #13452's target is zero truncated values AND zero silent
#: omissions, so an unmeasured affiliation FAILS (check D) until somebody writes
#: the pair here — a new exemption is a line a person had to add, never a blanket
#: rule that lets a whole style or file go unmeasured without saying so.
UNMEASURED: frozenset[tuple[str, str]] = frozenset()


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
                            A QUOTED value is closed, so an indented comment
                            line after it is not a continuation; a PLAIN value
                            can continue, and a continuation line opening with
                            `#` becomes a comment — so that shape is refused.
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
            # A QUOTED scalar is CLOSED: a following indented line cannot be part
            # of it, so an indented comment is just a comment. A PLAIN scalar can
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


def _split_comment(raw: str) -> str:
    """A QUOTED value's text with a trailing `` <space>#…`` comment removed.

    Only quoted values reach this: the split happens solely after the closing
    quote, so a ` #` inside the quotes stays content. A PLAIN value is
    deliberately NOT split — YAML ends it at the `#`, and the caller compares the
    parse against the RAW text, so a shortened value FAILS instead of quietly
    matching a value that was shortened the same way. (An earlier draft DID split
    plain values here, which made the survival comparison tautological and let it
    report coverage it did not have — found in review.)
    """
    q = raw[0]
    i = raw.find(" #")
    while i != -1:
        if raw[:i].rstrip().endswith(q):
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


def _quoted_style(raw: str) -> tuple[str, str | None]:
    """``(style, body)`` for a quoted value; ``("plain", None)`` when unquoted.

    ``broken`` is the truncation check (C) exists for: an UNESCAPED inner `"`, an
    unbalanced `'`, or no closing quote — YAML ends the scalar there (measured:
    `"a, "#1" KEEP"` parses to `'a, '`). The unescaped-quote test runs BEFORE the
    escape branch, because an escaped body can still carry an unescaped quote and
    an earlier order let that truncation through green (found in review).

    ``escaped`` declines the value comparison (hand de-quoting cannot reproduce
    `\\t`, `\\uXXXX`) but has already been checked for ``broken``.
    """
    if raw[:1] not in ('"', "'"):
        return "plain", None
    q = raw[0]
    value = _split_comment(raw)
    if len(value) < 2 or not value.endswith(q):
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
    parseable, seven references gone. Measured on main: 68 blocks, 8 truncated,
    every one an `aboutObjects`; the merged dossier's landed at 57 of 141 chars.
    Presence, and even parseability, is not survival.

    Both are read through the SAME consumer the index pipeline uses, so this
    measures the real degradation rather than a re-implementation.

    The affiliation VALUE is measured four ways, because one is provably not
    enough — each of the first three was demonstrated to miss a real truncation:

    (A) a value whose text BEGINS with `#` is entirely a comment: the key
        resolves to ``None`` and 100% of the value is gone. Checked for EVERY key
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

    Multi-line plain values and continued sequence items are REFUSED (they are
    legal folding, and a continuation line opening with `#` is silently
    swallowed); a character-level typo inside a quoted value is NOT a truncation
    and stays green — claiming otherwise would be a false claim of coverage.

    Non-vacuity: at the pre-fix tree ``docs/research/2026-11-18-13420-vector-
    placement/dossier.md`` opens its ``aboutSubjects`` value with a backtick, so
    ``parse_frontmatter`` returns ``{}`` and (A)/(B) red it there too.
    """
    from tortoise.file_indexer import parse_frontmatter

    paths = sorted((ROOT / "docs" / "research").glob("**/*.md"))
    assert paths, "docs/research/ has no markdown files — re-point this guard"
    failures: dict[str, str] = {}
    declared: set[tuple[str, str]] = set()
    measured: set[tuple[str, str]] = set()
    compared = 0
    for path in paths:
        # No frontmatter block at all is the OTHER arms' business; this arm is
        # about blocks that exist and do not parse / do not survive.
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
        declared |= {(rel, k) for k, _, _ in _values(fm) if k in AFFILIATION_KEYS}

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
            for text in texts:
                if text.lstrip().startswith(_WHOLE_VALUE_COMMENT):
                    bad.append(
                        f"`{key}` is ENTIRELY a YAML comment — everything after "
                        f"the leading `#` is discarded and the key resolves to "
                        f"None. Quote the value."
                    )
                    break
            if kind in ("scalar_multiline", "sequence_multiline"):
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
                for raw_item, got_item in zip(texts, got_list):
                    style, expected = _quoted_style(raw_item)
                    if style in ("broken", "malformed"):
                        bad.append(
                            f"`{key}` item {raw_item!r} is {style} — a quoted item "
                            "YAML cannot keep whole is TERMINATED at that quote. "
                            "Remove the inner quote, or use a block scalar."
                        )
                        break
                    if style == "escaped":
                        continue
                    if expected is None:
                        expected = raw_item
                    compared += 1
                    if not isinstance(got_item, str) or got_item != expected:
                        bad.append(
                            f"`{key}` item {raw_item!r} (len {len(expected)}) "
                            f"became {got_item!r} — a sequence item has no legal "
                            "transform, so this is a truncation. Quote the item."
                        )
                        break
                continue

            if kind != "scalar":
                continue

            # (B)/(C) are the affiliation's own instruments: a value the
            # affiliation carries is the one this issue is about.
            style, expected = _quoted_style(texts[0])
            if style in ("broken", "malformed"):
                bad.append(
                    f"`{key}` is a quoted value YAML cannot keep whole "
                    f"({style}: an unescaped inner quote, an unbalanced quote, or "
                    "no closing quote) — the value is TERMINATED there. Remove "
                    "the inner quote, or use a block scalar."
                )
                continue
            if key not in AFFILIATION_KEYS:
                continue
            if style == "escaped":
                continue  # declined: (C) already cleared it, (B) cannot de-quote
            if expected is None:
                expected = texts[0]  # plain: YAML performs no transform
            got = parsed.get(key)
            if not isinstance(got, str):
                bad.append(
                    f"`{key}` does not survive the parse: the raw value "
                    f"{texts[0]!r} became {got!r} — a plain scalar has no legal "
                    "transform, so this is a truncation. Quote the value."
                )
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
    unmeasured = sorted(declared - measured - UNMEASURED)
    assert not unmeasured, (
        "these affiliation values were NOT measured by ANY check — a silently "
        "unmeasured affiliation is the #13452 defect. Quote the value (plain or "
        "simply-quoted is measured), or, if the style is deliberate, add the "
        f"(path, key) pair to UNMEASURED with a reason: {unmeasured}"
    )
    assert measured, "measured no affiliation value at all — re-point this guard"
    assert compared, "compared no value at all — re-point this guard"
    assert not failures, (
        "docs/research/**/*.md frontmatter is unparseable or does not SURVIVE "
        f"the parse ({len(measured)} affiliation values measured, {compared} "
        f"compared): {failures}"
    )


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
