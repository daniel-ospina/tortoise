"""#11923 review — the goal-measurement design of record must stay arithmetically self-consistent.

``docs/plans/2026-11-11-goal-measurement-and-reasoning.md`` is the design of record #11934 cites, and
its falsifiable content is §3's 15-row capability table. The head review found three of its claims are
MACHINE-CHECKABLE against the document itself, and all three were drifting:

  * **No stated total.** §3 gave no count, so #11934's body restated the table as
    ``4 ✅ · 4 🟡 · 7 ⛔`` against a table that is 2 / 6 / 7 (measured 2026-11-11). The epic and the
    design of record disagreed about the same table, with no way to tell which was right short of
    recounting it by hand.
  * **An exhaustive quantifier the table contradicts.** *"Rows 10 and 11 have no step of their own"*
    read as the complete set of unstepped 🟡 rows; row 1 is also 🟡 with no §7 step. That is the class
    the doc's own §9 VGATE note names as its worst recurring defect.
  * **One datum cited for two criteria.** ``grep -c -i '\\bindicator\\b' docs/ONTOLOGY.md == 0`` proves
    #7860's **criterion 1** (the vocabulary is documented). It cannot prove **criterion 3**
    (reachable from where the goal kind lives), whose datum is core ``CANONICAL_OBJECT_KINDS`` omitting
    ``indicator`` (§6).

Why a guard rather than careful prose: this artifact is CITED as evidence by the epic and by the build
lanes, so a false statement in it is a false pass one level downstream — the ``test_csp_plan_doc_
consistency.py`` shape. Hermetic: three committed files read as text, stdlib only, no DB, no network, no
``tortoise.*`` import.

What these guards do NOT promise, named rather than hidden:

1. The state column is read POSITIONALLY (cell 4 of a ``| n |`` row inside §3). A row that moves the
   state into another cell reds as unparsable rather than passing silently — but nothing here parses
   markdown, so a re-shaped table needs this file updated with it.
2. The unstepped/stepped guard pins the DOC'S OWN declaration (the sentence's row sets) against the
   table's 🟡 set: disjoint and complete. It does not re-derive which rows §7 steps, so adding a §7 step
   for row 1 must also update that sentence — which is the point, because the sentence is the claim.
3. The criterion guard pins ATTRIBUTION, not truth: it requires each mention of criterion 3 to carry
   the datum that can show it. The ``CANONICAL_OBJECT_KINDS`` half is pinned by NAME, not by importing
   the vocabulary (a tier-1 hermetic test does not pay ``tortoise.live``'s import for one `frozenset`
   membership). Test 5 pins the ``ONTOLOGY.md`` half against the file itself.
4. Nothing here reads an ISSUE BODY. #11934's drifted totals are cited in the doc as *measured-at*
   text; a later epic edit is invisible to these guards.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "plans" / "2026-11-11-goal-measurement-and-reasoning.md"
ONTOLOGY = ROOT / "docs" / "ONTOLOGY.md"

STATES = ("✅", "🟡", "⛔")
_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}

# The exact datum strings the doc states. Spelled once here so a reworded command reds loudly.
GREP_DATUM = "grep -c -i '\\bindicator\\b' docs/ONTOLOGY.md"
CORE_DATUM = "CANONICAL_OBJECT_KINDS"

#: How far around a criterion-3 mention its own datum may sit. Wide enough for a wrapped sentence,
#: narrow enough that a datum three sections away cannot satisfy it.
_WINDOW = 500


def doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(text: str) -> str:
    """One-line projection, so a wrapped sentence is one span regardless of line breaks."""
    return re.sub(r"\s+", " ", text)


def section(text: str, heading: str) -> str:
    """The body of ``heading`` up to the next ``## `` — a table outside §3 cannot be counted."""
    start = text.index(heading)
    rest = text[start + len(heading):]
    m = re.search(r"^## ", rest, re.MULTILINE)
    return text[start:start + len(heading) + (m.start() if m else len(rest))]


def capability_rows(text: str) -> dict[int, str]:
    """``{row number: state emoji}`` from §3's table. Unparsable rows raise rather than being skipped."""
    rows: dict[int, str] = {}
    for line in section(text, "## 3. What exists today").splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 5, f"§3 row is not a 5-cell row (table re-shaped?): {line!r}"
        number = int(cells[0])
        state = cells[3]
        assert state in STATES, f"§3 row {number} has no state emoji in cell 4: {state!r}"
        rows[number] = state
    return rows


_TOTALS_RE = re.compile(
    r"Totals, counted from the table above:\s*(\d+)\s*✅\s*·\s*(\d+)\s*🟡\s*·\s*(\d+)\s*⛔\s*of\s*(\d+)"
)


def stated_totals(text: str) -> tuple[int, int, int, int]:
    m = _TOTALS_RE.search(flat(text))
    assert m, "§3 carries no 'Totals, counted from the table above: …' line to check the table against"
    return tuple(int(g) for g in m.groups())  # type: ignore[return-value]


def _unstepped_paragraph(text: str) -> str:
    for para in re.split(r"\n\s*\n", text):
        if "have no step of their own" in para:
            return flat(para)
    raise AssertionError("§3's 'no step of their own' paragraph is gone — the claim was not made")


def declared_row_sets(text: str) -> tuple[str, set[int], set[int]]:
    """``(count word, declared unstepped rows, declared stepped rows)`` from that one paragraph."""
    para = _unstepped_paragraph(text)
    word_m = re.search(r"(\w+) 🟡 rows have no step of their own", para)
    assert word_m, f"the unstepped claim does not say HOW MANY rows it covers: {para[:160]!r}"
    seg = re.search(r"rows? ([^.]+?), and that is all of them", para)
    assert seg, (
        "the unstepped-row claim does not declare itself complete ('…. and that is all of them'); "
        f"an exhaustive-sounding set that is not declared complete is the defect: {para[:200]!r}"
    )
    unstepped = {int(n) for n in re.findall(r"\d+", seg.group(1))}
    stepped_m = re.search(r"are stepped \(([^)]+)\)", para)
    assert stepped_m, f"the paragraph does not name the 🟡 rows that ARE stepped: {para[:200]!r}"
    stepped = {int(n) for n in re.findall(r"row (\d+)", stepped_m.group(1))}
    return word_m.group(1), unstepped, stepped


_CRIT3_RE = re.compile(r"criteri(?:on|a)\s*(?:1\s*(?:and|,)\s*)?3", re.IGNORECASE)


def criterion_mentions(text: str) -> list[tuple[str, str | None]]:
    """Every criterion-3 mention, paired with the datum named near it (``None`` when absent)."""
    line = flat(text)
    found: list[tuple[str, str | None]] = []
    for m in _CRIT3_RE.finditer(line):
        window = line[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
        found.append((m.group(0), CORE_DATUM if CORE_DATUM in window else None))
    return found


# ── the guards ───────────────────────────────────────────────────────────────


def test_section_three_table_is_fifteen_rows_with_all_three_states() -> None:
    """Non-vacuity floor: the totals below are only meaningful if the table is whole."""
    rows = capability_rows(doc_text())
    assert sorted(rows) == list(range(1, 16)), f"§3 must carry rows 1–15, got {sorted(rows)}"
    assert set(rows.values()) == set(STATES), f"§3 lost a state class: {sorted(set(rows.values()))}"


def test_stated_totals_match_the_table() -> None:
    """#11923 review note 3 — the epic restated the table (4/4/7) because the doc never counted it."""
    rows = capability_rows(doc_text())
    met, partial, absent, total = stated_totals(doc_text())
    counted = {state: sum(1 for s in rows.values() if s == state) for state in STATES}
    assert (met, partial, absent) == (counted["✅"], counted["🟡"], counted["⛔"]), (
        "§3's stated totals do not match its own table: "
        f"stated {met}/{partial}/{absent}, table has {counted['✅']}/{counted['🟡']}/{counted['⛔']}"
    )
    assert total == len(rows), f"§3 states '{total}' rows but the table carries {len(rows)}"


def test_unstepped_partial_rows_are_declared_as_a_complete_set() -> None:
    """#11923 review note 2 — the exhaustive quantifier the table contradicted (row 1 was missing)."""
    rows = capability_rows(doc_text())
    partial_rows = {n for n, s in rows.items() if s == "🟡"}
    word, unstepped, stepped = declared_row_sets(doc_text())

    assert unstepped.isdisjoint(stepped), (
        f"a 🟡 row is declared both stepped and unstepped: {sorted(unstepped & stepped)}"
    )
    assert unstepped | stepped == partial_rows, (
        "the 'no step of their own' claim does not cover the 🟡 rows: "
        f"unaccounted {sorted(partial_rows - (unstepped | stepped))}, "
        f"claimed-but-not-🟡 {sorted((unstepped | stepped) - partial_rows)}"
    )
    assert _WORDS.get(len(unstepped)) == word.lower(), (
        f"the claim says '{word}' but names {len(unstepped)} row(s) {sorted(unstepped)}"
    )


def test_every_criterion_three_mention_carries_its_own_datum() -> None:
    """#11923 review note 1 — the grep proves criterion 1; criterion 3's datum is the core vocabulary."""
    mentions = criterion_mentions(doc_text())
    assert mentions, "the doc no longer references #7860's criterion 3; update this guard with it"
    undatumed = [m for m, datum in mentions if datum is None]
    assert not undatumed, (
        "a criterion-3 mention does not carry its own datum "
        f"(`{CORE_DATUM}`) within {_WINDOW} chars: {undatumed}. The grep proves criterion 1 "
        "(documented); criterion 3 is REACHABILITY and only the core vocabulary can show it."
    )


def test_the_grep_datum_the_doc_states_still_holds() -> None:
    """The doc's criterion-1 datum is ``grep -c -i '\\bindicator\\b' docs/ONTOLOGY.md == 0``.

    A tripwire, not an endorsement: when the vocabulary lands, this reds so §9 O1 and the header scope
    block are updated with it — the design of record must not keep claiming a state the repo left.
    """
    text = doc_text()
    assert GREP_DATUM in flat(text), f"the doc no longer states the criterion-1 datum verbatim: {GREP_DATUM!r}"
    hits = len(re.findall(r"\bindicator\b", ONTOLOGY.read_text(encoding="utf-8"), re.IGNORECASE))
    assert hits == 0, (
        f"docs/ONTOLOGY.md now mentions `indicator` {hits}x, so #7860 criterion 1 is MET — update §9 O1 "
        "and the header scope block in docs/plans/2026-11-11-goal-measurement-and-reasoning.md "
        "(do not delete this tripwire: the doc is what the epic cites)."
    )
