"""The declared provenance-marker contract for ``graph-scripts/`` (#4830).

``tools/graph_script_provenance.py`` holds the ONE declaration; this file is the
consumer that reds when it stops holding:

(a) every provenance marker present in ``graph-scripts/`` is a member of the
    declared spelling set — an unrecognised spelling is REJECTED, not counted;
(b) every script that names a price of this product carries a marker AND names
    ``product/pricing.json`` (the source ``tortoise/pricing.py`` names
    canonical); and
(c) the corpus is a DECLARED PARTITION — every tracked script is either marked
    or listed as markerless residue, and the residue cannot grow.

(a) is bounded by ``MARKER_CANDIDATE_RE``: it catches a marker that *opens* on a
provenance stem. A spelling opening on no stem, a marker below the header, and a
second annotation on an already-marked file are the declared bounds, documented
in the module's DECLARED BOUNDS section — closing them is what #4830's
machine-readable field is for, not a broader regex (a stem-anywhere rule was
measured and reds on ordinary prose).

Every assertion here is EXERCISED THROUGH THE GATE, not through its inputs: the
fixtures below call ``violations()`` itself and assert the violation. Review
round 1 found that only the passing direction was tested, so three separate
mutations of ``violations()`` (whole body → ``[]``, ``(b)`` deleted, the
partition deleted) each passed the suite. A new guard that cannot fail is not a
guard, and this repo has shipped three of those in a single week.

What this file deliberately does NOT assert: that every DECLARED spelling is
still in use. Unifying the 24 files onto one spelling is the issue's separate
retrofit; the declaration is a superset of what is in use ON PURPOSE, so the
retrofit can normalise without reddening here. Asserting "no dead spelling"
would make the contract block the very change it exists to enable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.graph_script_provenance import (  # noqa: E402
    CANONICAL_PRICE_SOURCE,
    GRAPH_SCRIPTS,
    MARKER_CANDIDATE_RE,
    MARKER_SPELLINGS,
    PRICE_BEARING_SCRIPTS,
    UNMARKED_CEILING,
    UNMARKED_SCRIPTS,
    declared_spelling,
    header_lines,
    names_a_price,
    names_canonical_source,
    scan,
    violations,
)

#: The real spelling #1, used by the fixtures below to build a script that is
#: MARKED (so the marker check passes) but stale-as-ours in its pricing.
MARKED_BUT_STALE = (
    "# Historical — uses embedded tortoise.db. Do not run against production "
    "Docker.\n"
)

#: A plausible FIFTH spelling — nothing declares it, so it must be refused.
NOVEL_SPELLING = "# FROZEN (2026-01-01) — do not run against production Docker.\n"

#: A price line of this product, in the superseded tier vocabulary.
STALE_PRICE = "premium $100/mo is our mid-tier\n"


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


# ── (a) every marker is a declared spelling ────────────────────────────────


def test_every_marker_in_graph_scripts_is_declared():
    """No marker line in the corpus escapes the declaration (fail-closed)."""
    result = scan()
    assert result.unmatched == (), (
        "graph-scripts/ carries a marker spelling that is not declared in "
        f"MARKER_SPELLINGS: {result.unmatched}"
    )


def test_the_marker_candidate_scan_is_not_vacuous():
    """The scan really finds markers — a corpus that stopped matching must not
    read as 'clean'."""
    result = scan()
    assert result.marked, "no provenance marker was found at all"
    assert len(result.marked) >= 20, (
        f"only {len(result.marked)} marked files — the scan or the corpus moved"
    )
    for spelling in set(result.marked.values()):
        assert spelling in MARKER_SPELLINGS


def test_a_novel_spelling_is_found_and_refused(tmp_path):
    """A fifth spelling that nobody declared is FOUND, not silently counted."""
    _write(tmp_path, "novel.py", NOVEL_SPELLING)
    result = scan(tmp_path)
    assert [name for name, _, _ in result.unmatched] == ["novel.py"]
    assert declared_spelling(NOVEL_SPELLING.strip()) is None


def test_the_candidate_pattern_covers_plausible_new_spellings(tmp_path):
    """The candidate scan is broader than the declared set on purpose — that is
    what makes the refusal fail-closed instead of blind."""
    for line in (
        NOVEL_SPELLING.strip(),
        "# DEPRECATED — superseded by the generic command.",
        "⚠️ SUPERSEDED — some other option set.",
        "# ARCHIVED (2026-01-01) — do not run.",
    ):
        assert MARKER_CANDIDATE_RE.match(line), line
    # …and a body identifier of the same word is NOT a marker. That exclusion is
    # the HEADER REGION doing the work, not the pattern — which is why it is
    # asserted through ``scan()``. ``audit_graph.py``'s ``superseded = [...]`` is
    # the live noise case; flagging it is exactly what would train a guard away.
    _write(
        tmp_path,
        "body_ident.py",
        '"""Doc."""\nimport sys\n\nsuperseded = [(row[0], row[1]) for row in r]\n',
    )
    result = scan(tmp_path)
    assert result.unmatched == ()
    assert result.marked == {}


# ── the header region itself ──────────────────────────────────────────────


def test_a_prefixed_docstring_is_part_of_the_header():
    """A ``r\"\"\"`` module docstring IS the header. Matching only bare triple
    quotes missed it entirely, so a marker inside it was neither counted nor
    refused — a fail-OPEN hole (review round 1)."""
    source = f'r"""\n{MARKER_SPELLINGS[3]}\n"""\nimport sys\n'
    lines = header_lines(source)
    assert [lineno for lineno, _ in lines] == [1, 2, 3]
    assert declared_spelling(lines[1][1]) == MARKER_SPELLINGS[3]


def test_a_trailing_comment_does_not_inflate_the_header(tmp_path):
    """A docstring closing on its own line with a trailing comment must still
    CLOSE. Deciding the close with ``endswith`` swallowed everything after it
    into the marker region, reddening a valid file (review round 1).

    Multi-line on purpose: a single-line opener is closed by the OPENER branch,
    so only the multi-line shape exercises the in-docstring close test.
    """
    source = (
        '"""Doc line.\nmore.\n"""  # trailing comment\n'
        "import sys\n\nsuperseded = [(row[0]) for row in r]\n"
    )
    assert [lineno for lineno, _ in header_lines(source)] == [1, 2, 3]
    _write(tmp_path, "trailing.py", source)
    assert scan(tmp_path).unmatched == ()


def test_scan_marked_is_read_only(tmp_path):
    """``frozen=True`` must state a property the record has: a mutable ``dict``
    field made the dataclass hashable-in-name-only (review round 1)."""
    _write(tmp_path, "marked.py", MARKED_BUT_STALE)
    result = scan(tmp_path)
    with pytest.raises(TypeError):
        result.marked["marked.py"] = "whatever"  # type: ignore[index]


# ── the gate itself — each violation kind, asserted on ``violations()`` ────


def test_the_gate_is_clean_on_a_corpus_that_holds(tmp_path):
    """Not always-red: a valid corpus with matching declarations is CLEAN. Both
    directions matter — a gate that always fails tests nothing."""
    _write(tmp_path, "marked.py", MARKED_BUT_STALE)
    assert violations(tmp_path, unmarked=frozenset(), price_bearing=frozenset()) == []


def test_the_gate_refuses_an_unrecognised_marker(tmp_path):
    _write(tmp_path, "novel.py", NOVEL_SPELLING)
    problems = violations(
        tmp_path, unmarked=frozenset({"novel.py"}), price_bearing=frozenset()
    )
    assert any(p.startswith("UNRECOGNISED MARKER novel.py") for p in problems), problems


def test_the_gate_flags_an_unclassified_file(tmp_path):
    """The ratchet: a script that is neither marked nor declared reds."""
    _write(tmp_path, "fresh_driver.py", "print('live')\n")
    problems = violations(tmp_path, unmarked=frozenset(), price_bearing=frozenset())
    assert any(p.startswith("UNCLASSIFIED fresh_driver.py") for p in problems), problems


def test_the_gate_flags_a_stale_unmarked_declaration(tmp_path):
    """A declared-markerless file that has since been marked must be removed from
    the declaration, so the residue count stays honest."""
    _write(tmp_path, "marked.py", MARKED_BUT_STALE)
    problems = violations(
        tmp_path, unmarked=frozenset({"marked.py"}), price_bearing=frozenset()
    )
    assert any(p.startswith("STALE DECLARATION marked.py") for p in problems), problems


def test_the_gate_flags_an_undeclared_price_script(tmp_path):
    """A new price-naming script cannot be quietly left out of assertion (b)."""
    _write(tmp_path, "priced.py", f"{MARKED_BUT_STALE}{STALE_PRICE}")
    problems = violations(tmp_path, unmarked=frozenset(), price_bearing=frozenset())
    assert any(p.startswith("UNDECLARED PRICE SCRIPT priced.py") for p in problems), (
        problems
    )


def test_the_gate_flags_a_price_script_without_the_canonical_source(tmp_path):
    """(b)'s core case: marked, price-naming, and silent about the canonical
    source — the issue's sharpest gap, and the state the three real scripts were
    in before Option A."""
    _write(tmp_path, "priced.py", f"{MARKED_BUT_STALE}{STALE_PRICE}")
    problems = violations(
        tmp_path, unmarked=frozenset(), price_bearing=frozenset({"priced.py"})
    )
    assert any(
        p.startswith("PRICE SCRIPT WITHOUT CANONICAL SOURCE priced.py")
        for p in problems
    ), problems


def test_the_gate_flags_a_price_script_without_a_marker(tmp_path):
    """The other half of (b): naming the source does not excuse a missing marker."""
    _write(
        tmp_path,
        "priced.py",
        f"# Canonical source: {CANONICAL_PRICE_SOURCE}.\n{STALE_PRICE}",
    )
    problems = violations(
        tmp_path,
        unmarked=frozenset({"priced.py"}),
        price_bearing=frozenset({"priced.py"}),
    )
    assert any(
        p.startswith("PRICE SCRIPT WITHOUT MARKER priced.py") for p in problems
    ), problems


def test_the_gate_enforces_the_residue_ceiling(tmp_path):
    """The markerless residue may not GROW — otherwise every future unmarked
    script could be absorbed by adding one more line to the declaration."""
    grown = frozenset(f"x{i}.py" for i in range(UNMARKED_CEILING + 1))
    problems = violations(tmp_path, unmarked=grown, price_bearing=frozenset())
    assert any(p.startswith("RESIDUE GREW") for p in problems), problems


# ── (c) the corpus is a declared partition ─────────────────────────────────


def test_the_corpus_is_a_declared_partition():
    """marked ∪ UNMARKED_SCRIPTS == corpus, and disjoint."""
    result = scan()
    marked, corpus = set(result.marked), set(result.corpus)

    assert corpus >= marked | UNMARKED_SCRIPTS, (
        f"unclassified: {sorted(corpus - marked - UNMARKED_SCRIPTS)}"
    )
    assert marked.isdisjoint(UNMARKED_SCRIPTS), (
        f"declared markerless but marked: {sorted(marked & UNMARKED_SCRIPTS)}"
    )
    assert (marked | UNMARKED_SCRIPTS) >= corpus
    # A glob that silently stopped matching must fail, never pass vacuously.
    assert len(corpus) >= 50, f"corpus collapsed to {len(corpus)} file(s)"
    assert len(UNMARKED_SCRIPTS) <= UNMARKED_CEILING


# ── (b) a price-naming script carries a marker AND the canonical source ────


def test_the_price_trigger_fires_on_the_real_stale_tier_scripts():
    """The trigger is measured against the REAL gap the issue names, not only a
    synthetic fixture: these files assert non-canonical tiers as ours."""
    detected = {
        path.name
        for path in sorted(GRAPH_SCRIPTS.glob("*.py"))
        if names_a_price(path.read_text(encoding="utf-8", errors="replace"))
    }
    assert detected >= PRICE_BEARING_SCRIPTS, (
        f"declared price-bearing but the trigger does not fire: "
        f"{sorted(PRICE_BEARING_SCRIPTS - detected)}"
    )
    assert detected.issuperset({"bp_approach_cycle3.py", "bp_approach_cycle4.py"})


def test_the_price_declaration_is_not_vacuous():
    assert PRICE_BEARING_SCRIPTS, "an empty price set makes (b) unfalsifiable"


def test_price_bearing_scripts_carry_a_marker_and_name_the_canonical_source():
    """(b) itself, on the corpus."""
    result = scan()
    for name in sorted(PRICE_BEARING_SCRIPTS):
        assert name in result.marked, f"{name} names a price but is unmarked"
        source = (GRAPH_SCRIPTS / name).read_text(encoding="utf-8")
        assert names_canonical_source(source), (
            f"{name} names a price of this product and does not name "
            f"{CANONICAL_PRICE_SOURCE}"
        )


def test_a_marked_script_with_a_stale_price_and_no_source_reds():
    """The fixture form of the issue's sharpest gap: being MARKED does not
    satisfy (b) when the marker says nothing about pricing."""
    stale = MARKED_BUT_STALE + STALE_PRICE
    assert declared_spelling(MARKED_BUT_STALE.strip()) is not None
    assert names_a_price(stale) is True, "the price trigger missed the real gap"
    assert names_canonical_source(stale) is False, "(b) would pass vacuously"


def test_the_canonical_source_must_be_named_in_the_header():
    """The marker block is where the source belongs — a passing mention in the
    body is not the annotation."""
    body_only = f"{MARKED_BUT_STALE}import sys\n\n# see {CANONICAL_PRICE_SOURCE}\n"
    assert names_canonical_source(body_only) is False
    in_header = (
        f"{MARKED_BUT_STALE}"
        f"# Price-bearing — canonical source: {CANONICAL_PRICE_SOURCE}.\n"
    )
    assert names_canonical_source(in_header) is True


# ── the whole gate ─────────────────────────────────────────────────────────


def test_the_declared_contract_holds():
    """The gate: no violation of the declared contract."""
    assert violations() == []
