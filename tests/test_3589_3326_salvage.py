"""#3589 — the ONE live discovery salvaged from closed PR #3326.

PR #3326 ("journaled Object retraction", implementing #2977) was **closed
unmerged** on 2026-09-15. Issue #3589 asks for its *knowledge* to be salvaged:
classify its tests (A) identity-independent / (B) identity-dependent / (C)
production code, port the (A) tests, harvest the (B) scenarios, and record a
deliberate verdict for each — *"expected — model changed"* vs *"unexpected —
real regression"*.

The full inventory (all 76 tests, one scenario per line, with its disposition)
is `docs/evidence/3589-3326-salvage/README.md`. That inventory's re-measurement
conclusion is:

* **(A) — nothing to port.** Every test in the PR asserts at least one member
  of the API the PR would have introduced (``ObjectRetracted``,
  ``_fold_object_retracted``, ``_derived_escape``, ``OBJECT_STATUS_VALUES``,
  ``apply_replay``'s 3-tuple, ``_flush_object_folds``). That API is not on
  ``main`` and never was — ``_delete_entity`` journals ``EntityMutated
  op="delete"`` and replay HARD-deletes (#3299 / PR #3696), because ontology §5
  says *delete hard-deletes, retract tombstones*. So there is no "new model"
  for these tests to be ported onto: their subject was replaced, not renamed.
* **(C) — discarded.** The fold heuristics compensated for an incomplete
  journal; #5048 replaced the premise by making the journal complete
  (CLOSED COMPLETED 2026-10-12). The salvage carries none of them.
* **(B) — one real regression, still live on main.** Pinned here.

THE LIVE SHAPE (#3389's writer half, via #2977's public-path repro).
``_connect_issue_objects`` keys its write on ``MERGE (o:Object {id:$oid})``. A
session transcript that quotes the canonical derived id of an Object
(``obj-<sha26(name)>``) while a LIVE Object of that name already exists under a
DIFFERENT id therefore mints a SECOND carrier of one logical entity — a node
replay materialises but name resolution can never disambiguate from the first.

Measured on ``main`` (2026-10-13, ``eebc5bad027393a5e6e5942cd7650b967699e5ec``,
embedded engine)::

    _create_entity('Object', 'ARBITRARY_ID', {name:'SHARED', ...})
    _connect_issue_objects(ev, {'issues': [{'id': 'obj-2c8f…', 'title': 'SHARED'}]})
    # carriers of name SHARED == [['ARBITRARY_ID', 'live'],
    #                             ['obj-2c8f61dbe32e30a50debc0b61b', None]]

Two nodes, one entity. The public ``delete_entity(derived)`` then returns
``True`` (it found — and destroyed — the second carrier) where the identity
model requires ``False`` (the id anchors no live node of its own).

OWNER: **#3590** — S2 owns the id-keyed/name-keyed coexistence shape, recorded
as the fixture vector ``mixed_id_and_name_reference`` in
``tests/fixtures/entity_identity_vectors.json``. ``#3389`` (the original writer
report) was closed *consolidated into #5048*, and #5048 is itself CLOSED
COMPLETED, so #3590 is the open owner. ``strict=True`` is mandatory: when the
fix lands this XPASSES and FAILS, so the pin cannot outlive its bug.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK, _entity_name_id


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#3589 salvage of PR #3326 (B)-case: `_connect_issue_objects` MERGEs on "
        "`{id:$oid}`, so a reference carrying the derived id of a name that "
        "already has a live holder under another id mints a SECOND carrier of "
        "one logical entity. Owner #3590/S2 (fixture vector "
        "`mixed_id_and_name_reference`). Remove this pin when the fix lands."
    ),
)
def test_derived_id_reference_does_not_mint_a_second_carrier(tmp_path):
    """A derived-looking id proves nothing about identity (#3389)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "connect.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        proj = sdk._get_proj()
        derived = _entity_name_id("Object", "SHARED")
        assert derived.startswith("obj-"), derived

        # 1) a journaled live carrier of SHARED under an ARBITRARY id
        sdk._create_entity("Object", "ARBITRARY_ID",
                           {"name": "SHARED", "objectKind": "core:other",
                            "status": "live"},
                           "ObjectRegistered")

        # 2) the session-indexing path references the DERIVED id of that name
        ev = sdk.create_event("AgentSession", eventKind="AgentSession",
                              session_id="s1")
        assert sdk._connect_issue_objects(
            ev["eventId"], {"issues": [{"id": derived, "title": "SHARED"}]}) == 1

        carriers = proj.g.query(
            "MATCH (o:Object {name:'SHARED'}) RETURN o.id").result_set
        assert carriers == [["ARBITRARY_ID"]], (
            "the reference minted a second carrier of one logical entity "
            f"(#3389): {carriers}")

        # 3) the public delete of the (never-created) derived id must anchor
        #    nothing and must leave the live carrier untouched
        assert sdk.delete_entity(derived) is False
        live = proj.g.query(
            "MATCH (o:Object {name:'SHARED'}) RETURN o.id, o.status").result_set
        assert live == [["ARBITRARY_ID", "live"]], (
            f"the delete destroyed a live Object of the same name: {live}")
    finally:
        sdk.close()
