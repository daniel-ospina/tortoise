"""#5142 — the ONTOLOGY record of the D10 code half, pinned against drift.

Three recording gaps were found by the code review of PR #5127 (the D10 code
half) and closed by #5142. Each one is the same *class* of defect: a **claim in
the canonical ontology that nothing re-checks**, so the next lane reads it as
fact and either re-flips it, re-implements the opposite, or trusts a REQUIRED
marker the writer does not honour.

  1. **The `format` markers moved ⚠️ → ✅ with no changelog entry.** PR #5127
     (`294d5847e`) flipped §4.6's field row and §4.7's matrix row because
     `format` moved into `_SOURCE_HANDLED`, and touched no changelog line — so
     the doc asserted a landed code state with nothing recording when or why.
  2. **The deferred node collapse was unrecorded, and #5088 asserted the
     opposite.** A document keeps TWO `:Source` nodes; the collapse is
     deliberately deferred because merging them turns the `references` edge
     into a dropped self-loop and leaves every doc unit permanently incomplete
     for the index-completeness gate.
  3. **The document `:Source` scoping was unrecorded.** §4.6 marked
     `sourceKind`/`contentHash`/`version` REQUIRED ✅, while the document write
     path supplies a different set — two written (`contentHash`/`version`,
     PR #5592), one not (`sourceKind`, #5082).

This module is the **anti-drift falsifier** for all three. It asserts the doc
and the code together, so neither half can move alone:

  * the `format` markers are ✅ **and** `format` really is in
    `_SOURCE_HANDLED` (a marker that overstates the code is the class here);
  * the changelog **contains** the `_SOURCE_HANDLED` / `format` / PR #5127
    record (so a re-attribution has to carry a changelog line);
  * §4.4 carries the deferral statement **and** its `OVERRIDES:` line naming
    the departed default;
  * §4.6 carries the document-`:Source` scoping note naming `sourceKind` as
    unwritten (#5082) and `contentHash`/`version` as written (#5422);
  * `_upsert_document` writes `contentHash` **and** `version`, and writes **no**
    `sourceKind` — so both halves of that note stay true in both directions.

Hermetic: a file read plus one import of the handler class. No DB, no network,
no browser.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ONTOLOGY = REPO / "docs" / "ONTOLOGY.md"

# The two marker rows PR #5127 flipped, and the PR/commit that flipped them.
FLIP_PR = "#5127"
FLIP_COMMIT = "294d5847e"
# The landing that supplies `contentHash` + `version` on the document path.
ANCHOR_PR = "#5592"
# The open gap the §4.6 note must name.
KIND_ISSUE = "#5082"


def _doc() -> str:
    return ONTOLOGY.read_text(encoding="utf-8")


def _section(doc: str, heading: str) -> str:
    """The text of one `### …` section, up to the next `###`/`##` heading."""
    start = doc.index(heading)
    ends = [doc.find(marker, start + len(heading)) for marker in ("\n### ", "\n## ")]
    ends = [e for e in ends if e != -1]
    return doc[start:min(ends)] if ends else doc[start:]


def _changelog(doc: str) -> str:
    """The contiguous blockquote run that holds the changelog entries.

    Every changelog line is a blockquote line (blank separators are a bare
    ``>``), so the run ends at the first line that is not prefixed with ``>``.
    """
    start = doc.index("> **Changelog")
    out: list[str] = []
    for line in doc[start:].splitlines():
        if not line.startswith(">"):
            break
        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------
# 1. The `format` markers agree with the code, and the move has a changelog
#    entry naming it (the D10 model version is NOT the code version).
# --------------------------------------------------------------------------


def test_format_is_in_source_handled() -> None:
    """The ✅ marker's only justification: `format` is a fixed-clause key."""
    from tortoise.projection.entities import _EntityHandlers

    assert "format" in _EntityHandlers._SOURCE_HANDLED, (
        "§4.6/§4.7 mark `format` ✅ ('In `_SOURCE_HANDLED`'), so the code must "
        "carry it — PR #5127 moved it there (#5142)"
    )


def test_format_marker_rows_are_checked() -> None:
    """Both flipped rows read ✅; a re-flip to ⚠️/❌ must fail here."""
    doc = _doc()
    source_row = [
        ln for ln in _section(doc, "### §4.6 Source").splitlines()
        if ln.startswith("| `format` ")
    ]
    assert len(source_row) == 1, "expected exactly one `format` row in §4.6"
    assert "✅" in source_row[0], (
        "§4.6's `format` row must stay ✅ (PR #5127 moved it into "
        "`_SOURCE_HANDLED`) — #5142"
    )

    matrix_row = [
        ln for ln in _section(doc, "### §4.7 Temporal Model").splitlines()
        if ln.startswith("| format ")
    ]
    assert len(matrix_row) == 1, "expected exactly one `format` row in §4.7"
    assert "✅ `format` belongs here" in matrix_row[0], (
        "§4.7's matrix `format` row must stay ✅ — #5142"
    )


def test_changelog_records_the_format_move() -> None:
    """The flip must be recorded, not appear from nowhere in the rows."""
    changelog = _changelog(_doc())
    assert "_SOURCE_HANDLED" in changelog and "format" in changelog, (
        "the `format` markers moved ⚠️ → ✅ with no changelog entry (#5142): the "
        "changelog must record the `_SOURCE_HANDLED |= format` fact"
    )
    assert FLIP_PR in changelog or FLIP_COMMIT in changelog, (
        f"the changelog entry must name the code half that flipped the markers "
        f"({FLIP_PR} / {FLIP_COMMIT}) — the rows' '(v3.15)' is the MODEL version, "
        f"not the code's (#5142)"
    )


# --------------------------------------------------------------------------
# 2. The deferred node collapse is recorded, with its OVERRIDES line.
# --------------------------------------------------------------------------


def test_section_4_4_records_the_deferred_collapse() -> None:
    section = _section(_doc(), "### §4.4 Document")
    assert "DEFERRED" in section and "self-loop" in section, (
        "§4.4 must state that the document/corpus-source node collapse is "
        "deliberately deferred and why (the `references` self-loop) — without "
        "it, 'a document is a `:Source`' reads as an invitation to merge the "
        "two nodes and break the index-completeness gate (#5142)"
    )
    assert "references" in section and "index-completeness" in section, (
        "§4.4's deferral statement must name the `references` edge and the "
        "index-completeness gate's `edge` clause — the concrete consequence "
        "(#5142)"
    )


def test_section_4_4_carries_the_overrides_line() -> None:
    section = _section(_doc(), "### §4.4 Document")
    assert "OVERRIDES:" in section, (
        "the departure from the 'one node per document/source' default is a "
        "deliberate ruling, so it must carry an `OVERRIDES:` line (relayed from "
        "#5013) at §4.4 — the canonical home for the entity decision (#5142)"
    )
    overrides_row = [
        ln for ln in section.splitlines()
        if ln.lstrip("> ").strip().startswith("**OVERRIDES:**")
    ]
    assert len(overrides_row) == 1, "expected exactly one `OVERRIDES:` line in §4.4"
    assert "one node per" in overrides_row[0], (
        "the `OVERRIDES:` line must name the DEFAULT departed from — 'one node "
        "per document/source' / 'one node per source' — not merely state the "
        "choice (#5142)"
    )


# --------------------------------------------------------------------------
# 3. The document `:Source` scoping is recorded at §4.6, and is true of the
#    writer in BOTH directions.
# --------------------------------------------------------------------------


def test_section_4_6_records_the_document_scoping() -> None:
    section = _section(_doc(), "### §4.6 Source")
    # `sourceKind` is unwritten on a document :Source — the open work item.
    assert "Document `:Source` scoping" in section, (
        "§4.6 marks REQUIRED fields that a document `:Source` does not supply; "
        "the scoping note must state that at the row a reader trusts, instead of "
        "leaving it to the global 'code may lag' caveat (#5142)"
    )
    assert KIND_ISSUE in section, (
        f"the scoping note must name the open issue for the missing "
        f"`sourceKind` ({KIND_ISSUE}) — #5142"
    )
    assert "not written" in section, (
        "the scoping note must say which of the three REQUIRED fields the "
        "document path does NOT write — that is the one that is a work item "
        "(#5142)"
    )
    assert ANCHOR_PR in section or "#5422" in section, (
        f"the scoping note must record that `contentHash`/`version` ARE written "
        f"on the document path ({ANCHOR_PR} / #5422) — otherwise the note "
        f"overstates the gap the way the markers overstated the code (#5142)"
    )


def test_upsert_document_writes_the_anchor_pair_and_no_source_kind() -> None:
    """Both halves of the §4.6 note, true in both directions.

    `contentHash`/`version` are written (the note's ✅ half); `sourceKind` is
    not (the note's ❌ half, #5082). A change that adds the `sourceKind` write
    must update the note and this test together — the note must never be
    silently right or silently wrong.
    """
    import inspect

    from tortoise.projection.entities import _EntityHandlers

    src = inspect.getsource(_EntityHandlers._upsert_document)
    assert "s.contentHash" in src, "the document path must write the version anchor (#5422)"
    assert "s.version" in src, "the anchor pair is never half-written (#5422)"
    assert "s.sourceKind" not in src and "sourceKind=" not in src, (
        "`_upsert_document` writes no `sourceKind` (that is the #5082 gap the "
        "§4.6 scoping note records). If this write is added, #5082 is closed and "
        "the §4.6 note — plus this test — must be updated in the same change "
        "(#5142)"
    )
