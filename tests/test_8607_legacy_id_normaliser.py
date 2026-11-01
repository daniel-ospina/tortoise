"""#8607 (W1b) — the legacy-id census, the plan, and the count invariant.

The pass this file pins answers one question mechanically: **which ids on the
snapshot are not on the canonical contract, and does renaming exactly those
ones, exactly once, leave the snapshot whole?** Every test here is an INVARIANT
over that partition — the thing #8607's own indicator (1) asks for ("the same
node count resolves before/after") and (2) ("a second run changes nothing").

The fixture reproduces the population `STORAGE-ARCHITECTURE.md` §17.5 measured
on the live graph on 2026-10-08 — 8,700 ``<hex>-<hex>`` + 1,000 ``p-NNNNNNN``
+ ~30 ``EV-*`` — because the point of the census is the SPLIT, not the total:

* ``<hex>-<hex12>`` is what ``tortoise/ids.py::ulid()`` mints (``sdk._ULID_RE``),
  so those 8,700 are **already canonical**;
* ``p-NNNNNNN`` (``:Point``) is the population this pass renames;
* ``EV-*`` sits on ``:Event``, which is **declared out of scope** — `eventId`
  has no sanctioned re-keying path and `id` mirrors it (see
  ``DECLARED_OUT_OF_SCOPE``).

The fixture also carries what a REAL ``dump_graph`` artifact carries and a
contract-only fixture would miss: ``:Session`` (caller-supplied id),
``:GraphEvent`` and ``Meta`` (labels the record's key contract does not key at
all), and a shape-mix group that is *built to no regex* so the frame-correction
assertion binds to the contract predicate rather than to its own generator.

Pure functions over a plain dict: no graph, no database, no clock. Registered
in `core` (docker fast legs) — **not** the embedded carve-out: every input is a
`tortoise-logical-dump-v1` dict built in-process, so there is no URI, no graph
and no fixture to place.

⚠️ **`TORTOISE_TEST_CARVE_OUT=1` is needed to RUN it locally**, and that is not a
membership claim: the flag is the session-wide "no URI" opt-in (`epic #1647`), while
`config/ci-surfaces.yml`'s `carve_out:` list is a per-file routing list this file is NOT on
— the embedded carve-out leg runs exactly that list.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.legacy_ids import (
    CANONICAL,
    DECLARED_OUT_OF_SCOPE,
    LEGACY,
    OUT_OF_SCOPE,
    REKEY_SCOPE,
    UNCONTRACTED,
    LegacyIdError,
    apply_plan,
    build_plan,
    canonical_id,
    census,
    is_canonical,
    shape,
)

# The measured populations (§17.5, live graph, 2026-10-08).
MINTED = 8_700
P_IDS = 1_000
EV_IDS = 30
POINT_TOTAL = MINTED + P_IDS
IN_SCOPE = POINT_TOTAL + 2  # + the two shape-mix Points


def _node(dump_id: int, labels: list[str], **props) -> dict:
    return {"dump_id": dump_id, "labels": labels, "props": props}


def _minted_id(i: int) -> str:
    """A ``:Point`` id in the form ``ulid()`` mints: ``<hex>-<hex12>``."""
    return f"{i + 0x1000:x}-{i:012x}"


def _p_id(i: int) -> str:
    """The legacy sweep format — no current minter in the repo."""
    return f"p-{i:07d}"


def _ev_id(i: int) -> str:
    """A legacy Event id string."""
    return f"EV-2026-{i:04d}"


def _dump() -> dict:
    """The measured population, plus every key class the real dump carries.

    The canonical ``<hex>-<hex>`` group carries the LARGEST count on purpose:
    it is the group the issue's framing calls legacy, and it is not.
    """
    nodes: list[dict] = []
    next_id = 1
    for i in range(MINTED):
        nodes.append(_node(next_id, ["Point"], id=_minted_id(i), content=f"c{i}"))
        next_id += 1
    for i in range(P_IDS):
        nodes.append(_node(next_id, ["Point"], id=_p_id(i), content=f"legacy {i}"))
        next_id += 1
    for i in range(EV_IDS):
        nodes.append(_node(next_id, ["Event"], eventId=_ev_id(i), id=_ev_id(i), name=f"ev {i}"))
        next_id += 1
    # Declared out of scope: name/url-keyed labels and a caller-supplied id.
    nodes.append(_node(next_id, ["Object"], name="acme"))
    next_id += 1
    nodes.append(_node(next_id, ["Subject"], name="alice"))
    next_id += 1
    nodes.append(_node(next_id, ["Source"], url="https://x.test/a"))
    next_id += 1
    nodes.append(_node(next_id, ["Session"], id="session_9f8a7b6c5d4e"))
    next_id += 1
    # Labels the record's key contract does not key at all — a real dump has
    # both of these (`_EXPORT_SKIP_LABELS` keeps :GraphEvent; the calibration
    # Meta key is exported as DATA).
    nodes.append(_node(next_id, ["GraphEvent"], seq=1, event_id="ev-1"))
    next_id += 1
    nodes.append(_node(next_id, ["Meta"], key="calibration_milestone"))
    next_id += 1
    # Shape mix, built to NO regex: a longer hex half and a short one are both
    # off the mint format and must land in `legacy`.
    nodes.append(_node(next_id, ["Point"], id="1a121bfbe39-5a056891dd27aa"))
    next_id += 1
    nodes.append(_node(next_id, ["Point"], id="1a121bfbe39-5a056891dd2"))
    next_id += 1
    edges = [
        {"src": 1, "dst": MINTED + 1, "type": "IMPL"},
        {"src": 1, "dst": POINT_TOTAL - EV_IDS + 1, "type": "NAND"},
        {"src": MINTED + 1, "dst": POINT_TOTAL + 1, "type": "aboutObject"},
    ]
    return {
        "format": "tortoise-logical-dump-v1",
        "dump_revision": 2,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def _renamed_population() -> int:
    """``p-NNNNNNN`` + the two shape-mix Points."""
    return P_IDS + 2


# ── the census: the split, not the carried total ────────────────────────────


def test_the_minted_hex_pair_group_is_canonical_not_legacy():
    """The frame correction, pinned. ``<hex>-<hex12>`` IS the mint format."""
    assert shape(_minted_id(0)) == "minted-ulid"
    assert is_canonical(_minted_id(0))
    # …and the genuinely off-contract group is not.
    assert shape(_p_id(0)) == LEGACY
    assert not is_canonical(_p_id(0))


def test_shape_mix_is_off_the_mint_format():
    # Built to no regex: only the exact ``<hex>-<hex12>`` spelling is minted.
    assert shape("1a121bfbe39-5a056891dd27aa") == LEGACY  # 14-hex half
    assert shape("1a121bfbe39-5a056891dd2") == LEGACY  # 11-hex half
    assert not is_canonical("1a121bfbe39-5a056891dd27aa")


def test_census_counts_the_measured_population():
    found = census(_dump())
    assert found.nodes == POINT_TOTAL + EV_IDS + 8
    # in scope = the :Point population only (the declared REKEY_SCOPE)
    assert found.in_scope == IN_SCOPE
    assert found.canonical == MINTED
    assert found.legacy == _renamed_population(), (
        "only the p-NNNNNNN group and the off-format Points need a rename; the "
        "8,700 <hex>-<hex> ids are the minted form and EV-* is out of scope"
    )
    assert found.legacy != 9_700, "the carried ~9,700 is not the measured population"
    assert found.canonical_shapes == {"minted-ulid": MINTED}
    assert found.legacy_shapes == {LEGACY: _renamed_population()}
    assert found.out_of_scope == {
        "Event": EV_IDS,
        "Object": 1,
        "Session": 1,
        "Source": 1,
        "Subject": 1,
    }
    assert found.uncontracted == {"GraphEvent": 1, "Meta": 1}
    assert found.by_label["Point"] == {CANONICAL: MINTED, LEGACY: _renamed_population()}
    assert found.by_label[UNCONTRACTED] == {OUT_OF_SCOPE: 2}


def test_the_scope_is_a_declared_label_map_not_a_property_name():
    """`:Session` and `:Event` must never be re-keyed by this pass."""
    assert set(REKEY_SCOPE) == {"Point"}
    assert set(DECLARED_OUT_OF_SCOPE) == {"Event", "Session", "Subject", "Object", "Source"}
    for label in ("Event", "Session"):
        assert DECLARED_OUT_OF_SCOPE[label].strip()
    plan = build_plan(_dump())
    assert {r.label for r in plan.renames} == {"Point"}


# ── the partition: every legacy id mapped exactly once ──────────────────────


def test_every_legacy_id_is_mapped_exactly_once():
    dump = _dump()
    plan = build_plan(dump)
    # Cross-checked against the module's OWN other reader — a plan and a census
    # that disagree is the defect class this assertion exists for.
    assert len(plan.renames) == census(dump).legacy
    legacy = {
        (node["labels"][0], node["props"]["id"])
        for node in dump["nodes"]
        if node["labels"] == ["Point"] and not is_canonical(node["props"]["id"])
    }
    assert len(legacy) == _renamed_population()
    assert {(r.label, r.old) for r in plan.renames} == legacy, (
        "the plan's (label, old) set must be exactly the legacy set — no node "
        "skipped, no canonical node dragged in"
    )
    assert len({r.dump_id for r in plan.renames}) == len(plan.renames), "one node, one rename"
    assert len({(r.label, r.new) for r in plan.renames}) == len(plan.renames), (
        "the mapping must be single-valued: two nodes onto one key is a MERGE"
    )
    existing = {
        (node["labels"][0], node["props"].get("id"))
        for node in dump["nodes"]
        if node["labels"] == ["Point"]
    }
    assert not ({(r.label, r.new) for r in plan.renames} & existing), "no target may already exist"
    for rename in plan.renames:
        assert is_canonical(rename.new), rename
        assert rename.new == canonical_id(rename.label, rename.old), (
            "the target is recomputable — the existing canonical minter, no new scheme"
        )


def test_the_target_is_the_existing_canonical_minter():
    # §1.4 rule 1: the rewrite renames the node's key VALUE; it does not mint a
    # second identity space. The value is pinned as a FROZEN LITERAL — the
    # retired entity minter's spelling (`label[:3]-<sha256[:26]>`, #3590 retired
    # it as an ENTITY id and `legacy_ids._derived_legacy_handle` kept it for
    # this migration only). The literal is the point: a change to the
    # derivation would silently re-target every existing plan.
    assert canonical_id("Point", "p-0000438") == "poi-0eab7bc5fcd2b21f5afc10a7a9"
    assert canonical_id("Point", "p-0000438") != canonical_id("Object", "p-0000438")


def test_a_normalised_point_key_is_an_entity_handle_not_a_minted_ulid():
    """⚠️ The DECISION this pass makes, pinned where it can be seen.

    The target is ``legacy_ids._derived_legacy_handle``'s — ``poi-<sha256[:26]>`` — which is
    NOT the ``<ts-hex>-<uuid12>`` shape ``create_point`` mints (and warns about
    when it is handed anything else). A normalised ``:Point`` key space is
    therefore the entity-handle spelling, and the label carries BOTH canonical
    shapes after a pass. Deliberate, and stated in the module docstring and
    §1.4: a recomputable target is what makes the plan reviewable, verifiable
    and reversible, and ``ulid()`` is random — so there is no deterministic
    minted-shape target to aim at instead.
    """
    from tortoise.sdk import _is_ulid

    new = canonical_id("Point", "p-0000438")
    assert new.startswith("poi-"), new
    assert not _is_ulid(new), "the entity handle is NOT the created-point mint format"
    # …and it is canonical anyway, via the label-agnostic union `is_canonical`
    # uses — which is exactly what makes a second run a no-op.
    assert shape(new) == "entity-handle"
    assert is_canonical(new)


def test_a_pass_leaves_two_canonical_shapes_on_point_and_says_so():
    """The module's own instrument must REPORT the split, not hide it.

    ``census(canonical_shapes)`` is the reading a lane quotes; before this
    assertion the only shape assertion was over the INPUT. A pass that silently
    changed the :Point key space from one canonical shape to two would leave the
    instrument reading ``{"minted-ulid": 8700}`` and never say so.
    """
    dump = _dump()
    out, _ = apply_plan(dump, build_plan(dump))
    after = census(out)
    assert after.canonical_shapes == {
        "minted-ulid": MINTED,
        "entity-handle": _renamed_population(),
    }, after.canonical_shapes
    assert after.legacy == 0
    assert after.legacy_shapes == {}


# ── applying it: the counts reconcile, and nothing else moves ──────────────


def test_counts_reconcile_and_edges_survive():
    dump = _dump()
    # Snapshotted BEFORE the pass, and this is not ceremony: `apply_plan` builds
    # `out` as `{**dump, ...}` and appends the SAME edge list object, so
    # comparing `out["edges"] == dump["edges"]` afterwards is vacuous — the
    # assertion stays green even if `apply_plan` mutated every row in place.
    # The node half has the same trap: every node the pass does not rename is
    # appended BY REFERENCE (`out_nodes.append(node)`), so `after == before`
    # over `dump["nodes"]` is reflexivity and cannot fire. Both snapshots are
    # therefore deep copies taken before the pass (found in review of
    # `fix/8607-legacy-id-normaliser`, "unfalsifiable assertion").
    edges_before = copy.deepcopy(dump["edges"])
    nodes_before = copy.deepcopy(dump["nodes"])
    plan = build_plan(dump)
    out, report = apply_plan(dump, plan)

    assert report["nodes_in"] == report["nodes_out"] == len(dump["nodes"])
    assert report["in_scope_in"] == report["in_scope_out"] == IN_SCOPE
    assert report["renamed"] == _renamed_population()
    assert report["legacy_remaining"] == 0
    assert report["legacy_before"] == _renamed_population()
    assert report["canonical_before"] == MINTED
    assert report["canonical_after"] == IN_SCOPE, "every in-scope id is canonical after"
    assert report["renamed"] + report["canonical_before"] == report["in_scope_in"], (
        "the ledger's two halves must account for every in-scope node"
    )
    assert out["edges"] == edges_before, "a key rename must not touch edges"
    assert out["node_count"] == dump["node_count"]
    assert [n["dump_id"] for n in out["nodes"]] == [n["dump_id"] for n in dump["nodes"]], (
        "dump_id is the store's internal id and is preserved — that is WHY the edges survive"
    )
    for before, after in zip(nodes_before, out["nodes"], strict=True):
        if before["labels"] != ["Point"] or is_canonical(before["props"]["id"]):
            assert after == before, f"untouched node changed: {before}"
    assert census(out).legacy == 0


def test_a_second_run_changes_nothing():
    dump = _dump()
    out, _ = apply_plan(dump, build_plan(dump))
    again = build_plan(out)
    assert again.is_empty, "#8607 indicator 2 — the pass must be a fixed point"
    assert again.renames == ()
    out2, report2 = apply_plan(out, again)
    assert out2 == out
    assert report2["renamed"] == 0


def test_the_pass_is_reversible():
    dump = _dump()
    plan = build_plan(dump)
    out, _ = apply_plan(dump, plan)
    restored, report = apply_plan(out, plan, revert=True)
    assert report["mode"] == "revert"
    assert restored == dump, "reverting must reproduce the original artifact, not a lookalike"
    assert census(restored).legacy == _renamed_population()


# ── the real dump: nodes this pass has no business refusing ────────────────


def test_a_graph_event_and_a_meta_node_are_counted_not_refused():
    """A real `dump_graph` artifact carries both; refusing makes the tool unusable."""
    dump = _dump()
    dump["nodes"].append(_node(90_001, ["GraphEvent"], seq=42, event_id="ev-42"))
    dump["nodes"].append(_node(90_002, ["Meta"], key="calibration_milestone"))
    dump["node_count"] = len(dump["nodes"])
    plan = build_plan(dump)  # must not raise
    assert census(dump).uncontracted == {"GraphEvent": 2, "Meta": 2}
    out, _ = apply_plan(dump, plan)
    assert next(n for n in out["nodes"] if n["dump_id"] == 90_001)["props"]["event_id"] == "ev-42"


def test_a_session_id_is_counted_and_never_rewritten():
    """`:Session.id` is caller-supplied (`session_<hex12>`) — §1.4: opaque and valid."""
    dump = _dump()
    out, _ = apply_plan(dump, build_plan(dump))
    session = next(n for n in out["nodes"] if n["labels"] == ["Session"])
    assert session["props"]["id"] == "session_9f8a7b6c5d4e"
    assert census(dump).out_of_scope["Session"] == 1


def test_an_event_id_is_counted_and_never_rewritten():
    """`eventId` has no sanctioned re-keying path, and `id` mirrors it."""
    dump = _dump()
    out, _ = apply_plan(dump, build_plan(dump))
    events = [n for n in out["nodes"] if n["labels"] == ["Event"]]
    assert len(events) == EV_IDS
    for node in events:
        assert node["props"]["eventId"] == node["props"]["id"], "the mirror must not diverge"
        assert node["props"]["eventId"].startswith("EV-")


# ── refusals: every one of them is a merge or a hole, fail closed ──────────


def test_two_nodes_sharing_one_legacy_id_are_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_000, ["Point"], id="p-0000001", content="same key, other node"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("duplicate legacy identity" in p for p in exc.value.problems)


def test_a_target_collision_is_refused():
    """A rename may not land on an id another node already carries."""
    dump = _dump()
    collision = canonical_id("Point", "p-0000007")
    dump["nodes"].append(_node(10_001, ["Point"], id=collision, content="squatter"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("target collision" in p for p in exc.value.problems), exc.value.problems


def test_a_multi_label_node_is_in_the_collision_universe():
    """⛔ A ``:Point``+``:Session`` node is skipped by the SCOPE branch, but its
    ``id`` is still a ``:Point`` key — a rename that lands on it merges two
    nodes. Measured before the fix: this dump was ACCEPTED by ``build_plan`` and
    ``apply_plan`` left both nodes carrying the same ``id``.

    A real ``dump_graph`` artifact carries multi-label nodes, so this is the
    population the live pass would meet, not a fabricated edge case.
    """
    dump = _dump()
    collision = canonical_id("Point", "p-0000001")
    # The picked label is the out-of-scope :Session, so the scope branch skips
    # this node entirely — before the fix, so did the collision universe.
    dump["nodes"].append(
        _node(10_003, ["Point", "Session"], id=collision, sessionId="session_abc123def456")
    )
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("target collision" in p for p in exc.value.problems), exc.value.problems


def test_a_preexisting_duplicate_on_an_out_of_scope_label_does_not_block_the_pass():
    """A duplicate `:Session` id cannot make a RENAME ambiguous, so it is not a refusal.

    Only a REKEYED label can be a rename target, so refusing here would be a
    false block: a real dump that happens to carry two nodes under one
    `:Session` key would refuse the whole pass for a collision it cannot cause.
    The duplicate is still registered in the collision universe.
    """
    dump = _dump()
    session_id = "session_dup_4a3b2c1d0e9f"
    dump["nodes"].append(_node(10_008, ["Session"], id=session_id, sessionId=session_id))
    dump["nodes"].append(_node(10_009, ["Session"], id=session_id, sessionId=session_id))
    dump["node_count"] = len(dump["nodes"])
    plan = build_plan(dump)  # must not raise
    assert len(plan.renames) == census(dump).legacy, "the in-scope renames still plan"


def test_a_duplicate_the_rename_resolves_is_not_a_refusal():
    """A shared key the plan RESOLVES must not be refused as if it were unfixable.

    ``[Point+Session{id: p-0000005}, Point{id: p-0000005}]`` shares a ``:Point``
    key — but the plain ``:Point`` is a rename candidate, so after the pass the
    two nodes carry ``p-0000005`` and ``poi-<hash>`` and NO key is shared. The
    refusal must be raised only when two holders SURVIVE the pass; raising it at
    registration time blocked a fully normalisable dump, which is the same
    over-block class as refusing on an out-of-scope label's duplicate.
    """
    # A MINIMAL dump: the base fixture already carries every `p-0000NNN` id, so
    # appending one of those would test the fixture's own duplicate, not this.
    dump = _dump()
    dump["nodes"] = [
        _node(10_010, ["Point", "Session"], id="p-0000005"),
        _node(10_011, ["Point"], id="p-0000005"),
    ]
    dump["edges"] = []
    dump["node_count"] = 2
    dump["edge_count"] = 0
    for field in ("source_node_count", "source_edge_count", "read_edge_count"):
        if field in dump:
            dump[field] = 2 if "node" in field else 0
    plan = build_plan(dump)  # must not raise
    out, _ = apply_plan(dump, plan)
    seen: dict[tuple[str, str], list[int]] = {}
    from tortoise.cache_projection import KEY_PROPERTY as _KP

    for node in out["nodes"]:
        for label in node["labels"]:
            kp = _KP.get(label)
            if kp and isinstance(node["props"].get(kp), str):
                seen.setdefault((label, node["props"][kp]), []).append(node["dump_id"])
    shared = {k: v for k, v in seen.items() if len(v) > 1}
    assert shared == {}, f"the pass left a shared key: {shared}"


def test_a_repeated_label_is_not_the_node_merging_with_itself():
    """``labels: ["Point", "Point"]`` is ONE node, not two holders of one key.

    Registering a holder per label OCCURRENCE made the survivor test see two
    holders and refuse the node as its own merge, with a message asserting two
    nodes that do not exist. `hosted_backup` dedupes `labels(n)` on the real
    export path, so this is a foreign-artifact guard.
    """
    dump = _dump()
    dump["nodes"] = [_node(1, ["Point", "Point"], id=_minted_id(0))]
    dump["edges"] = []
    dump["node_count"] = 1
    dump["edge_count"] = 0
    plan = build_plan(dump)  # must not raise
    assert len(plan.renames) == 0, "a canonical id is never a rename candidate"


def test_a_duplicate_dump_id_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(1, ["Point"], id="p-0000999", content="positional clash"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("carried by two nodes" in p for p in exc.value.problems)


def test_a_duplicate_canonical_identity_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_002, ["Point"], id=_minted_id(0), content="twin"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("duplicate identity" in p for p in exc.value.problems)


def test_an_in_scope_node_without_a_key_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_003, ["Point"], content="no id at all"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("no `id`" in p for p in exc.value.problems)


def test_a_co_label_cannot_suppress_a_missing_in_scope_key_in_either_order():
    """The same malformed node must be a problem whichever way its labels are ordered."""
    for labels in (["Object", "Point"], ["Point", "Object"]):
        dump = _dump()
        dump["nodes"].append(_node(10_005, labels, name="acme"))
        dump["node_count"] = len(dump["nodes"])
        with pytest.raises(LegacyIdError) as exc:
            build_plan(dump)
        assert any("no `id`" in p for p in exc.value.problems), (labels, exc.value.problems)


def test_an_ambiguous_multi_label_identity_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_004, ["Point", "Event"], id="p-0000999", eventId="EV-2026-9999"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("ambiguous" in p for p in exc.value.problems)


@pytest.mark.parametrize("labels", [["Point", "Session"], ["Session", "Point"]])
def test_a_shared_key_property_is_out_of_scope_in_both_label_orders(labels):
    """:Point and :Session share ONE key (`id`) — and that id is the SESSION's.

    Scope is decided over the label set, never by array order: `dump_graph`
    preserves FalkorDB's order, so a `claiming[0]` fallback would rename a
    caller-supplied `session_*` key in one order and skip it in the other.
    """
    dump = _dump()
    # A DISTINCT session id, for readability only: no refusal applies either way,
    # because a duplicate on an out-of-scope label is tolerated by design — see
    # `test_a_preexisting_duplicate_on_an_out_of_scope_label_does_not_block_the_pass`.
    dump["nodes"].append(_node(10_006, labels, id="session_7d6e5f4a3b2c"))
    dump["node_count"] = len(dump["nodes"])
    plan = build_plan(dump)  # must not raise
    assert [r for r in plan.renames if r.dump_id == 10_006] == [], (
        "a node carrying an out-of-scope label's key is never a rename candidate"
    )
    assert census(dump).out_of_scope["Session"] == 2
    out, _ = apply_plan(dump, plan)
    node = next(n for n in out["nodes"] if n["dump_id"] == 10_006)
    assert node["props"]["id"] == "session_7d6e5f4a3b2c", "the session key is untouched"


def test_a_padded_key_is_refused_rather_than_silently_trimmed():
    dump = _dump()
    dump["nodes"].append(_node(10_007, ["Point"], id=" p-0000888"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("surrounding whitespace" in p for p in exc.value.problems)


def test_a_truncated_snapshot_is_refused():
    dump = _dump()
    dump["node_count"] = len(dump["nodes"]) + 5  # a #5062 truncated export
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("truncated or corrupt" in p for p in exc.value.problems)


def test_a_non_list_edge_set_is_refused_not_read_as_empty():
    dump = _dump()
    dump.pop("edge_count")
    dump["edges"] = {"1": {"src": 1, "dst": 2}}
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("not a list" in p for p in exc.value.problems)


def test_a_stale_plan_is_refused_by_the_fingerprint():
    """``dump_id`` is positional — a refreshed snapshot reuses it.

    Without the binding, a plan built for snapshot A applies to snapshot B of
    the same size, rewrites a different node's key, and every count still
    reconciles (the failure mode ``retype``'s fingerprint exists for).
    """
    dump = _dump()
    plan = build_plan(dump)
    moved = _dump()
    moved["nodes"][2]["dump_id"] = 900_000
    with pytest.raises(LegacyIdError) as exc:
        apply_plan(moved, plan)
    assert any("different snapshot" in p for p in exc.value.problems)


def test_a_content_edit_does_not_invalidate_the_plan():
    # The narrowing, stated: the fingerprint binds identity, labels, key and the
    # edge list — the things a rename and a revert decide from. Reprojecting a
    # node's CONTENT is not a different snapshot.
    dump = _dump()
    plan = build_plan(dump)
    edited = _dump()
    edited["nodes"][5]["props"]["content"] = "re-extracted text"
    out, _ = apply_plan(edited, plan)
    assert census(out).legacy == 0


def test_an_edited_edge_list_is_refused_on_revert():
    """A revert must restore the original TOPOLOGY, not only the original keys."""
    dump = _dump()
    plan = build_plan(dump)
    out, _ = apply_plan(dump, plan)
    out["edges"][0]["type"] = "NAND"
    with pytest.raises(LegacyIdError) as exc:
        apply_plan(out, plan, revert=True)
    assert any("not the original artifact" in p for p in exc.value.problems)


# ── the CLI: the dry run writes nothing, and the artifact is required ──────


def test_cli_dry_run_writes_the_report_and_no_snapshot(tmp_path, capsys):
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    report_path = tmp_path / "report.json"
    rc = main(["--dump", str(dump_path), "--report", str(report_path)])
    assert rc == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert report["renamed"] == _renamed_population()
    assert report["legacy_remaining"] == _renamed_population(), "a dry run changes nothing"
    assert report["census"]["out_of_scope"]["Event"] == EV_IDS
    assert not (tmp_path / "out.json").exists()


def test_cli_apply_then_revert_round_trips(tmp_path):
    from tools.legacy_id_normalise import main

    dump = _dump()
    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(dump), encoding="utf-8")
    out_path = tmp_path / "out.json"
    report_path = tmp_path / "report.json"
    assert (
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(out_path),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    assert json.loads(out_path.read_text(encoding="utf-8")) != dump
    assert json.loads(report_path.read_text(encoding="utf-8"))["legacy_remaining"] == 0

    restored_path = tmp_path / "restored.json"
    assert (
        main(
            [
                "--dump",
                str(out_path),
                "--revert",
                "--report",
                str(report_path),
                "--out",
                str(restored_path),
            ]
        )
        == 0
    )
    assert json.loads(restored_path.read_text(encoding="utf-8")) == dump


def test_cli_refusal_exits_2_and_writes_nothing(tmp_path):
    from tools.legacy_id_normalise import main

    dump = _dump()
    dump["node_count"] = len(dump["nodes"]) + 5
    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(dump), encoding="utf-8")
    out_path = tmp_path / "out.json"
    rc = main(
        [
            "--dump",
            str(dump_path),
            "--apply",
            "--out",
            str(out_path),
            "--report",
            str(tmp_path / "r.json"),
        ]
    )
    assert rc == 2
    assert not out_path.exists()
    assert not (tmp_path / "r.json").exists()


def test_cli_refuses_an_in_place_run(tmp_path):
    """`--out == --dump` with a lost plan is unrecoverable: the old ids exist only in the plan."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(dump_path),
                "--report",
                str(tmp_path / "r.json"),
            ]
        )
    # The usage error propagates as SystemExit(<message>) — Python exits 1 — and
    # NOT as argparse's own 2, which this tool reserves for a refusal.
    assert "--out and --dump" in str(exc.value.code)


def test_cli_usage_errors_exit_1_so_2_means_only_refused(tmp_path):
    """`2` is the REFUSAL; a bad invocation must not be indistinguishable from it.

    `parser.error` — argparse's own path — exits 2, and both cases write
    nothing, so a caller keying on `2 == refused` treated a typo'd command line
    as a refused snapshot. The tool's own `_Parser` makes every usage error a
    usage/IO failure (exit 1).
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    usage_errors = [
        ["--dump", str(dump_path), "--apply"],  # --out and --report missing
        ["--dump", str(dump_path), "--apply", "--revert"],  # both modes at once
        ["--dump", str(dump_path), "--revert", "--out", str(tmp_path / "o.json")],  # no --report
        ["--nonsense"],  # unknown flag
    ]
    for argv in usage_errors:
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert isinstance(exc.value.code, str) and "error:" in exc.value.code, (
            argv,
            exc.value.code,
            "a usage error is SystemExit(<message>) -> exit 1, never the refusal's 2",
        )


def test_cli_exit_codes_are_distinguishable_at_the_process_level(tmp_path):
    """The documented contract, settled the way a caller sees it: `$?`.

    ``uv run python tools/legacy_id_normalise.py --dump x.json --apply; echo $?``
    answers 1 (usage), and a refused snapshot answers 2 — the two are told apart
    without reading stderr.
    """
    import subprocess

    tool = Path(__file__).resolve().parents[1] / "tools" / "legacy_id_normalise.py"
    good = tmp_path / "dump.json"
    good.write_text(json.dumps(_dump()), encoding="utf-8")
    truncated = tmp_path / "truncated.json"
    bad = _dump()
    bad["node_count"] = len(bad["nodes"]) + 5
    truncated.write_text(json.dumps(bad), encoding="utf-8")

    def run(argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(tool), *argv], capture_output=True, text=True)

    usage = run(["--dump", str(good), "--apply"])
    assert usage.returncode == 1, (usage.returncode, usage.stderr[-400:])
    assert "error:" in usage.stderr

    dry = run(["--dump", str(good)])
    assert dry.returncode == 0, (dry.returncode, dry.stderr[-400:])

    refusal = run(["--dump", str(truncated)])
    assert refusal.returncode == 2, (refusal.returncode, refusal.stderr[-400:])
    assert "REFUSED" in refusal.stderr

    missing = run(["--dump", str(tmp_path / "nope.json")])
    assert missing.returncode == 1, (missing.returncode, missing.stderr[-400:])
    assert "Traceback" not in missing.stderr


# ─────────────────────────── the ledger identity ───────────────────────────
def test_every_node_lands_in_exactly_one_bucket():
    """`nodes` must equal the sum of the parts — a remainder is not a count.

    #8607's indicator (1) is "the same node count resolves before/after"; that
    is only evidence if the census accounts for EVERY node. A malformed row, an
    ambiguous identity or an undeclared label that fell through the buckets would
    make the split look complete while a population went uncounted.
    """
    found = census(_dump())
    ledger = found.ledger()
    assert ledger["nodes"] == ledger["accounted"], ledger
    assert ledger["balanced"] is True
    assert found.in_scope == found.canonical + found.legacy


def test_the_ledger_holds_for_a_malformed_snapshot_too():
    dump = _dump()
    dump["nodes"].append("not a node")  # a row that is not an object at all
    dump["nodes"].append(_node(10_020, ["Point", "Event"], id="p-0000777", eventId="EV-2026-7777"))
    dump["nodes"].append(_node(10_021, ["Point"]))  # in scope, no key
    dump["node_count"] = len(dump["nodes"])
    found = census(dump)
    assert found.ledger()["balanced"] is True, found.ledger()
    assert sum(found.unresolved.values()) == 3
    with pytest.raises(LegacyIdError):
        build_plan(dump)


# ─────────────────── the scope maps are total over the contract ─────────────
def test_the_two_scope_maps_partition_the_contract_exactly():
    """Every keyed label has a recorded decision — none is "undeclared".

    Without this, a label could join `KEY_PROPERTY` and be silently absorbed
    into "out of scope" — attributed to a decision nobody made.
    """
    from tortoise.cache_projection import KEY_PROPERTY

    assert set(REKEY_SCOPE) | set(DECLARED_OUT_OF_SCOPE) == set(KEY_PROPERTY)
    assert not set(REKEY_SCOPE) & set(DECLARED_OUT_OF_SCOPE)


def test_a_keyed_label_with_no_decision_is_refused_not_absorbed(monkeypatch):
    import tortoise.legacy_ids as mod

    monkeypatch.setitem(mod.KEY_PROPERTY, "Widget", "widgetId")
    monkeypatch.setattr(mod, "REKEY_SCOPE", {"Point": next(iter(REKEY_SCOPE.values()))})
    dump = _dump()
    dump["nodes"].append(_node(10_030, ["Widget"], widgetId="w-1"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("neither REKEY_SCOPE nor DECLARED_OUT_OF_SCOPE" in p for p in exc.value.problems)
    assert census(dump).undeclared == {"Widget": 1}


# ──────────────────────────── CLI path collisions ──────────────────────────
@pytest.mark.parametrize(
    "target,other",
    [("--report", "--dump"), ("--report", "--out"), ("--out", "--dump")],
)
def test_cli_refuses_two_artifacts_on_one_path(tmp_path, target, other):
    """Three artifacts, three jobs — a collision destroys one of them silently."""
    from tools.legacy_id_normalise import main

    same = tmp_path / "same.json"
    same.write_text(json.dumps(_dump()), encoding="utf-8")
    rest = tmp_path / "rest.json"
    paths = {"--dump": rest, "--out": rest, "--report": rest}
    paths[other] = same
    paths[target] = same
    argv = ["--dump", str(paths["--dump"])]
    if "--out" in (target, other):
        argv += ["--apply", "--out", str(paths["--out"])]
    if "--report" in (target, other):
        argv += ["--report", str(paths["--report"])]
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert "error:" in str(exc.value.code), exc.value.code
    assert json.loads(same.read_text(encoding="utf-8"))["nodes"], "the dump survived"


def _fs_is_case_insensitive(path: Path) -> bool:
    """True when the filesystem behind ``path`` folds case to one file.

    The repo's own probe (`tests/test_guard_measured_revision.py:150`): APFS —
    where this repo's dev lanes run — folds ``Snapshot.json`` onto
    ``snapshot.json``, ext4 does not. A case-fold case that *assumes* one of the
    two behaviours is red on the other platform, which is exactly what happened
    here: the test asserted ``not report.exists()`` before reaching the refusal
    it exists to prove, so it failed on macOS before the rail was exercised.
    """
    probe = path / "CaseProbe.tmp"
    probe.write_text("x")
    try:
        return (path / "caseprobe.TMP").exists()
    finally:
        probe.unlink()


def test_cli_refuses_a_case_folded_alias_of_an_artifact_that_exists(tmp_path):
    """An EXISTING artifact against a case-folded alias of it — the pair the OLD gate skipped.

    The rail keyed an existing path as ``("inode", dev, ino)`` and a missing one
    as ``("path", text)``. Two tuples of different SHAPES are never equal, so for
    every pair where one side already existed — here the snapshot itself — the
    check could not fire whatever the two names were. On a case-insensitive
    filesystem ``--report Snapshot.json`` IS ``--dump snapshot.json``: the report
    replaces the snapshot the run was asked to read, and the plan the revert
    needs is the thing that overwrote it.

    Both filesystems run the SAME assertion — the refusal — and differ only in
    their precondition: on a folding filesystem the two names are already one
    file (so `samefile` is the precondition, not `not exists()`).
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "snapshot.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    report = tmp_path / "Snapshot.json"
    case_insensitive = _fs_is_case_insensitive(tmp_path)
    if case_insensitive:
        assert report.exists() and os.path.samefile(dump_path, report), (
            "a folding filesystem must resolve the two names to one file here"
        )
    else:
        assert not report.exists(), "case-sensitive here: the two names are two names"
    before = dump_path.read_bytes()
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path), "--report", str(report)])
    assert "are the same file" in str(exc.value.code)
    assert "--report and --dump" in str(exc.value.code)
    assert dump_path.read_bytes() == before, "the dump survived untouched"
    if not case_insensitive:
        assert not report.exists(), "nothing was written"


def test_cli_refuses_two_not_yet_created_artifacts_named_the_same_up_to_case(tmp_path):
    """The normal `--apply`: neither `--out` nor `--report` exists yet.

    Nothing on disk can settle whether the two names are one file, so the OLD
    gate compared them as resolved TEXT and passed. On a case-insensitive
    filesystem it then wrote the report first and truncated it with the
    snapshot — the plan that undoes the pass was destroyed, and the run printed
    "wrote report / wrote snapshot" and exited 0. Refused on a case-SENSITIVE
    filesystem too, on purpose: the same argv may be replayed where case folds,
    and a false refusal costs a rename while a false pass costs the plan.
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out_path = tmp_path / "normalised.json"
    report_path = tmp_path / "Normalised.json"
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(out_path),
                "--report",
                str(report_path),
            ]
        )
    assert "are the same file" in str(exc.value.code)
    assert "--report and --out" in str(exc.value.code)
    assert not out_path.exists() and not report_path.exists(), "nothing was written"


def test_cli_reports_a_usage_error_not_a_traceback_on_an_unreadable_dump(tmp_path):
    from tools.legacy_id_normalise import main

    (tmp_path / "adir").mkdir()
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(tmp_path / "adir")])
    # `SystemExit(<message>)` — Python prints it and exits 1: a usage/IO error,
    # not the traceback an unguarded `read_text` raises.
    assert "could not read dump" in str(exc.value.code)


def test_cli_refuses_a_report_row_that_is_not_a_rename(tmp_path):
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    (tmp_path / "r.json").write_text(
        json.dumps({"renames": [{"dump_id": "not-an-int"}], "dump_sha256": "x"}),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--dump",
                str(dump_path),
                "--revert",
                "--report",
                str(tmp_path / "r.json"),
                "--out",
                str(tmp_path / "o.json"),
            ]
        )
    assert "not a report this tool wrote" in str(exc.value.code)
    assert not (tmp_path / "o.json").exists()


def test_an_unserialisable_edge_value_is_not_a_traceback():
    """A Python-constructed dump can hold what JSON cannot; the refusal must be
    the module's own, never a `TypeError` from `sorted()`/`json.dumps()`."""
    dump = _dump()
    dump["edges"] = [{"src": 1, "dst": 2, "props": {"blob": {1, 2, 3}}}]
    dump["edge_count"] = 1
    plan = build_plan(dump)  # must not raise TypeError
    assert plan.dump_sha256


def test_the_fingerprint_is_order_insensitive_over_edges():
    dump = _dump()
    dump["edges"] = [{"src": 1, "dst": 2}, {"src": 2, "dst": 3}]
    dump["edge_count"] = 2
    first = build_plan(dump).dump_sha256
    dump["edges"] = [{"dst": 3, "src": 2}, {"dst": 2, "src": 1}]
    assert build_plan(dump).dump_sha256 == first


# ──────────── the artifacts are files, not names: identity beats text ─────────
def test_cli_refuses_a_hardlink_to_the_dump_as_the_report(tmp_path):
    """Two names, one inode — the dry run would replace the snapshot it reads.

    A `resolve()` comparison passes this (`/p/a.json` != `/p/b.json`), and the
    report then lands ON the dump while the run exits 0 saying only "wrote
    report": the documented step 1 destroys its own input, and `--revert` has
    no plan to read because the plan IS the file that got overwritten.
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "snapshot.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    hardlink = tmp_path / "plan.json"
    os.link(dump_path, hardlink)
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path), "--report", str(hardlink)])
    # A bad invocation, discovered before anything is read: exit 1 (usage), not
    # the 2 this tool reserves for a REFUSED snapshot.
    assert "are the same file" in str(exc.value.code)
    assert json.loads(dump_path.read_text(encoding="utf-8"))["nodes"], "the snapshot survived"


def test_cli_refuses_an_unresolvable_path_instead_of_tracebacking(tmp_path, capsys):
    """A symlink loop raises RuntimeError out of `resolve()`, not OSError."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    loop = tmp_path / "loop.json"
    loop.symlink_to(loop)
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path), "--report", str(loop)])
    assert "cannot be resolved" in str(exc.value.code)  # not a RuntimeError


def test_cli_refuses_a_non_utf8_dump_with_a_message(tmp_path):
    """The docstring points at the encrypted `tortoise export` artifact — i.e.
    at a file that is NOT UTF-8. A decode error must be the same usage/IO exit
    as a missing file, not a traceback."""
    from tools.legacy_id_normalise import main

    binary = tmp_path / "export.bin"
    binary.write_bytes(b"\xff\xfe\x00\x80 binary")
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(binary)])
    assert "not UTF-8 text" in str(exc.value.code)


def test_cli_refuses_out_without_a_mode(tmp_path):
    """`--apply` without `--out` is refused; so is its mirror — a wrapper that
    names the artifact, forgets the mode, gets exit 0 and no snapshot, and the
    next phase loads a stale same-named file from an earlier run."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path), "--out", str(tmp_path / "o.json")])
    assert "--out requires --apply or --revert" in str(exc.value.code)
    assert not (tmp_path / "o.json").exists()


def test_the_apply_headline_shows_what_the_pass_left_behind(tmp_path, capsys):
    """`legacy 0` is scoped to the labels the pass renames.

    #8607's indicator (1) asks for `0` ids in ALL THREE legacy shapes
    (`<hex>-<hex>` non-canonical · `p-NNNNNNN` · `EV-*`), and `:Event` is
    declared out of scope, so 30 `EV-*` ids survive the pass. A headline that
    prints only `legacy 1002 → 0` reads as "no legacy ids remain".
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    assert (
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(tmp_path / "o.json"),
                "--report",
                str(tmp_path / "r.json"),
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "legacy 1002 → 0" in err
    assert "outside this pass 34" in err, err
    assert "'Event': 30" in err, err


def test_a_revert_never_rewrites_the_plan_it_reads(tmp_path):
    """`--report` is an INPUT in revert mode — rewriting it would destroy the
    apply-time record in the file that is the only way back."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    plan_path = tmp_path / "plan.json"
    assert (
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(tmp_path / "n.json"),
                "--report",
                str(plan_path),
            ]
        )
        == 0
    )
    before = plan_path.read_text(encoding="utf-8")
    assert json.loads(before)["mode"] == "apply"
    assert (
        main(
            [
                "--dump",
                str(tmp_path / "n.json"),
                "--revert",
                "--report",
                str(plan_path),
                "--out",
                str(tmp_path / "back.json"),
            ]
        )
        == 0
    )
    assert plan_path.read_text(encoding="utf-8") == before, "the plan was rewritten"


def test_a_relative_symlink_to_the_dump_is_refused_as_the_report(tmp_path):
    """The identity key must catch a symlink too, not only a hardlink."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    (tmp_path / "alias.json").symlink_to("dump.json")  # RELATIVE target
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path), "--report", str(tmp_path / "alias.json")])
    assert "are the same file" in str(exc.value.code)


def test_the_report_is_on_disk_before_a_snapshot_write_that_fails(tmp_path):
    """The documented order, pinned: report first, snapshot second.

    If the snapshot write fails, the plan that can undo the pass must already
    exist — otherwise the run leaves a plan-less artifact, or none at all.
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    report = tmp_path / "r.json"
    with pytest.raises(SystemExit):
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(tmp_path),  # a DIRECTORY: the snapshot write fails
                "--report",
                str(report),
            ]
        )
    assert json.loads(report.read_text(encoding="utf-8"))["renamed"] == _renamed_population()
    assert json.loads(dump_path.read_text(encoding="utf-8"))["node_count"] == 9_738


def test_a_report_with_no_renames_cannot_pose_as_an_undo(tmp_path):
    """A no-op plan is not an undo, and its proof is vacuous.

    Run documented step 2 twice: the second pass renames nothing and rewrites
    the report with `renames: []`. Step 3 then "restores" the normalised dump,
    exits 0, and the original ids survive in NO artifact — `_derived_legacy_handle` is a
    sha256, so they are not re-derivable.
    """
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out, report = tmp_path / "n.json", tmp_path / "plan.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    assert (
        main(
            [
                "--dump",
                str(out),
                "--revert",
                "--report",
                str(report),
                "--out",
                str(tmp_path / "a.json"),
            ]
        )
        == 0
    )
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"renames": [], "dump_sha256": "x"}), encoding="utf-8")
    with pytest.raises(LegacyIdError) as exc:
        apply_plan(
            json.loads(out.read_text(encoding="utf-8")),
            build_plan(json.loads(out.read_text(encoding="utf-8"))),
            revert=True,
        )
    assert any("carries no renames" in p for p in exc.value.problems)
    # Exit 1 (usage/IO), not 2: the SNAPSHOT is healthy, the PLAN cannot undo
    # anything — the same class as a report with no fingerprint, and the same home.
    with pytest.raises(SystemExit) as exc2:
        main(
            [
                "--dump",
                str(out),
                "--revert",
                "--report",
                str(empty),
                "--out",
                str(tmp_path / "b.json"),
            ]
        )
    assert "carries no renames" in str(exc2.value.code)
    assert not (tmp_path / "b.json").exists()


def test_a_no_op_apply_cannot_destroy_a_plan_that_can_undo_the_pass(tmp_path):
    """The destructive half: step 2 re-run over an already-normalised snapshot."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out, report = tmp_path / "n.json", tmp_path / "plan.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    before = report.read_text(encoding="utf-8")
    assert json.loads(before)["renamed"] == _renamed_population()
    assert (
        main(
            [
                "--dump",
                str(out),
                "--apply",
                "--out",
                str(tmp_path / "n2.json"),
                "--report",
                str(report),
            ]
        )
        == 2
    )
    assert report.read_text(encoding="utf-8") == before, "the plan survived"
    assert not (tmp_path / "n2.json").exists(), "and no snapshot was written"


def test_a_revert_report_is_on_stdout_and_parses(tmp_path, capsys):
    """Pins the half of the revert change that REPLACED the removed write."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out, report = tmp_path / "n.json", tmp_path / "plan.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "--dump",
                str(out),
                "--revert",
                "--report",
                str(report),
                "--out",
                str(tmp_path / "b.json"),
            ]
        )
        == 0
    )
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["mode"] == "revert"
    assert emitted["renamed"] == _renamed_population()
    assert emitted["dump_sha256"]


def test_a_report_without_a_fingerprint_is_a_usage_error_not_a_refusal(tmp_path):
    """A malformed report is exit 1; exit 2 is reserved for an unsafe snapshot."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out = tmp_path / "n.json"
    assert (
        main(
            [
                "--dump",
                str(dump_path),
                "--apply",
                "--out",
                str(out),
                "--report",
                str(tmp_path / "p.json"),
            ]
        )
        == 0
    )
    nosha = tmp_path / "nosha.json"
    nosha.write_text(
        json.dumps(
            {
                "renames": [
                    {"dump_id": 1, "label": "Point", "key_property": "id", "old": "a", "new": "b"}
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--dump",
                str(out),
                "--revert",
                "--report",
                str(nosha),
                "--out",
                str(tmp_path / "z.json"),
            ]
        )
    assert "dump_sha256" in str(exc.value.code)
    assert not (tmp_path / "z.json").exists()


def test_stdout_redirected_onto_the_snapshot_is_refused(tmp_path):
    """`--revert … --out restored.json > restored.json` shares one file, and the
    gate cannot see it as a path pair — one destination is fd 1."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out, report = tmp_path / "n.json", tmp_path / "plan.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    target = tmp_path / "restored.json"
    saved = os.dup(1)
    try:
        with open(target, "w", encoding="utf-8") as handle:
            os.dup2(handle.fileno(), 1)
            with pytest.raises(SystemExit) as exc:
                main(
                    ["--dump", str(out), "--revert", "--report", str(report), "--out", str(target)]
                )
            assert "standard output is redirected" in str(exc.value.code)
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    assert not target.exists() or "nodes" not in target.read_text(encoding="utf-8")


def test_a_second_pass_over_a_different_dump_cannot_overwrite_the_first_plan(tmp_path):
    """The P1: two passes, one `--report`, the first pass's ids lost.

    Measured before the guard: pass 1 over dump A writes plan.json; `rm` the dump
    (the documented workflow — the plan IS the way back); pass 2 over dump B with
    the same `--report` overwrites it, exits 0, and A's original ids then survive
    in no file. `_derived_legacy_handle` hashes the OLD key, so they are not re-derivable.
    """
    from tools.legacy_id_normalise import main

    def write(path, prefix):
        rows = [_node(i, ["Point"], id=f"{prefix}-{i}") for i in range(1, 4)]
        path.write_text(
            json.dumps(
                {
                    "format": "tortoise-logical-dump-v1",
                    "dump_revision": 2,
                    "node_count": 3,
                    "edge_count": 0,
                    "nodes": rows,
                    "edges": [],
                }
            ),
            encoding="utf-8",
        )

    dump_a, dump_b = tmp_path / "a.json", tmp_path / "b.json"
    write(dump_a, "alpha-legacy")
    write(dump_b, "beta-legacy")
    report = tmp_path / "plan.json"
    assert (
        main(
            [
                "--dump",
                str(dump_a),
                "--apply",
                "--out",
                str(tmp_path / "na.json"),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    first = report.read_text(encoding="utf-8")
    assert "alpha-legacy-1" in first
    assert (
        main(
            [
                "--dump",
                str(dump_b),
                "--apply",
                "--out",
                str(tmp_path / "nb.json"),
                "--report",
                str(report),
            ]
        )
        == 2
    )
    assert report.read_text(encoding="utf-8") == first, "the first pass's plan survived"
    assert not (tmp_path / "nb.json").exists()


def test_the_same_dumps_plan_may_be_rewritten(tmp_path):
    """The guard must NOT block a re-run of the same pass — that is byte-identical."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    report = tmp_path / "plan.json"
    out = tmp_path / "n.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    first = report.read_text(encoding="utf-8")
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    assert report.read_text(encoding="utf-8") == first


def test_a_report_on_device_that_is_not_a_file_is_not_read(tmp_path):
    """`--report /dev/null` on a no-op pass must not be read (nor refused).

    The guard reads the existing report to compare fingerprints; reading a sink
    is either a false refusal (`/dev/null` is not valid JSON) or a hang (a pipe).
    """
    from tools.legacy_id_normalise import main

    normalised = tmp_path / "norm.json"
    normalised.write_text(json.dumps(_dump()), encoding="utf-8")
    out = tmp_path / "o.json"
    assert (
        main(["--dump", str(normalised), "--apply", "--out", str(out), "--report", "/dev/null"])
        == 0
    )


def test_the_report_may_be_named_as_stdout_in_a_dry_run(tmp_path, capsys):
    """`--report /dev/stdout > plan.json` is ONE write to one file, not a collision."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    target = tmp_path / "plan.json"
    saved = os.dup(1)
    try:
        with open(target, "w", encoding="utf-8") as handle:
            os.dup2(handle.fileno(), 1)
            assert main(["--dump", str(dump_path), "--report", "/dev/stdout"]) == 0
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    assert json.loads(target.read_text(encoding="utf-8"))["renamed"] == _renamed_population()


def test_a_dry_run_over_the_normalised_snapshot_cannot_wipe_the_apply_plan(tmp_path):
    """The same destruction, one mode over: step 1 re-measured on step 2's output."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    out, report = tmp_path / "n.json", tmp_path / "plan.json"
    assert (
        main(["--dump", str(dump_path), "--apply", "--out", str(out), "--report", str(report)]) == 0
    )
    before = report.read_text(encoding="utf-8")
    assert json.loads(before)["mode"] == "apply"
    assert main(["--dump", str(out), "--report", str(report)]) == 2
    assert report.read_text(encoding="utf-8") == before, "the apply plan survived a dry run"


def test_a_revert_cannot_replace_another_passs_plan_with_its_snapshot(tmp_path):
    """`--revert … --out plan_B.json` used to clobber B's plan and exit 0.

    Apply mode refuses the identical command for `--out`; revert mode exempts
    only `--report` (the plan being READ), not `--out`.
    """
    from tools.legacy_id_normalise import main

    def write(path, ids):
        nodes = [_node(i, ["Point"], id=v) for i, v in enumerate(ids, start=1)]
        path.write_text(
            json.dumps(
                {
                    "format": "tortoise-logical-dump-v1",
                    "dump_revision": 2,
                    "node_count": len(nodes),
                    "edge_count": 0,
                    "nodes": nodes,
                    "edges": [],
                }
            ),
            encoding="utf-8",
        )

    a, b = tmp_path / "A.json", tmp_path / "B.json"
    write(a, ["p-0000001", "p-0000002"])
    write(b, ["p-0000001", "p-0000003"])
    plan_a, plan_b = tmp_path / "plan_A.json", tmp_path / "plan_B.json"
    na, nb = tmp_path / "A_norm.json", tmp_path / "B_norm.json"
    assert main(["--dump", str(a), "--apply", "--out", str(na), "--report", str(plan_a)]) == 0
    assert main(["--dump", str(b), "--apply", "--out", str(nb), "--report", str(plan_b)]) == 0
    b_plan = plan_b.read_text(encoding="utf-8")
    assert main(["--dump", str(na), "--revert", "--report", str(plan_a), "--out", str(plan_b)]) == 2
    assert plan_b.read_text(encoding="utf-8") == b_plan, "B's plan survived the revert"


def test_a_closed_stdout_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    """`--dump x >&-` closes fd 1, so `sys.stdout` is None and `None.write` is an
    AttributeError. The exit code was already right (1); the module's own
    standard is a message, and nothing pinned it."""
    from tools.legacy_id_normalise import main

    dump_path = tmp_path / "dump.json"
    dump_path.write_text(json.dumps(_dump()), encoding="utf-8")
    monkeypatch.setattr(sys, "stdout", None)
    with pytest.raises(SystemExit) as exc:
        main(["--dump", str(dump_path)])
    assert "standard output is closed" in str(exc.value.code)
