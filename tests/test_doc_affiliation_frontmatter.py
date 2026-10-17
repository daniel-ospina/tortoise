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

Non-vacuity: on the pre-fix tree the two architecture companions carry neither
``created`` nor a team key, and the STORAGE companion carries the copied gate —
so checks 2 and 3 fail there.

Static, no network / DB / graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: The required `docs/` affiliation fields (AGENTS.md -> document-conventions).
REQUIRED_KEYS = ("type", "domain", "doc_status", "created")
TEAM_KEYS = ("subjects.team", "ownedBy")

#: The changelog companions this change adds — the files the review flagged.
COMPANIONS = (
    "docs/ONTOLOGY-CHANGELOG.md",
    "docs/architecture/EXTRACTOR-V4-ARCHITECTURE-CHANGELOG.md",
    "docs/architecture/STORAGE-ARCHITECTURE-CHANGELOG.md",
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
