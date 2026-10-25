"""#9963 — the MECHANISM leg of the three-layer relation model (#7852) at the
`create_operator` write door.

The model is one triple (ONTOLOGY §8, #7852): **mechanism** (`IMPL` / `NAND` —
how it acts), **semantics** (what it means) and **direction** (which way it
runs). Before this file the door checked two of the three legs:

  * `label`/`semantics` MEMBERSHIP (`undeclared_relation`, #1934/#7551), and
  * the semantics → DIRECTION resolution (`direction_basis='semantics'`).

and never compared `op_type` to the mechanism the write's own meaning (or the
pack relation it names) DECLARES — even though the registry rows it already
reads carry `mechanism` (measured: `{'predicate': 'groundedIn', 'mechanism':
'IMPL', 'semantics': 'supports', …}`) and `SEMANTICS_MECHANISMS` states the
polarity-bearing meanings. So the one combination that INVERTS the logic —
"this evidence REFUTES that claim", spelled `op_type='IMPL'` — was accepted in
silence, EP raised the target's confidence, and the node recorded
`direction_basis: 'semantics'` (i.e. the declared meaning was honoured).

⛔ CONTRACT: the new warning is WARN-NOT-BLOCK. #1934's ruling (governance
D1/D2) is a recorded decision and this file pins it: every mismatched write
below must still be WRITTEN, with its `op_type` stored verbatim, and must
return a structured warning. A refusal here is a regression, not a fix.

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_mechanism_write_path_9963.py -q
"""
from __future__ import annotations

import pytest

from tortoise.pack_registry import (
    CORE_PREDICATES,
    SEMANTICS_MECHANISMS,
    SEMANTICS_VALUES,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "mechanism.db"))
    yield s
    s.close()


def _two_points(sdk):
    return (
        sdk.create_point("statement", "the migration finished")["id"],
        sdk.create_point("statement", "the migration did not finish")["id"],
    )


def _mechanism_warnings(op: dict) -> list[dict]:
    return [w for w in op.get("warnings") or [] if w.get("code") == "mechanism_mismatch"]


def _stored_op_type(sdk, op_id: str) -> str:
    return sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.op_type", params={"id": op_id}
    ).result_set[0][0]


# ── the vocabulary invariant (no graph needed) ───────────────────────────

def test_mechanism_map_is_ontology_s8s_table():
    """The map IS ONTOLOGY §8's semantics table, mechanism column — the ruled
    table (#7852), not a derivation. Pinned verbatim so a vocabulary move
    cannot leave the map behind, and so the four `—` rows stay explicitly
    mechanism-free instead of silently acquiring one."""
    assert SEMANTICS_MECHANISMS == {
        "addresses": "IMPL",
        "causes": "IMPL",
        "dependsOn": "IMPL",
        "hasPart": "IMPL",
        "supports": "IMPL",
        "mutuallySupportive": "IMPL",
        "refutes": "NAND",
        "mutuallyExclusive": "NAND",
    }
    assert set(SEMANTICS_MECHANISMS) <= SEMANTICS_VALUES
    assert set(SEMANTICS_MECHANISMS.values()) <= CORE_PREDICATES
    # ONTOLOGY §8 gives these NO mechanism (`—`): structural/neutral meanings,
    # over which either mechanism may be spelled.
    assert SEMANTICS_VALUES - set(SEMANTICS_MECHANISMS) == {
        "related", "transacts", "ownedBy", "managedBy",
    }


# ── the named test from #9963 ─────────────────────────────────────────────

def test_semantics_refutes_on_impl_is_refused_or_warned(sdk):
    """The measured defect, verbatim: a write declaring `refutes` with the
    mechanism `IMPL` must not be silent (warn-not-block, per #1934)."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="refutes", semantics="refutes")
    assert op.get("warnings")


# ── the mechanism leg: meaning → mechanism ────────────────────────────────

def test_semantics_refutes_on_impl_names_the_declared_mechanism(sdk):
    """The warning is SPECIFIC: it names the mechanism the meaning declares,
    the mechanism the caller passed, and where the vocabulary lives — a bare
    "something is wrong" leaves the caller nothing to act on."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], semantics="refutes")
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    message = warns[0]["message"]
    assert "'NAND'" in message, message          # what the meaning declares
    assert "'IMPL'" in message, message          # what the caller wrote
    assert "SEMANTICS_MECHANISMS" in message, message  # where the vocabulary lives
    assert "warn-not-block" in message, message


def test_label_spelling_is_compared_too(sdk):
    """The mirror spelling from the issue: `label='refutes'` with NO
    `semantics`. The label is a Layer-2 value the core ontology declares, so
    the same comparison applies — and the direction taken from a contradictory
    `semantics` must not hide it."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="refutes", semantics="supports")
    assert _mechanism_warnings(op), op.get("warnings")


def test_pack_declared_relation_mechanism_is_compared(sdk):
    """A pack-declared predicate carries a declared `mechanism` — the datum the
    registry rows already hold. `competesWith` (product-strategy) declares NAND,
    so an IMPL write of it is the same logic inversion."""
    a, b = _two_points(sdk)
    op = sdk.create_operator("IMPL", a, [b], label="competesWith")
    warns = _mechanism_warnings(op)
    assert warns, op.get("warnings")
    assert "NAND" in warns[0]["message"], warns[0]["message"]


def test_agreeing_mechanism_does_not_warn(sdk):
    """Discrimination: a write whose mechanism AGREES with its declared meaning
    (`supports` → IMPL, a pack relation whose declared mechanism is IMPL) stays
    silent. A warning on every write carries no information (#7551's lesson)."""
    a, b = _two_points(sdk)
    for kwargs in ({"semantics": "supports"}, {"label": "supports"},
                   {"label": "fixes"}, {"label": "causes"}):
        op = sdk.create_operator("IMPL", a, [b], **kwargs)
        assert not _mechanism_warnings(op), (kwargs, op.get("warnings"))


def test_neutral_meaning_does_not_pin_a_mechanism(sdk):
    """The FOUR meanings ONTOLOGY §8 gives no mechanism (`related`,
    `transacts`, `ownedBy`, `managedBy`) must never be reported: they are
    structural/neutral, so neither spelling contradicts them. Reporting one
    would invent an ontology ruling the model never made."""
    a, b = _two_points(sdk)
    for semantics in ("related", "transacts", "ownedBy", "managedBy"):
        assert semantics not in SEMANTICS_MECHANISMS, semantics
        for op_type in ("IMPL", "NAND"):
            op = sdk.create_operator(op_type, a, [b], semantics=semantics)
            assert not _mechanism_warnings(op), (op_type, semantics, op["warnings"])


def test_a_structural_meaning_still_declares_its_mechanism(sdk):
    """Discrimination against a polarity-only reading of the defect: ONTOLOGY
    §8 gives `hasPart`/`addresses`/`dependsOn` IMPL too, so a NAND write of one
    is the same inversion and must be reported. Without this, an implementation
    that only mapped `supports`/`refutes` would pass the tests above."""
    a, b = _two_points(sdk)
    for semantics in ("hasPart", "addresses", "dependsOn", "causes"):
        op = sdk.create_operator("NAND", a, [b], semantics=semantics)
        assert _mechanism_warnings(op), (semantics, op.get("warnings"))
        # ... and the agreeing direction stays silent.
        op2 = sdk.create_operator("IMPL", a, [b], semantics=semantics)
        assert not _mechanism_warnings(op2), (semantics, op2.get("warnings"))


# ── the #1934 contract: WARN-NOT-BLOCK ───────────────────────────────────

@pytest.mark.parametrize("op_type,declared", [("IMPL", "NAND"), ("NAND", "IMPL")])
def test_mismatch_still_writes(sdk, op_type, declared):
    """#1934 is a RECORDED DECISION: warn-not-block. The write must land with
    the caller's `op_type` stored VERBATIM — the warning is the disclosure, not
    a refusal (#9963 explicitly does not convert this into a hard refusal)."""
    a, b = _two_points(sdk)
    semantics = "refutes" if declared == "NAND" else "supports"
    op = sdk.create_operator(op_type, a, [b], semantics=semantics)
    assert op.get("id"), "warn-not-block: the write must proceed"
    assert _stored_op_type(sdk, op["id"]) == op_type
    assert _mechanism_warnings(op), op.get("warnings")
