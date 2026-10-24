"""The #8503 evidence bundle's doc pins — in a module a DOCS-ONLY diff runs.

⛔ WHY THIS FILE EXISTS (code-review round 9, P1 — the test-selection gap).

These two pins lived in `tests/test_extractor_v2.py`, which is registered `core`
(`config/ci-surfaces.yml`) **and is a `slow_files` member**. `docs/` is in
`NON_PYTHON_PREFIXES` and `docs/evidence/8503/` matches no `CORE_ALSO` entry, so
a diff that touches only the bundle README filtered to `changed == []` and took
`select()`'s docs-only early return — `surfaces: []`, `slow_run: False`,
tier1 ONLY. Slow files are subtracted from tier1, so the pins ran in **no leg at
all** on the one diff class that can break them: deleting the `docs/00_index.md`
row, or restoring the banner's
"so no edge_missing is an extractor-side drop" over-claim, merged GREEN and only
reddened `main` post-merge. Measured at this head:

    select(["docs/evidence/8503/README.md"]) -> full=False, surfaces=[],
        slow_run=False, 63 test files, "test_extractor_v2.py" not among them

The same class `config/ci-surfaces.yml` records as fixed at least six times
(#8614 `test_fly_secret_drift.py`, #9778, #5333, #8495, #9080) — each time as
"the doc-bound guards were asleep on the one change that can break them".

Both pins read FILES only (no graph, no DB, no network, and no import of
`tortoise.extractor_v2`), so they are safe in `tier1` — the lane a docs-only
diff actually runs. Registered in BOTH `core` and `tier1`, pinned by
`tests/test_ci_selection.py::test_the_8503_docs_pins_run_on_a_docs_only_diff`.

The third pin of the original class
(`test_the_supersede_leg_documentation_matches_the_shipped_predicate`) reads
`tortoise/extractor_v2.py`'s own source and imports the module, so it STAYS in
`tests/test_extractor_v2.py` — it is correctly bound to a code diff, and moving
it here would put an extractor import in the tier1 lane for no coverage gain.

Round 9's review P2 added one more pin to THIS file, and the same selection
argument is why it lives here rather than in `test_write_path_runner.py` (a
`slow_files` member, so tier1 subtracts it): the bundle's blind-spot map in §1
said **two** seams while `runner.operator_drop_notes` enumerated **three**, and
the document is what a receipt reader opens first. The pin compares the two
lists mechanically — count, then each clause — against the note the shipped
code actually emits, so neither copy can drift alone. It imports
`tests.eval.write_path.runner`, whose module-scope imports are stdlib +
`tests.eval.write_path.{corpus,grading,judge,schema,prescreen}` (also stdlib), so
it stays tier1-safe: measured 0.06 s import, no graph, no DB, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.eval.write_path import runner

_REPO = Path(__file__).resolve().parent.parent
_EVIDENCE_8503 = _REPO / "docs" / "evidence" / "8503"
_INDEX_00 = _REPO / "docs" / "00_index.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


#: The seam COUNT, in the form both copies state it ("Three seams", "cover
#: three seams"). Numbers are accepted so a future note that says "4 seams" is
#: compared rather than silently unmatched.
_SEAM_COUNT = re.compile(r"\b(\d+|two|three|four|five|six)\s+seams\b", re.IGNORECASE)
_SEAM_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}

#: One probe per seam, matched in BOTH copies. Deliberately topical (not a
#: whole-sentence quote): a reworded seam must still be recognized, and a seam
#: DROPPED from either copy must fail here instead of degrading the map quietly.
_SEAM_CLAUSES = (
    "commit layer",
    "snapshot failed to read",
    "NOOP",
)


def _seam_count(text: str) -> int:
    matches = _SEAM_COUNT.findall(text)
    assert len(matches) == 1, (
        f"expected exactly one 'N seams' statement in {text[:60]!r}…, found "
        f"{matches} — the blind-spot map must state one count, once")
    raw = matches[0].lower()
    return int(raw) if raw.isdigit() else _SEAM_NUMBERS[raw]


class TestEvidence8503Claims:
    def test_the_bundle_is_registered_in_the_docs_index(self):
        """Filing rule AND figure rule (the #9110 review-P2 pin's shape).

        A missing row is the review-P2 omission; two rows is an index that
        names the same artifact twice; a row citing figures the document does
        not establish is worse than no row, because it is an index entry a
        reader trusts.
        """
        rows = [line for line in _INDEX_00.read_text(encoding="utf-8").splitlines()
                if line.startswith("|") and "docs/evidence/8503/README.md" in line]
        assert len(rows) == 1, (
            "expected exactly one docs/00_index.md row naming "
            f"docs/evidence/8503/README.md, found {len(rows)}")
        row = rows[0]
        for artifact in (
            "docs/evidence/8503/before-run.json",
            "docs/evidence/8503/mutation_arm.py",
            "w2b-operator-2026-10-24-8503-a.json",
            "w2b-operator-2026-10-24-8503-b.json",
            "w2b-operator-2026-10-24-8503-preleg.json",
        ):
            assert artifact in row, f"the docs/00_index.md row does not cite {artifact}"
        # the figures the document actually establishes — and NOT the one the
        # criterion asks for. The row must publish the miss, not just the gain.
        assert "5/14" in row and "NOT the 14/14" in row, (
            "the index row must publish 5/14 and say it is NOT the 14/14 the "
            "criterion asks for — a row that reads as a gain is the over-claim "
            "this pin exists to prevent")
        assert "7/15 → 11/15" in row, "the row must carry the endpoint-recall figure"
        assert "does NOT make every `edge_missing` a selection miss" in row, (
            "the row must keep the banner's bound: the operator-drop zero does "
            "not make every edge_missing a selection miss")

    def test_the_banner_does_not_infer_every_edge_missing_is_an_extractor_drop(self):
        """The banner must state what was measured, not what it implies.

        `operator_drops = 0` is a measurement of the OPERATOR PASS. The
        SUPERSEDE row's `edge_missing` is assigned by the same document
        (§3.2b/§3.2e) to `_resolve_point_supersede` — an extractor-side
        refusal — so "so no edge_missing is an extractor-side drop" is false,
        and a lane reading the banner would hunt a prompt fix for a row whose
        repair the doc itself says needs a semantic resolver.
        """
        flat = _flat((_EVIDENCE_8503 / "README.md").read_text(encoding="utf-8"))
        # the two over-claiming sentences must be GONE, not softened
        assert "so no `edge_missing` is an extractor-side drop" not in flat
        assert "each `edge_missing` is a **selection** miss" not in flat
        # the scoped claim replaces it, and names the pass it measured
        assert "logged no OPERATOR-PASS drop in any run" in flat, (
            "the banner must scope the zero to the OPERATOR PASS")
        assert "does NOT make every `edge_missing` a selection miss" in flat
        # two-sided: the honest accounting §3.2e owns is still the summary,
        # and it still assigns the SUPERSEDE row to the resolver.
        assert "the SUPERSEDE row is the resolver" in flat, (
            "§3.2e must still state the SUPERSEDE row's class — the pin is on "
            "the banner agreeing with it, not on deleting the classification")

    def test_the_blind_spot_enumeration_matches_the_shipped_note(self):
        """§1's seam list and the run note's seam list are the SAME list.

        Round 9 (P2): the bundle — the deliverable — said **two** seams while
        `runner.operator_drop_notes` enumerated **three**, and the paragraph
        also asserted the note "says so verbatim". The omitted seam was the
        point-level `supersedes` ref dropped on a NOOP fold, i.e. exactly the
        replace-channel drop the SUPERSEDE row is about, so the document
        UNDERSTATED the instrument's blind spots in the fail-open direction:
        a lane reading it would rule that route out and hunt a prompt fix.

        The note is a pure function, so the pin compares against the text the
        shipped code actually emits rather than against a copy of it, and
        compares COUNTS as well as clauses: a fourth seam added in code fails
        here until the document states four, and a seam deleted from either
        copy fails on the clause probe.
        """
        doc = _flat((_EVIDENCE_8503 / "README.md").read_text(encoding="utf-8"))
        note = _flat(runner.operator_drop_notes(
            {"operator_drops": 0, "supersede_refusals": 0}, "llm")[0])
        # the note the code ships is the reference; the doc must match it
        assert _seam_count(note) == _seam_count(doc), (
            "docs/evidence/8503/README.md §1 states a different number of "
            "blind-spot seams than runner.operator_drop_notes emits — the doc "
            "is the copy a reader opens first, so it may not understate the "
            "instrument (this is the round-9 P2: doc said two, code said "
            "three)")
        assert _seam_count(note) >= 3, (
            "the shipped note must name all three known seams")
        for clause in _SEAM_CLAUSES:
            assert clause in note, (
                f"the shipped note no longer names the {clause!r} seam — if it "
                "was renamed, update _SEAM_CLAUSES and §1 together")
            assert clause in doc, (
                f"docs/evidence/8503/README.md §1 does not name the "
                f"{clause!r} seam that runner.operator_drop_notes names — the "
                "blind-spot map is understating the instrument again")
        # the "verbatim" claim was false when the counts differed; the pin is
        # what makes it true, so assert the doc points AT this comparison.
        assert "runner.operator_drop_notes" in doc, (
            "§1 must name the function whose note it claims to match, so the "
            "claim is checkable by a reader")
