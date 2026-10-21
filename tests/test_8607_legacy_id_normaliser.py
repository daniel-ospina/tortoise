"""#8607 (W1b) — the legacy-id census, the plan, and the count invariant.

The pass this file pins answers one question mechanically: **which ids on the
snapshot are not on the canonical contract, and does renaming exactly those
ones, exactly once, leave the snapshot whole?** Every test here is an INVARIANT
over that partition — the thing #8607's own indicator (1) asks for ("the same
node count resolves before/after") and (2) ("a second run changes nothing").

The fixture reproduces the population `STORAGE-ARCHITECTURE.md` §17.5 measured
on the live graph on 2026-10-08 — 8,700 ``<hex>-<hex>`` + 1,000 ``p-NNNNNNN``
+ ~30 ``EV-*`` — because the point of the census is the SPLIT, not the total:
``<hex>-<hex12>`` is what ``tortoise/ids.py::ulid()`` mints (``sdk._ULID_RE``),
so those 8,700 are already canonical and only 1,030 ids are actually legacy.

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
    LEGACY,
    NAME_KEYED,
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
TOTAL_ID_KEYED = MINTED + P_IDS + EV_IDS


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
    """The measured population, plus every key class the contract has.

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
        nodes.append(_node(next_id, ["Event"], eventId=_ev_id(i), name=f"ev {i}"))
        next_id += 1
    # name/url-keyed labels: content is the key, so they are declared out of
    # scope and must survive untouched.
    nodes.append(_node(next_id, ["Object"], name="acme", id="obj-ignored-handle"))
    next_id += 1
    nodes.append(_node(next_id, ["Subject"], name="alice"))
    next_id += 1
    nodes.append(_node(next_id, ["Source"], url="https://x.test/a"))
    next_id += 1
    edges = [
        {"src": 1, "dst": MINTED + 1, "type": "IMPL"},
        {"src": 1, "dst": TOTAL_ID_KEYED - EV_IDS + 1, "type": "NAND"},
        {"src": MINTED + 1, "dst": TOTAL_ID_KEYED + 1, "type": "aboutObject"},
    ]
    return {
        "format": "tortoise-logical-dump-v1",
        "dump_revision": 2,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


# ── the census: the split, not the carried total ────────────────────────────


def test_the_minted_hex_pair_group_is_canonical_not_legacy():
    """The frame correction, pinned. ``<hex>-<hex12>`` IS the mint format."""
    assert shape(_minted_id(0)) == "minted-ulid"
    assert is_canonical(_minted_id(0))
    # …and the two genuinely-off-contract groups are not.
    assert shape(_p_id(0)) == LEGACY
    assert shape(_ev_id(0)) == LEGACY
    assert not is_canonical(_p_id(0))


def test_census_counts_the_measured_population():
    found = census(_dump())
    assert found.nodes == TOTAL_ID_KEYED + 3
    assert found.id_keyed == TOTAL_ID_KEYED
    assert found.canonical == MINTED
    assert found.legacy == P_IDS + EV_IDS, (
        "only the p-NNNNNNN and EV-* groups are off the contract; the 8,700 "
        "<hex>-<hex> ids are the minted form"
    )
    assert found.legacy != 9_700, "the carried ~9,700 is not the measured population"
    assert found.canonical_shapes == {"minted-ulid": MINTED}
    assert found.legacy_shapes == {LEGACY: P_IDS + EV_IDS}
    assert found.name_keyed == 3
    assert found.by_label["Point"] == {CANONICAL: MINTED, LEGACY: P_IDS}
    assert found.by_label["Event"] == {LEGACY: EV_IDS}
    for label in ("Object", "Subject", "Source"):
        assert found.by_label[label] == {NAME_KEYED: 1}, label


# ── the partition: every legacy id mapped exactly once ──────────────────────


def test_every_legacy_id_is_mapped_exactly_once():
    dump = _dump()
    plan = build_plan(dump)
    legacy = {
        (node["labels"][0], node["props"]["id" if node["labels"][0] != "Event" else "eventId"])
        for node in dump["nodes"]
        if node["labels"][0] in ("Point", "Event")
        and not is_canonical(node["props"]["id" if node["labels"][0] != "Event" else "eventId"])
    }
    assert len(legacy) == P_IDS + EV_IDS
    assert {(r.label, r.old) for r in plan.renames} == legacy, (
        "the plan's (label, old) set must be exactly the legacy set — no node "
        "skipped, no canonical node dragged in"
    )
    assert len({r.dump_id for r in plan.renames}) == len(plan.renames), "one node, one rename"
    assert len({(r.label, r.new) for r in plan.renames}) == len(plan.renames), (
        "the mapping must be single-valued: two nodes onto one key is a MERGE"
    )
    existing = {
        (node["labels"][0], node["props"].get("id") or node["props"].get("eventId"))
        for node in dump["nodes"]
        if node["labels"][0] in ("Point", "Event")
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
    assert canonical_id("Event", "EV-2026-0001") == entity_name_id("Event", "EV-2026-0001")
    assert canonical_id("Point", "p-0000438") != canonical_id("Event", "p-0000438")


# ── applying it: the counts reconcile, and nothing else moves ──────────────


def test_counts_reconcile_and_edges_survive():
    dump = _dump()
    plan = build_plan(dump)
    out, report = apply_plan(dump, plan)

    assert report["nodes_in"] == report["nodes_out"] == len(dump["nodes"])
    assert report["id_keyed_in"] == report["id_keyed_out"] == TOTAL_ID_KEYED
    assert report["renamed"] == P_IDS + EV_IDS
    assert report["legacy_remaining"] == 0
    assert report["legacy_before"] == P_IDS + EV_IDS
    assert report["canonical_before"] == MINTED
    assert report["canonical_after"] == TOTAL_ID_KEYED, "every id is canonical after the pass"
    assert report["renamed"] + report["canonical_before"] == report["id_keyed_in"], (
        "the ledger's two halves must account for every id-keyed node"
    )
    assert out["edges"] == dump["edges"], "a key rename must not touch edges"
    assert out["node_count"] == dump["node_count"]
    assert [n["dump_id"] for n in out["nodes"]] == [n["dump_id"] for n in dump["nodes"]], (
        "dump_id is the store's internal id and is preserved — that is WHY the edges survive"
    )
    # every node still carries exactly one key, and the untouched nodes are
    # byte-identical.
    for before, after in zip(dump["nodes"], out["nodes"], strict=True):
        assert after["props"].get("name") == before["props"].get("name")
        if before["labels"][0] in ("Object", "Subject", "Source"):
            assert after == before
    assert census(out).legacy == 0
    assert report["census"]["nodes"] == len(out["nodes"])


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
    assert census(restored).legacy == P_IDS + EV_IDS


# ── refusals: every one of them is a merge or a hole, fail closed ──────────


def test_two_nodes_sharing_one_legacy_id_are_refused():
    dump = _dump()
    dup = _node(10_000, ["Point"], id="p-0000001", content="same key, other node")
    dump["nodes"].append(dup)
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


def test_a_duplicate_canonical_identity_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_002, ["Point"], id=_minted_id(0), content="twin"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("duplicate identity" in p for p in exc.value.problems)


def test_an_id_keyed_node_without_a_key_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_003, ["Point"], content="no id at all"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("no `id`" in p for p in exc.value.problems)


def test_an_ambiguous_multi_label_identity_is_refused():
    dump = _dump()
    dump["nodes"].append(_node(10_004, ["Point", "Event"], id="p-0000999", eventId="EV-2026-9999"))
    dump["node_count"] = len(dump["nodes"])
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("ambiguous" in p for p in exc.value.problems)


def test_a_truncated_snapshot_is_refused():
    dump = _dump()
    dump["node_count"] = len(dump["nodes"]) + 5  # a #5062 truncated export
    with pytest.raises(LegacyIdError) as exc:
        build_plan(dump)
    assert any("truncated or corrupt" in p for p in exc.value.problems)


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
    # The narrowing, stated: the fingerprint binds identity, labels and key —
    # the three things a rename decides from. Reprojecting a node's CONTENT is
    # not a different snapshot and must not force a rebuild.
    dump = _dump()
    plan = build_plan(dump)
    edited = _dump()
    edited["nodes"][5]["props"]["content"] = "re-extracted text"
    out, _ = apply_plan(edited, plan)
    assert census(out).legacy == 0


def test_name_keyed_labels_are_counted_and_never_rewritten():
    dump = _dump()
    out, _ = apply_plan(dump, build_plan(dump))
    objects = [n for n in out["nodes"] if n["labels"] == ["Object"]]
    assert objects and objects[0]["props"]["name"] == "acme"
    # a :Source keyed on url is untouched even though its "id" looks legacy
    source = next(n for n in out["nodes"] if n["labels"] == ["Source"])
    assert source["props"]["url"] == "https://x.test/a"


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
    assert report["renamed"] == P_IDS + EV_IDS
    assert report["legacy_remaining"] == P_IDS + EV_IDS, "a dry run changes nothing"
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
    rc = main(["--dump", str(dump_path), "--apply", "--out", str(out_path)])
    assert rc == 2
    assert not out_path.exists()
