"""#8864 — a shared name must never be stamped with one id.

``_upsert_object`` / ``_upsert_subject`` key their ``MERGE`` on **name**
(#7369) and write the incoming id in ``ON MATCH``, so that a raw/connector
name-stub is adopted by the canonical registration. When the name is held by
two or more nodes — legal under #3590's D2 (``name`` is a natural key, not an
identity) and produced by explicit-id writers and by renames — the ``MERGE``
matches **every** carrier and the ``ON MATCH`` write stamps **one** id onto
**all** of them. From then on every id-keyed read (``MATCH (n {id:$x})``)
folds two distinct entities into one result set: duplicate-id corruption in
the write path.

The fix keys on the record's own ``id`` once the name has more than one
carrier (the id IS the identity, #3590 R1, so no name resolution is needed to
fold the record) and leaves the single-carrier path — the #452 content-hash
dedup and the stub adoption — byte-identical.

The carrier count is over **any** status, not only live holders: the ``MERGE``
matches terminal twins too, and ``test_terminal_twin_is_not_re_ided`` is the
case a live-only count would miss.
"""

from __future__ import annotations

import os
import sys

sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, sys_path)

import pytest  # noqa: E402

from tortoise.sdk import TortoiseSDK, _entity_name_id  # noqa: E402


def _nodes(proj, label, *props):
    cols = ", ".join(f"n.{p}" for p in ("id", *props)) or "n.id"
    return proj.g.query(f"MATCH (n:{label}) RETURN {cols}").result_set


def _by_id(proj, label, node_id, *props):
    cols = ", ".join(f"n.{p}" for p in props) or "n.id"
    return proj.g.query(
        f"MATCH (n:{label} {{id:$i}}) RETURN {cols}", params={"i": node_id}
    ).result_set


def _ids(rows):
    return [r[0] for r in rows]


class TestSharedNameDoesNotCollapseIds:
    def test_two_live_carriers_keep_their_own_ids(self, tmp_path):
        """The measured shape: two explicit-id live carriers, then a create."""
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query(
                "CREATE (o:Object {id:'decoy-1', name:'Acme', "
                "objectKind:'company', title:'ONE', status:'live'})"
            )
            proj.g.query(
                "CREATE (o:Object {id:'decoy-2', name:'Acme', "
                "objectKind:'company', title:'TWO', status:'live'})"
            )

            created = sdk.create_object("Acme", "company")
            created_id = created["id"]
            assert created_id == _entity_name_id("Object", "Acme")

            rows = _nodes(proj, "Object", "name")
            ids = _ids(rows)
            assert sorted(ids) == sorted(["decoy-1", "decoy-2", created_id]), (
                f"a same-name carrier was re-id'd — duplicate-id corruption (#8864): {rows}"
            )
            assert len(set(ids)) == len(ids), f"two distinct nodes share one id (#8864): {rows}"
            # The carriers are untouched, not merely un-collapsed.
            assert _by_id(proj, "Object", "decoy-1", "title") == [["ONE"]]
            assert _by_id(proj, "Object", "decoy-2", "title") == [["TWO"]]
            # And the create returned a node that actually exists, once.
            assert _by_id(proj, "Object", created_id, "name") == [["Acme"]]
        finally:
            sdk.close()

    def test_terminal_twin_is_not_re_ided(self, tmp_path):
        """The count is over ANY status: the MERGE matches terminal twins."""
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query(
                "CREATE (o:Object {id:'live-1', name:'Beta', objectKind:'company', status:'live'})"
            )
            proj.g.query(
                "CREATE (o:Object {id:'sup-1', name:'Beta', "
                "objectKind:'company', status:'superseded'})"
            )

            created_id = sdk.create_object("Beta", "company")["id"]

            rows = _nodes(proj, "Object", "status")
            ids = _ids(rows)
            assert sorted(ids) == sorted(["live-1", "sup-1", created_id]), (
                "a live-only carrier count missed the terminal twin and the "
                f"superseded carrier was re-id'd (#8864): {rows}"
            )
            assert len(set(ids)) == len(ids), rows
            assert _by_id(proj, "Object", "sup-1", "status") == [["superseded"]]
        finally:
            sdk.close()

    def test_subject_twin_is_not_re_ided(self, tmp_path):
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query("CREATE (s:Subject {id:'sub-decoy-1', name:'Bob', status:'live'})")
            proj.g.query("CREATE (s:Subject {id:'sub-decoy-2', name:'Bob', status:'live'})")

            created_id = sdk.create_subject("Bob")["id"]

            rows = _nodes(proj, "Subject", "name")
            ids = _ids(rows)
            assert sorted(ids) == sorted(["sub-decoy-1", "sub-decoy-2", created_id]), rows
            assert len(set(ids)) == len(ids), rows
        finally:
            sdk.close()

    def test_extra_props_land_on_the_new_node_only(self, tmp_path):
        """``_persist_extra_props`` follows the MERGE key, not the name."""
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query("CREATE (o:Object {id:'decoy-1', name:'Acme', status:'live'})")
            proj.g.query("CREATE (o:Object {id:'decoy-2', name:'Acme', status:'live'})")

            created_id = sdk.create_object("Acme", "company", lane="x")["id"]

            assert _by_id(proj, "Object", created_id, "lane") == [["x"]]
            assert _by_id(proj, "Object", "decoy-1", "lane") == [[None]], (
                "a caller prop leaked onto a same-name carrier (#8864)"
            )
            assert _by_id(proj, "Object", "decoy-2", "lane") == [[None]]
        finally:
            sdk.close()


class TestSingleCarrierPathIsUnchanged:
    """The fix must not touch the name-keyed path (#452 dedup, stub adoption)."""

    def test_create_twice_is_idempotent(self, tmp_path):
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            first = sdk.create_object("Solo", "company")["id"]
            second = sdk.create_object("Solo", "company")["id"]
            assert first == second
            rows = _nodes(proj, "Object", "name")
            assert len(rows) == 1, f"content-hash dedup regressed: {rows}"
        finally:
            sdk.close()

    def test_create_twice_stays_idempotent_when_the_name_is_shared(self, tmp_path):
        """A 2-carrier name does not break the create's idempotency."""
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query("CREATE (o:Object {id:'decoy-1', name:'Acme', status:'live'})")
            proj.g.query("CREATE (o:Object {id:'decoy-2', name:'Acme', status:'live'})")
            first = sdk.create_object("Acme", "company")["id"]
            second = sdk.create_object("Acme", "company")["id"]
            assert first == second
            assert len(_nodes(proj, "Object")) == 3, _nodes(proj, "Object")
        finally:
            sdk.close()

    def test_stub_is_adopted_by_name(self, tmp_path):
        """A single pre-existing carrier is still adopted (id stamped on it)."""
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            # A pre-#7936 style stub: carries the name, a foreign id, no _stub.
            proj.g.query("CREATE (o:Object {id:'random-ulid-stub', name:'Gamma', status:'live'})")
            created = sdk.create_object("Gamma", "company")
            rows = _nodes(proj, "Object", "name")
            assert len(rows) == 1, rows
            assert rows[0][0] == created["id"], (
                "the canonical id must land on the adopted stub (#1155-P1)"
            )
        finally:
            sdk.close()


class TestFoldSeam:
    """The same guard holds on the fold itself (live IS the replay fold)."""

    def test_apply_on_a_pre_seeded_shared_name_graph(self, tmp_path):
        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            proj.g.query("CREATE (o:Object {id:'decoy-1', name:'Acme', status:'live'})")
            proj.g.query("CREATE (o:Object {id:'decoy-2', name:'Acme', status:'live'})")
            canonical = _entity_name_id("Object", "Acme")
            proj.apply(
                {
                    "type": "ObjectRegistered",
                    "id": canonical,
                    "name": "Acme",
                    "objectKind": "company",
                    "status": "live",
                }
            )
            ids = _ids(_nodes(proj, "Object"))
            assert sorted(ids) == sorted(["decoy-1", "decoy-2", canonical]), ids
            assert len(set(ids)) == len(ids), ids
        finally:
            sdk.close()


class TestReplayModelsSeeTheMentionStubs:
    """The two Python models of the fold must count the SAME carriers as the graph.

    #8864 made the MERGE-key branch depend on the CARRIER COUNT, so a carrier a
    model cannot see puts it on the OPPOSITE branch from every real graph. The
    models (`journal_object_surviving_keys`, `journal_epistemic_existence`) read
    registrations; `_event_plain_merge` is a second, registration-free carrier
    source that also runs on replay — a mention's `object`/`subject`/`uses[]`
    name MERGEs a name-stub (`ON CREATE SET n.id=$id`, deterministic #7936 id).

    Skipping that mint made the model BURY a carrier the graph keeps, i.e. it
    refused a fold `rebuild_all` performs — the fail-CLOSED direction these
    readers exist to avoid. Each case below reproduces the shape end to end and
    compares the model against the replayed graph, so it cannot pass on models
    that undercount.
    """

    def test_a_mention_minted_OBJECT_stub_is_a_carrier_in_both_models(self, tmp_path):
        from tortoise.projection import (
            _EXISTENCE_EXISTS,
            _existence_state,
            journal_epistemic_existence,
            journal_object_surviving_keys,
        )

        name = "Acme"
        stub = _entity_name_id("Object", name)
        events = [
            # Mints `(stub, name)` — a carrier with NO registration record.
            {"type": "EventRecorded", "eventId": "ev-1",
             "event": {"eventId": "ev-1", "eventKind": "mentioned",
                       "object": name}},
            {"type": "ObjectRegistered", "id": "obj-x", "name": "Other"},
            # Now TWO nodes carry "Acme" — but only the model that SAW the stub
            # knows that, and that is what picks the MERGE key.
            {"type": "EntityMutated", "label": "Object", "id": "obj-x",
             "op": "rename", "state": {"name": name}},
            {"type": "ObjectRegistered", "id": stub, "name": name},
        ]

        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            for rec in events:
                proj.apply(rec)
            graph_ids = sorted(_ids(_nodes(proj, "Object")))
        finally:
            sdk.close()

        assert graph_ids == sorted([stub, "obj-x"]), graph_ids

        survivors, _names = journal_object_surviving_keys(events)
        assert sorted(survivors) == graph_ids, (
            "journal_object_surviving_keys disagrees with the replayed graph — "
            f"model={sorted(survivors)} graph={graph_ids}"
        )
        boundary = journal_epistemic_existence(events)
        assert _existence_state(
            *boundary[("Object", "obj-x")], len(events) - 1,
        ) == _EXISTENCE_EXISTS, (
            "journal_epistemic_existence buried 'obj-x', which the graph still "
            f"carries: {boundary[('Object', 'obj-x')]}"
        )

    def test_a_mention_minted_SUBJECT_stub_is_a_carrier_in_the_boundary(self, tmp_path):
        from tortoise.projection import (
            _EXISTENCE_EXISTS,
            _existence_state,
            journal_epistemic_existence,
        )

        name = "Bob"
        stub = _entity_name_id("Subject", name)
        events = [
            {"type": "EventRecorded", "eventId": "ev-1",
             "event": {"eventId": "ev-1", "eventKind": "mentioned",
                       "subject": name}},
            {"type": "SubjectAdded", "id": "sub-x", "name": "Other"},
            {"type": "EntityMutated", "label": "Subject", "id": "sub-x",
             "op": "rename", "state": {"name": name}},
            {"type": "SubjectAdded", "id": stub, "name": name},
        ]

        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            for rec in events:
                proj.apply(rec)
            graph_ids = sorted(_ids(_nodes(proj, "Subject")))
        finally:
            sdk.close()

        assert graph_ids == sorted([stub, "sub-x"]), graph_ids

        boundary = journal_epistemic_existence(events)
        assert _existence_state(
            *boundary[("Subject", "sub-x")], len(events) - 1,
        ) == _EXISTENCE_EXISTS, (
            "journal_epistemic_existence buried 'sub-x', which the graph still "
            f"carries: {boundary[('Subject', 'sub-x')]}"
        )

    @pytest.mark.parametrize("label,name,mention_key", [
        ("Object", "Acme", "object"),
        ("Subject", "Bob", "subject"),
    ])
    def test_a_mention_stub_is_a_carrier_in_the_ENTITY_reference_fold(
            self, tmp_path, label, name, mention_key):
        """`consistency._fold_journal_entities` is the THIRD model of this fold.

        `journal_epistemic_existence` and `journal_object_surviving_keys` are the
        read-side pair; `_fold_journal_entities` is the carrier table
        `check_consistency`'s ENTITY leg runs, and its `carriers_by_name` index
        decides the state-op guard. Before the fix it counted registrations
        only, so the mention-minted stub was invisible: the name looked
        single-carrier, the guard took the collapsing branch and EVICTED the
        live carrier — then refused the following state op `state-op-miss` on a
        journal the graph folds (fail-CLOSED: `recover_from_log` refuses to open
        the DB, `rebuild` parity fail-loud, `backup.restore` refuses).

        Pinned against the REPLAYED GRAPH (both carriers kept, the state op
        applied), never against the model's own opinion.
        """
        import copy

        from tortoise.consistency import _fold_journal_entities
        from tortoise.projection.nonfolded import (
            collect_non_folded,
            refused_events,
        )

        stub = _entity_name_id(label, name)
        other = "obj-x" if label == "Object" else "sub-x"
        creation = ("ObjectRegistered" if label == "Object" else "SubjectAdded")
        events = [
            # Mints `(stub, name)` — a carrier with NO registration record.
            {"type": "EventRecorded", "eventId": "ev-1",
             "event": {"eventId": "ev-1", "eventKind": "mentioned",
                       mention_key: name}},
            {"type": creation, "id": other, "name": "Other"},
            # Now TWO nodes carry `name`, one of them the stub.
            {"type": "EntityMutated", "label": label, "id": other,
             "op": "rename", "state": {"name": name}},
            {"type": creation, "id": stub, "name": name},
            # The state op the carrier guard gates. A shared name keys on `id`
            # (#8864), so the graph MATCHes `other` and folds.
            {"type": "EntityMutated", "label": label, "id": other,
             "op": "restatus", "state": {"status": "archived"}},
        ]

        sdk = TortoiseSDK(str(tmp_path / "g.db"))
        try:
            proj = sdk._get_proj()
            for rec in events:
                proj.apply(rec)
            graph_ids = sorted(_ids(_nodes(proj, label)))
            graph_status = _by_id(proj, label, other, "status")
        finally:
            sdk.close()

        assert graph_ids == sorted([stub, other]), graph_ids
        assert graph_status == [["archived"]], graph_status

        with collect_non_folded() as entries:
            entities, _deleted, _ambiguous = _fold_journal_entities(
                copy.deepcopy(events))
        assert [str(e) for e in refused_events(entries)] == [], (
            "the ENTITY reference fold refused a record the graph folds — it "
            "did not count the mention-minted stub as a carrier: "
            f"{[str(e) for e in refused_events(entries)]}"
        )
        assert entities[(label, other)]["status"] == "archived", (
            "the reference fold did not apply the state op the graph folded: "
            f"{entities[(label, other)]}"
        )
