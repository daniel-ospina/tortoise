"""#7551 — the operator-label declared set is the DECLARED vocabulary.

The defect was not a missing capability: `create_operator` already returned a
structured `undeclared_relation` warning, and `sdk.py` already documented it.
What was wrong was the set it consulted — **pack relations only**. So every
*core* predicate read as undeclared, and the warning carried no information:
`supports` — the graph's own evidence→claim convention (ONTOLOGY §8) — warned
exactly like an invented name. A lane that could not find a reachable declared
predicate invented one, which is how `measures` accumulated (#7551).

Two halves, both written to be able to fail:

1. **Vocabulary (no DB).** `CORE_OPERATOR_LABELS` is the mechanism pair
   (`IMPL`/`NAND`, ONTOLOGY §3.9) unioned with the closed Layer-2 semantics
   vocabulary (§8) — and the doc's table is **transcribed here** rather than
   derived from the constant it guards, so an undocumented label cannot become
   silently accepted by being added to the constant (the mirror of the defect
   this closes; #7852's pending names are absent on purpose for the same
   reason).
2. **Write path (embedded DB).** A core label writes with NO warning; a
   pack-declared predicate still writes with no warning (the UNION leg, not a
   replacement); `measures` still warns and the write still PROCEEDS
   (warn-not-block, #1934 — deliberately unchanged, see #7551's resolution).

Runnable with:
``TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_operator_label_vocabulary.py -q``
The DB fixture is a module-scoped embedded graph (``db_path``) — no server needed.
"""
from __future__ import annotations

import pytest

from tortoise.pack_registry import (
    CORE_OPERATOR_LABELS,
    CORE_PREDICATES,
    SEMANTICS_TRANSITION,
    SEMANTICS_VALUES,
)

#: ONTOLOGY §8 "The semantics vocabulary (Layer 2)" — transcribed from the
#: doc's table, NOT derived from the constant it guards. A guard that restates
#: the production expression guards only its own line; this literal is what
#: makes `CORE_OPERATOR_LABELS` answerable to the canonical document.
O8_SEMANTICS_VOCABULARY = {
    "addresses",
    "supports",
    "refutes",
    "mutuallySupportive",
    "mutuallyExclusive",
    "hasPart",
    "causes",
    "dependsOn",
    "transacts",
    "related",
    "ownedBy",
    "managedBy",
}

#: ONTOLOGY §3.9: "Epistemic edges (operators): `IMPL`, `NAND` (+ semantic label)."
O39_MECHANISM_PAIR = {"IMPL", "NAND"}

#: A predicate that IS declared — but by a PACK (agent-ops/product-strategy),
#: never by core. It is the control proving the declared set is a UNION.
PACK_ONLY_PREDICATE = "groundedIn"

#: The name the issue found: declared by nothing, core or pack.
UNDECLARED_NAME = "measures"


class TestOperatorLabelVocabulary:
    """The declared set, answered to the document."""

    def test_semantics_vocabulary_is_the_documented_table(self):
        assert set(SEMANTICS_VALUES) == O8_SEMANTICS_VOCABULARY

    def test_core_operator_labels_are_the_documented_set(self):
        # Adding an undocumented label to `CORE_OPERATOR_LABELS` fails here.
        assert set(CORE_OPERATOR_LABELS) == O39_MECHANISM_PAIR | O8_SEMANTICS_VOCABULARY

    def test_mechanism_pair_is_a_subset_and_the_pair_constant_is_unchanged(self):
        assert set(CORE_PREDICATES) == O39_MECHANISM_PAIR
        assert set(CORE_OPERATOR_LABELS) >= O39_MECHANISM_PAIR

    def test_measures_is_absent_and_the_graph_convention_is_present(self):
        # `supports` is the predicate the issue's resolution designates for
        # evidence→claim; `measures` is the invented one (#7551).
        assert UNDECLARED_NAME not in CORE_OPERATOR_LABELS
        assert "supports" in CORE_OPERATOR_LABELS
        assert "addresses" in CORE_OPERATOR_LABELS

    def test_retired_spellings_are_not_accepted_as_labels(self):
        # A retired word must keep WARNING so the caller learns the canonical
        # one — accepting it here would make the transition map silent.
        for retired in SEMANTICS_TRANSITION:
            assert retired not in CORE_OPERATOR_LABELS, retired

    def test_structural_edge_predicates_are_not_operator_labels(self):
        # `create_edge`'s vocabulary is a DIFFERENT surface (ONTOLOGY §3.9).
        for structural in ("aboutSubject", "extractedFrom", "performs",
                           "references", "wasDerivedFrom"):
            assert structural not in CORE_OPERATOR_LABELS, structural


@pytest.fixture(scope="module")
def sdk(tmp_path_factory):
    # ONE SDK instance for the whole write-path class. Measured: the embedded
    # carve-out's first write carries a ~125s one-time warm-up (the sibling
    # guard tests/test_6131_operator_mutation_vocabulary.py shows the same
    # 125.6s on its first call, 131s for 5 tests), so a per-test fixture would
    # pay that once per test for no extra isolation.
    from tortoise.sdk import TortoiseSDK

    path = tmp_path_factory.mktemp("operator-labels") / "t.db"
    return TortoiseSDK(db_path=str(path))


@pytest.fixture(scope="module")
def pair(sdk):
    a = sdk.create_point("evidence", "7551 evidence probe")["id"]
    b = sdk.create_point("statement", "7551 claim probe")["id"]
    return a, b


class TestWritePathDeclaredness:
    """What the write path actually accepts — behaviour, not the constant.

    One point pair serves every case: membership in `CORE_OPERATOR_LABELS` is
    pinned exhaustively by `TestOperatorLabelVocabulary` above, so the write
    path only has to prove the constant is CONSULTED. The labels here are
    therefore the representative ones, not all fifteen.
    """

    def _codes(self, out) -> list[str]:
        return [w.get("code") for w in out.get("warnings", [])]

    def test_core_operator_labels_write_without_a_warning(self, sdk, pair):
        # The regression that reddens this file: a core predicate warning like
        # an invented one (the pre-fix behaviour for EVERY core label).
        # `IMPL` is the no-regression control (it was hardcoded before);
        # `supports`/`addresses` are the issue's names; `related` is a §8
        # semantics value that is NOT the mechanism pair.
        a, b = pair
        for label in ("IMPL", "supports", "addresses", "related"):
            out = sdk.create_operator("IMPL", a, [b], label=label)
            assert self._codes(out) == [], (label, out.get("warnings"))
            assert out.get("id"), label

    def test_pack_declared_predicate_still_writes_without_a_warning(self, sdk, pair):
        # The UNION leg: the fix must ADD the core vocabulary, not replace the
        # pack registry lookup with it.
        a, b = pair
        out = sdk.create_operator("IMPL", a, [b], label=PACK_ONLY_PREDICATE)
        assert self._codes(out) == [], out.get("warnings")

    def test_measures_still_warns_and_still_writes(self, sdk, pair):
        # `measures` is declared nowhere. warn-not-block is a RECORDED decision
        # (#1934 / #6131) — this fix must not turn it into a refusal.
        a, b = pair
        out = sdk.create_operator("IMPL", a, [b], label=UNDECLARED_NAME)
        assert self._codes(out) == ["undeclared_relation"], out.get("warnings")
        assert UNDECLARED_NAME in out["warnings"][0]["message"]
        assert out.get("id"), "the write must proceed (warn-not-block, #1934)"

    def test_retired_spelling_and_unknown_name_still_warn(self, sdk, pair):
        a, b = pair
        for label in ("opposes", "does_not_support"):
            out = sdk.create_operator("IMPL", a, [b], label=label)
            assert self._codes(out) == ["undeclared_relation"], (label, out)

    def test_mitigates_is_still_not_an_operator_label(self, sdk, pair):
        # #4937: MITIGATES is a capability, not a predicate — it must not ride
        # the widened vocabulary back into the menu.
        a, b = pair
        out = sdk.create_operator("IMPL", a, [b], label="MITIGATES")
        assert self._codes(out) == ["undeclared_relation"], out.get("warnings")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
