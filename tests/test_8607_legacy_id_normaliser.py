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

Pure functions over a plain dict: no graph, no database, no clock. Runs in the
embedded carve-out lane.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.ids import entity_name_id
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
    # second identity space. `entity_name_id` is `_entity_name_id`'s (§1.2).
    assert canonical_id("Point", "p-0000438") == entity_name_id("Point", "p-0000438")
    assert canonical_id("Point", "p-0000438") != canonical_id("Object", "p-0000438")


# ── applying it: the counts reconcile, and nothing else moves ──────────────


def test_counts_reconcile_and_edges_survive():
    dump = _dump()
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
    assert out["edges"] == dump["edges"], "a key rename must not touch edges"
    assert out["node_count"] == dump["node_count"]
    assert [n["dump_id"] for n in out["nodes"]] == [n["dump_id"] for n in dump["nodes"]], (
        "dump_id is the store's internal id and is preserved — that is WHY the edges survive"
    )
    for before, after in zip(dump["nodes"], out["nodes"], strict=True):
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
    dump["nodes"].append(_node(10_006, labels, id="session_9f8a7b6c5d4e"))
    dump["node_count"] = len(dump["nodes"])
    plan = build_plan(dump)  # must not raise
    assert [r for r in plan.renames if r.dump_id == 10_006] == [], (
        "a node carrying an out-of-scope label's key is never a rename candidate"
    )
    assert census(dump).out_of_scope["Session"] == 2
    out, _ = apply_plan(dump, plan)
    node = next(n for n in out["nodes"] if n["dump_id"] == 10_006)
    assert node["props"]["id"] == "session_9f8a7b6c5d4e", "the session key is untouched"


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
    assert exc.value.code == 2


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
    assert exc.value.code == 2
    assert json.loads(same.read_text(encoding="utf-8"))["nodes"], "the dump survived"


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
