"""#6131 — the operator-mutation vocabulary is discoverable, and the verbs work.

The defect was a FALSE COMPLETENESS CLAIM, not a missing capability.

* ``operator_action``'s refusal read *"must be 'mitigate' or 'annotate'"*, which
  states a closed set. It is not closed: operators are also edited and removed by
  the generic verbs, because **an operator IS a Point** (``is_operator=true``) and
  so takes ``update()``/``delete()``'s Point arm.
* ``update()`` and ``delete()`` describe themselves only as Point/entity verbs and
  never name operators, so every discoverable signal pointed away from the verb
  that works.

The consequence was observed, not hypothesised: in a live session the conclusion
*"the SDK cannot delete an operator; a mislabelled edge is permanent"* was reached
and reported — and it was WRONG.

Why the assertions are shaped this way — every test is written to be able to fail:

1. the refusal must no longer claim to be the whole vocabulary (reverting the
   message reddens it);
2. the capability must be real and pinned by BEHAVIOUR — an operator can be
   re-labelled in place and deleted, and a plain Point still deletes through the
   same verb, so neither assertion is vacuous (routing operators away from the
   Point arm reddens both, and dropping the control would let a "delete
   everything" regression pass);
3. the prose at the point of use is asserted on purpose. For a DISCOVERABILITY
   defect the docstring **is** the artifact, so prose that drifts back to hiding
   operators is precisely the regression this file exists to catch.

Runnable with: `.venv/bin/python -m pytest tests/test_6131_operator_mutation_vocabulary.py -v`
The fixture is a per-test embedded DB (``db_path``), so it needs no running server.
"""
from __future__ import annotations

from pathlib import Path

from tortoise.sdk import TortoiseSDK


def _sdk(tmp_path: Path) -> TortoiseSDK:
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _operator(sdk: TortoiseSDK) -> str:
    """Two Points and the operator bridging them; returns the operator id."""
    a = sdk.create_point("evidence", "6131 probe point A")
    b = sdk.create_point("evidence", "6131 probe point B")
    op = sdk.create_operator("IMPL", source_id=a["id"], target_ids=[b["id"]])
    assert op["is_operator"] is True, op
    return op["id"]


def test_operator_is_a_point_so_update_relabels_it_in_place(tmp_path: Path) -> None:
    """The capability the report concluded was missing (#6131, part 2)."""
    sdk = _sdk(tmp_path)
    op_id = _operator(sdk)

    out = sdk.update(op_id, label="competesWith")

    # It stays an OPERATOR — i.e. it took the Point arm, not the entity arm.
    assert out.get("is_operator") is True, out
    assert out.get("op_type") == "IMPL", out
    assert out.get("label") == "competesWith", out
    # ...and the edit is durable, not just echoed back.
    assert (sdk.get_point(op_id) or {}).get("label") == "competesWith"


def test_operator_is_deletable_through_the_generic_verb(tmp_path: Path) -> None:
    """The specific claim that was false: a mislabelled edge is not permanent."""
    sdk = _sdk(tmp_path)
    op_id = _operator(sdk)

    assert sdk.delete(op_id) is True
    assert not sdk.get_point(op_id), "the operator survived delete()"


def test_control_a_plain_point_still_deletes_through_the_same_verb(tmp_path: Path) -> None:
    """Control: makes the operator claims non-vacuous — the verb was not narrowed."""
    sdk = _sdk(tmp_path)
    a = sdk.create_point("evidence", "6131 plain control point")

    assert sdk.delete(a["id"]) is True
    assert not sdk.get_point(a["id"])


