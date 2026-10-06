"""The declared provenance-marker contract for ``graph-scripts/`` (#4830).

``tools/graph_script_provenance.py`` holds the ONE declaration; this file is the
consumer that reds when it stops holding. Three assertions:

(a) every provenance marker present in ``graph-scripts/`` is a member of the
    declared spelling set — an unrecognised spelling is REJECTED, not counted;
(b) every script that names a price of this product carries a marker AND names
    ``product/pricing.json`` (the source ``tortoise/pricing.py`` names
    canonical); and
(c) the corpus is a DECLARED PARTITION — every tracked script is either marked
    or listed as markerless residue, so the residue can only shrink.

Every assertion is paired with a fixture that shows it can FAIL. A new guard
that cannot fail is not a guard, and this repo has shipped three of those in a
single week.

What this file deliberately does NOT assert: that every DECLARED spelling is
still in use. Unifying the 24 files onto one spelling is the issue's separate
retrofit; the declaration is a superset of what is in use ON PURPOSE, so the
retrofit can normalise without reddening here. Asserting "no dead spelling"
would make the contract block the very change it exists to enable.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.graph_script_provenance import (  # noqa: E402
    CANONICAL_PRICE_SOURCE,
    GRAPH_SCRIPTS,
    MARKER_CANDIDATE_RE,
    MARKER_SPELLINGS,
    PRICE_BEARING_SCRIPTS,
    UNMARKED_SCRIPTS,
    declared_spelling,
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
        '\"\"\"Doc.\"\"\"\nimport sys\n\nsuperseded = [(row[0], row[1]) for row in r]\n',
    )
    result = scan(tmp_path)
    assert result.unmatched == ()
    assert result.marked == {}


# ── (c) the corpus is a declared partition ─────────────────────────────────


def test_the_corpus_is_a_declared_partition():
    """marked ∪ UNMARKED_SCRIPTS == corpus, and disjoint. Both directions: a
    new script cannot arrive unclassified, and the residue cannot drift."""
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


def test_a_new_unmarked_script_is_unclassified(tmp_path):
    """A script with neither a marker nor a declaration reds (the ratchet)."""
    _write(tmp_path, "fresh_driver.py", "print('live')\n")
    _, marked, unmarked_or_declared = _partition(tmp_path)
    assert marked == set()
    assert "fresh_driver.py" not in unmarked_or_declared


def _partition(root: Path) -> tuple[set[str], set[str], set[str]]:
    """(corpus, marked, declared-markerless) for *root* — the partition inputs."""
    result = scan(root)
    return set(result.corpus), set(result.marked), set(UNMARKED_SCRIPTS)


# ── (b) a price-naming script carries a marker AND the canonical source ────


def test_the_price_trigger_fires_on_the_real_stale_tier_scripts():
    """The trigger is measured against the REAL gap the issue names, not only a
    synthetic fixture: these files assert non-canonical tiers as ours."""
    detected = set()
    for path in sorted(GRAPH_SCRIPTS.glob("*.py")):
        if names_a_price(path.read_text(encoding="utf-8", errors="replace")):
            detected.add(path.name)
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
    """The fixture form of the issue's sharpest gap: stale-as-ours pricing with
    a marker that says nothing about pricing. Being MARKED does not satisfy (b)."""
    stale = f"{MARKED_BUT_STALE}claim = [CONFIDENCE] premium $100/mo is our mid-tier\n"
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
