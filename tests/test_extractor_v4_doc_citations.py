"""#8498 review P2 — a citation of a shipped predicate must name the shipped rule.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §15 describes
`tortoise/value_gate.py`'s identifier-only DISCARD (`#4899`). The row first
stated the rule as `^#\\d{3,}$`-style — `#4899`'s *proposed* pattern, not the
shipped one — and it was wrong in BOTH directions:

  * the shipped `_REFERENCE` is `#\\d{1,6}(?!\\d)` (1-6 digits, no anchors), so
    `#12` IS a discard the documented pattern excludes;
  * `identifier_only()` is a WHOLE-RESIDUE test, not an anchored match, so
    `See #12` discards too;
  * `#1234567` (7 digits) is NOT a discard, though `\\d{3,}` implies one.

A lane taking the pattern literally would mis-model a gate whose whole risk is
deleting memory (§7), so the row must cite the symbol — the document's own rule
is that "the symbol name is authoritative and the line number is a convenience".

WHAT THIS PINS
--------------
1. §15's value-gate row names the shipped symbol (`value_gate._REFERENCE` /
   `identifier_only()`) and carries no hand-written `^#\\d{3,}$` pattern.
2. Those symbols exist and behave as the shipped module does — so the guard
   cannot pass over a rename that would make the citation stale.

Non-vacuity: on the pre-fix document (which carried `(`^#\\d{3,}$`-style)` and
neither symbol name) check 1 fails.

Static, no network / DB / graph.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"

#: The §15 row that describes the shipped gate. Scoping the read to this row
#: keeps a `\\d{3,}` elsewhere in the document (e.g. §4.2 quoting `#4899`'s
#: false-positive text) from reding a guard that pins §15, not the whole doc.
ROW_ANCHOR = "§1 the mechanism"


def _value_gate_row() -> str:
    text = DOC.read_text(encoding="utf-8")
    assert ROW_ANCHOR in text, f"{ROW_ANCHOR!r} not found in {DOC.name}"
    start = text.index(ROW_ANCHOR)
    return text[start : text.index("\n", start)]


def test_the_value_gate_row_names_the_shipped_symbol_not_a_proposed_pattern():
    row = _value_gate_row()
    assert "value_gate._REFERENCE" in row, (
        "§15's value-gate row must name the shipped symbol "
        "(`value_gate._REFERENCE`); a hand-written pattern is what the #8498 "
        "review found wrong in both directions. Row: " + row
    )
    assert "identifier_only()" in row, row
    assert "^#\\d{3,}$" not in row, (
        "the `^#\\d{3,}$`-style pattern is #4899's PROPOSAL, not the shipped "
        "rule — `_REFERENCE` is `#\\d{1,6}(?!\\d)` and `identifier_only()` is a "
        "whole-residue test. Row: " + row
    )


def test_the_symbols_the_row_names_are_the_shipped_rule():
    """The citation is only correct while the symbols exist and behave so."""
    from tortoise import value_gate

    assert value_gate._REFERENCE.pattern == r"#\d{1,6}(?!\d)"
    # The direction the pre-fix pattern denied: a 1-2 digit ref IS discarded.
    assert value_gate.identifier_only("#12") == value_gate.RULE_REFERENCE
    # The direction it wrongly included: a 7-digit run is NOT discarded.
    assert value_gate.identifier_only("#1234567") is None
