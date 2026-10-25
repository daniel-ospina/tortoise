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
"""
from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_EVIDENCE_8503 = _REPO / "docs" / "evidence" / "8503"
_INDEX_00 = _REPO / "docs" / "00_index.md"


def _flat(text: str) -> str:
    return " ".join(text.split())


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
