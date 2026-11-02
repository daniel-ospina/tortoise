"""#2194/#3590 — ObjectRegistered journaling for SDK capture-created Objects.

Capture-created Objects (and their ObjectSuperseded folds) must survive
``rebuild_all``: ``_create_entity`` journals ``ObjectRegistered`` on EVERY
Object create (#3590 retired the first-registration probe: with a minted id,
"the node exists" no longer implies "this name was registered", so a probe could
skip the registration of a live node and `rebuild_all` would destroy it). The
duplicate line is replay-idempotent — the fold MERGEs by id and `createdAt`
adopts first-wins — so the pass-1b replay + fold sweep always have a node to
fold.

Test classes:
- Tests 1-7: RED at base (no journal / no unconditional reserved-prop pop).
- Tests 8-9: green-pin guards (pre-existing behavior the fix must not break).
"""
from __future__ import annotations

import os

sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
import sys  # noqa: E402

sys.path.insert(0, sys_path)

import pytest  # noqa: E402
from _node_ids import node_id  # noqa: E402

from tortoise.ids import ulid  # noqa: E402
from tortoise.log import EventLog  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


def _journaled(sdk, events_dir):
    return EventLog(str(events_dir / "events.jsonl")).read_all()


def _ors(journal):
    return [e for e in journal if e.get("type") == "ObjectRegistered"]


def _name_ors(journal, name):
    return [e for e in _ors(journal) if e.get("name") == name]


def _object_row(proj, name, *props):
    """Return result rows for an Object by name.

    With explicit props → rows of scalars. Without props → the full node as a
    properties dict (RETURN properties(o) — FalkorDB RETURN o yields a Node
    object, not a dict).
    """
    if props:
        cols = ", ".join(f"o.{p}" for p in props)
        r = proj.g.query(
            f"MATCH (o:Object {{name:$n}}) RETURN {cols}", params={"n": name})
    else:
        r = proj.g.query(
            "MATCH (o:Object {name:$n}) RETURN properties(o)",
            params={"n": name})
    return r.result_set


# ── Test 1 (RED): capture→rebuild→fold round-trip ──────────────────────────

class TestCaptureFoldRoundTrip:
    def test_capture_object_and_fold_survive_rebuild_all(self, tmp_path):
        """Indicator 2 + ts parity: a capture-shaped create + a REAL
        apply_supersessions fold survive rebuild_all; supersededAt replays
        the journaled ObjectSuperseded envelope ts (#2164 P4), not rebuild
        time."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t1.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            # capture shape: successor first (visible-successor gate), then
            # the fold target, both is_episodic=False like _extract_session_v2.
            sdk.create_entity("object", "strategy-B",
                              objectKind="core:strategy", is_episodic=False)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            from tortoise.commit_ops import apply_supersessions
            applied = apply_supersessions(
                proj, sdk,
                [{"superseded": "strategy-A", "supersedes_by": "strategy-B",
                  "evidence": "capture fold"}],
                session_id="s1")
            assert applied == 1, "the live fold must apply"
            journal = _journaled(sdk, events)
            fold_ts = [e for e in journal
                       if e.get("type") == "ObjectSuperseded"][-1]["ts"]
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(
                proj, "strategy-A", "id", "status", "supersededBy",
                "supersededAt", "objectKind", "is_episodic")
            assert rows, (
                "capture Object vanished on rebuild — ObjectRegistered was "
                "never journaled (OD2 gap)")
            assert rows[0][1] == "superseded", rows[0]
            assert rows[0][2] == "strategy-B", rows[0]
            assert rows[0][4] == "core:strategy", rows[0]
            assert rows[0][5] == False, rows[0]  # noqa: E712 — DB-read scalar
            assert rows[0][3] == fold_ts, (
                "supersededAt must replay the journaled event ts, not "
                "rebuild time")
        finally:
            sdk.close()

    @pytest.mark.parametrize("name", ["acme", "estrategia-ñ-日本語-💡"])
    def test_plain_object_survives_rebuild_all_byte_identical(self, tmp_path,
                                                              name):
        """Indicator 1+3: auto ObjectRegistered exists per fresh create; the
        FULL replayed property set equals the pre-wipe live snapshot;
        createdAt == journaled createdAt (no rebuild-time drift).

        Parametrized over an ASCII name and a non-ASCII name so the probe's
        minted-id + JSONL mirror get unicode
        coverage — a digest/param regression on non-ASCII input would
        silently fail-closed on genuinely-new registrations (the plan's
        Step 1.1 acceptance; the fail-closed consequence is the exact
        node-loss class this issue closes)."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t1b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", name,
                              objectKind="core:org", is_episodic=False)
            live_rows = _object_row(proj, name)
            assert live_rows, "object must exist live"
            live_props = {k: v for k, v in live_rows[0][0].items()
                          if k != "embedding"}
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, name)
            assert len(ors) == 1, f"exactly one ObjectRegistered expected: {ors}"
            line = ors[0]
            assert line["id"] == node_id(proj, "Object", name), line
            assert line["name"] == name
            assert line["object_kind"] == "core:org"
            # #9974 R1: the record carries NO status for a status-less birth —
            # the door no longer fabricates `'live'` into it (absent READS live).
            assert "status" not in line, line
            assert line.get("createdAt"), line
            assert line["is_episodic"] == False  # noqa: E712
            proj.rebuild_all(str(events), confirm_destructive=True)
            rebuilt = _object_row(proj, name)
            assert rebuilt, "Object must survive rebuild"
            rebuilt_props = {k: v for k, v in rebuilt[0][0].items()
                             if k != "embedding"}
            assert rebuilt_props == live_props, (
                f"replayed prop set diverges from live: "
                f"live={live_props!r} rebuilt={rebuilt_props!r}")
            assert rebuilt[0][0].get("createdAt") == line["createdAt"], (
                "createdAt must not drift to rebuild time")
        finally:
            sdk.close()


# ── Tests 3-4 (RED): only-on-create semantics ──────────────────────────────

class TestReMentionJournalsEveryRegistration:
    def test_remention_double_journals_and_rebuild_matches_live(
            self, tmp_path):
        """A canonical re-mention (MERGE by ID, ON MATCH) APPENDS a duplicate
        ``ObjectRegistered`` (#3590 retired the first-registration probe — see
        the module docstring).

        The old "only-on-create" divergence is GONE with it: replay used to see
        only the first registration, so a live prop churn was invisible and
        ``rebuild`` reverted to the first value. Now every create is journaled,
        so replay lands on the same props as live — `rebuild == live`, which is
        the invariant the epic exists for."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t3.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "strategy-A",
                              objectKind="dev:issue", is_episodic=False)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy",
                              title="Recategorized", is_episodic=False)
            # the mutation IS live (ON MATCH coalesce updates objectKind)
            rows = _object_row(proj, "strategy-A", "objectKind")
            assert rows and rows[0][0] == "core:strategy", rows
            # id-equality via graph query (create_entity returns
            # {node, nudges} — never index result["id"])
            ids = proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id",
                params={"n": "strategy-A"}).result_set
            assert len(ids) == 1, "exactly one Object node"
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "strategy-A")
            assert len(ors) == 2, (
                "a re-mention journals a second (replay-idempotent) "
                f"registration line: {len(ors)} lines")
            assert [ln["object_kind"] for ln in ors] == [
                "dev:issue", "core:strategy"], (
                "both registrations are journaled, in call order")
            proj.rebuild_all(str(events), confirm_destructive=True)
            rebuilt = _object_row(proj, "strategy-A", "objectKind")
            assert rebuilt and rebuilt[0][0] == "core:strategy", (
                "rebuild must reproduce the LIVE props (#3590: no "
                "only-on-create divergence)")
        finally:
            sdk.close()

    def test_remention_after_fold_keeps_the_fold_and_does_not_resurrect(
            self, tmp_path):
        """A superseded name re-mentioned: the fold survives live AND on replay.

        #3590 round-5 review F4. The retired form of this pin claimed the
        re-mention "resolves to the SAME id, so it appends a registration line
        that MERGEs onto the existing node instead of creating a second one".
        That is the PRE-#3590 contract and the code no longer does it:
        ``entity_identity.resolve_or_mint`` resolves through
        ``live_name_holder_ids(..., live_only=True)``, and its own docstring
        rules that *"A terminal holder does not block a create: it is a
        different incarnation"* — re-using the folded id would be id reuse (R5).
        So the re-mention MINTS a second, LIVE Object carrying the same name;
        same-name coexistence is what D2 legalises, and the #1350 clobber
        invariant is that the FOLDED incarnation stays folded.

        The retired assertions read a 2-row result in UNSPECIFIED order
        (``rows[0][0] == 'superseded'``), so they passed only while the
        terminal row happened to come back first. MEASURED at the review head:
        the pre-rebuild read returns ``[superseded, live]`` and the POST-rebuild
        read returns ``[live, superseded]``, so the pin was RED on the tree it
        was committed to (``1 failed, 20 passed``). It is restated over the
        ``(id, status, supersededBy)`` SET, which is order-free and pins more,
        not less — the fold survives replay AND the twin carries a DIFFERENT id
        (no id reuse).

        Mutations that must red this:
          * a clobber (the re-mention resetting superseded->live) — the statuses
            collapse to ``{"live"}``;
          * id reuse (the re-mention adopting the folded id) — one row, not two;
          * a replay that resurrects the folded incarnation — the superseded row
            is gone after ``rebuild_all``.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t4.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "strategy-B",
                              objectKind="core:strategy", is_episodic=False)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            seeded = _object_row(proj, "strategy-A", "id", "status")
            assert len(seeded) == 1, seeded
            folded_id = seeded[0][0]
            from tortoise.commit_ops import apply_supersessions
            assert apply_supersessions(
                proj, sdk,
                [{"superseded": "strategy-A", "supersedes_by": "strategy-B",
                  "evidence": "fold"}],
                session_id="s1") == 1
            # re-mention the SUPERSEDED name: D2/R5 mint a NEW incarnation, and
            # the fold on the old one must not be cleared (#1350 clobber guard).
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            rows = _object_row(proj, "strategy-A", "id", "status",
                               "supersededBy")
            assert len(rows) == 2, (
                "a re-mention of a terminal name must mint ONE new incarnation "
                f"(D2/R5), leaving the folded one in place; got {rows}")
            by_status = {r[1]: r for r in rows}
            assert set(by_status) == {"superseded", None}, (
                "the re-mention reset the fold — #1350 clobber guard: "
                f"{rows}")
            assert by_status["superseded"][0] == folded_id, (
                f"the folded incarnation changed id: {rows} vs {folded_id}")
            assert by_status["superseded"][2] == "strategy-B", rows
            twin_id = by_status[None][0]
            assert twin_id != folded_id, (
                f"R5 forbids id reuse; the re-mention adopted {folded_id}: {rows}")
            journal = _journaled(sdk, events)
            assert len(_name_ors(journal, "strategy-A")) == 2, (
                "re-mention of a superseded Object journals a duplicate "
                "registration, but must not add a fold or a reset")
            proj.rebuild_all(str(events), confirm_destructive=True)
            rebuilt = _object_row(proj, "strategy-A", "id", "status",
                                  "supersededBy")
            assert len(rebuilt) == 2, (
                "replay must reproduce BOTH incarnations — a re-mention after "
                f"a fold is not a no-op; got {rebuilt}")
            rebuilt_by_status = {r[1]: r for r in rebuilt}
            assert rebuilt_by_status.get("superseded") == by_status["superseded"], (
                "no resurrect on rebuild — the folded incarnation must replay "
                f"byte-identically; live={rows} rebuilt={rebuilt}")
            assert rebuilt_by_status.get(None) == by_status[None], (
                "the live incarnation must replay with the same id: "
                f"live={rows} rebuilt={rebuilt}")
        finally:
            sdk.close()


# ── Test 5 (RED): stub adoption ────────────────────────────────────────────

class TestStubAdoption:
    def test_stub_adoption_journals_canonicalization(self, tmp_path):
        """#1155/#2164 ISSUE-B heal: a name-stub under a random ulid (raw
        CREATE, connector produces-edge shape) adopted by an SDK create must
        journal the canonical registration (probe by canonical id+name misses)
        AND adopt createdAt on the live node (ON MATCH coalesce), so rebuild
        restores the canonical node with the journaled id + createdAt.

        Byte-identity scope on the stub path is id + createdAt (what the ON
        MATCH adoption clause guarantees). status/title diverge by accepted
        design: the #1350 clobber guard forbids ON MATCH status writes, so the
        LIVE adopted node keeps the stub's absent status/title, while rebuild's
        ON CREATE writes status='live' + title='' from the journaled line —
        the plan-accepted stub status/title asymmetry (pre-existing for live;
        #2194 makes the rebuilt twin honest about the registration)."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t5.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            stub_id = ulid()
            proj.g.query(
                "CREATE (o:Object {name:$n, id:$id})",
                params={"n": "connector-name", "id": stub_id})
            sdk.create_entity("object", "connector-name",
                              objectKind="core:other", is_episodic=False)
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "connector-name")
            assert len(ors) == 1, (
                "stub adoption must journal the canonical registration "
                f"(probe by canonical id+name found no canonical row): {ors}")
            line = ors[0]
            assert line["id"] == node_id(proj, "Object", "connector-name")
            # live adopted node carries the journaled createdAt (ON MATCH
            # coalesce(o.createdAt, $ca)) — byte-identity on the stub path
            rows = _object_row(proj, "connector-name", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "stub must be canonicalized to the obj-<sha26> id")
            assert rows[0][1] == line["createdAt"], (
                "live adopted createdAt must equal the journaled value")
            # Accepted stub asymmetry: rebuild's ON CREATE writes the
            # journaled line's own properties, the live stub keeps its own. For
            # `status` there is nothing to write either way since #9974 R1 (the
            # line carries none for a status-less birth), so both sides are
            # ABSENT and the asymmetry is gone for this field.
            live_props = _object_row(proj, "connector-name")[0][0]
            assert "status" not in live_props, live_props
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "connector-name", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "rebuild must restore the canonical id, not the stub ulid")
            assert rows[0][1] == line["createdAt"], rows[0]
            rebuilt_props = _object_row(proj, "connector-name")[0][0]
            assert "status" not in rebuilt_props, rebuilt_props
        finally:
            sdk.close()


# ── Tests 14-15 (closed by #3299): delete durability ──────────────────────


class TestDeleteDurability:
    """Delete durability (#2194 code review; #2296 scope hook; closed by #3299).

    #3299 journals ``_delete_entity`` as an ``EntityMutated op=delete`` JSONL
    record and replays it as a hard delete, so:
    - test 14: a deleted Object is ABSENT after rebuild_all;
    - test 15: delete→recreate journals TWO first-registrations and replay
      reproduces the SECOND (live) incarnation, not the first.
    """

    def test_deleted_object_absent_after_rebuild(self, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t14.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "delete-me-A",
                              objectKind="core:other", is_episodic=False)
            oid = node_id(proj, "Object", "delete-me-A")
            journal = _journaled(sdk, events)
            assert len(_name_ors(journal, "delete-me-A")) == 1
            assert sdk._delete_entity(oid) is True, "node must be deleted"
            rows = _object_row(proj, "delete-me-A")
            assert not rows, "live node must be gone after delete"
            # #3299: the delete is now journaled (EntityMutated op=delete),
            # so replay hard-deletes instead of resurrecting the OR line.
            # #2944: rebuild_all is destructive, so the caller opts in.
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "delete-me-A", "status", "createdAt")
            assert not rows, (
                "deleted Object resurrects live on rebuild — the #3299 "
                "defect (no longer: the delete is journaled)")
        finally:
            sdk.close()

    def test_delete_recreate_replays_second_incarnation(self, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t15.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "delete-me-B",
                              objectKind="core:other", is_episodic=False)
            oid = node_id(proj, "Object", "delete-me-B")
            assert sdk._delete_entity(oid) is True
            sdk.create_entity("object", "delete-me-B",
                              objectKind="core:other", is_episodic=False)
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "delete-me-B")
            assert len(ors) == 2, (
                "delete→recreate must journal a second first-registration "
                "(the probe misses post-delete)")
            live_rows = _object_row(proj, "delete-me-B", "createdAt")
            assert live_rows and live_rows[0][0] == ors[1]["createdAt"], (
                "live node carries the SECOND registration's createdAt")
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "delete-me-B", "createdAt")
            assert rows and rows[0][0] == ors[1]["createdAt"], (
                "replay must reproduce the SECOND incarnation — the "
                "journaled delete must not swallow the later recreate")
        finally:
            sdk.close()


# ── Tests 6-7 (RED): reserved props dropped both lanes + GraphEvent pin ────

class TestReservedProps:
    def test_journaled_line_and_live_node_drop_reserved_props(self, tmp_path):
        """The unconditional point/payload pop: excluded from the journal
        mirror AND not persisted live; envelope keys top-level only; no
        envelope pollution post-rebuild; ObjectRegistered never hits the
        GraphEvent store."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t6.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "reserved-test",
                              objectKind="core:other", is_episodic=False,
                              point="x", payload="y")
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "reserved-test")
            assert len(ors) == 1, ors
            line = ors[0]
            assert "point" not in line and "payload" not in line, line
            for k in ("event_id", "ts", "type", "initiated_by",
                      "projection_version"):
                assert k in line, f"envelope key {k} missing: {line}"
            rows = _object_row(proj, "reserved-test")
            assert rows, "object must exist live"
            node = rows[0][0]
            assert "point" not in node and "payload" not in node, node
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "reserved-test")
            node = rows[0][0]
            for k in ("event_id", "ts", "initiated_by", "projection_version"):
                assert k not in node, f"envelope key {k} polluted node: {node}"
            # GraphEvent-store membership pin: ObjectRegistered ∉
            # _GRAPH_EVENT_TYPES → JSONL-only (BatchIdStamped precedent,
            # test_ingest_bundle.py:1086)
            r = proj.g.query(
                "MATCH (e:GraphEvent {type:'ObjectRegistered'}) "
                "RETURN count(e)").result_set
            assert r and r[0][0] == 0, (
                "ObjectRegistered must never write the GraphEvent store")
        finally:
            sdk.close()

    def test_no_log_sdk_no_journal_and_unconditional_pop(self, tmp_path):
        """RED: journal-less SDK Object create with scalar reserved props —
        no log, no synthesis artifacts, and (NEW behavior) the reserved props
        are dropped live (pre-fix only label == 'Event' popped)."""
        sdk = TortoiseSDK(str(tmp_path / "t7.db"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "no-log-test",
                              objectKind="core:other", is_episodic=False,
                              point="x", payload="y")
            assert not (tmp_path / "events").exists(), "no log must be written"
            rows = _object_row(proj, "no-log-test")
            assert rows, "object must exist live"
            node = rows[0][0]
            assert "point" not in node and "payload" not in node, (
                "unconditional pop must apply on journal-less SDKs (a "
                f"journal-gated pop would diverge persistence): {node}")
            for k in ("event_id", "ts", "initiated_by", "projection_version"):
                assert k not in node, f"envelope key {k} on node: {node}"
            assert "createdAt" in node, (
                "createdAt from the projection path (coalesce $now)")
        finally:
            sdk.close()


# ── Test 8 (green-pin): fold-miss warning firing path ──────────────────────

class TestFoldMissWarning:
    def test_fold_miss_warning_fires_for_unregistered_target(self, tmp_path,
                                                             caplog):
        """Green-pin: a journaled ObjectSuperseded with no journaled
        registration must fire the fold-miss warning at rebuild (caplog
        level, not string — the T4.4 reword must survive) and must NOT
        fabricate the Object.

        The run itself now PASSES: since #3590 D2 (#10107) a target the
        journal never registered is exempt (`object-superseded-unregistered`,
        still RECORDED by the run boundary) because live folded nothing either.
        ⚠️ RETIRED CONTRACT: this test used to require
        `NonFoldedEventsError` here. The refusal it pinned did not disappear —
        it moved to a name the journal DID establish, and lives in
        `tests/test_rebuild_parity_fail_loud_3585.py`
        (`test_name_only_supersede_of_registered_then_deleted_name_still_refuses`,
        `test_name_from_a_rename_counts_as_established_both_engines`). Do not
        re-add the raise here: it would re-pin the exemption's own defect.
        """
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t8.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            # #3590: an id to journal against for a name with NO node — an
            # opaque value is all it can be now (the derived id is retired).
            oid = ulid()
            sdk._emit_event("ObjectSuperseded", id=oid, name="ghost-name",
                            supersedes_by="successor-name")
            import logging
            with caplog.at_level(logging.WARNING,
                                 logger="tortoise.projection"):
                proj.rebuild_all(str(events), confirm_destructive=True)
            assert any("fold" in r.message.lower() and
                       "match" in r.message.lower() for r in caplog.records), (
                "fold-miss warning must fire for an unregistered target")
            rows = _object_row(proj, "ghost-name")
            assert not rows, "no phantom resurrection of the unregistered Object"
        finally:
            sdk.close()


# ── Test 9 (green-pin): duplicate-registration replay idempotency ──────────

class TestDuplicateReplay:
    def test_duplicate_registration_lines_replay_idempotently(self, tmp_path):
        """Green-pin: two ObjectRegistered lines for one name (the probe-
        window race shape) with DIFFERENT createdAt values + a fold → rebuild
        yields one node, superseded, createdAt == FIRST line's value (the ON
        CREATE / ON MATCH coalesce(o.createdAt, $ca) split must not flip it).
        Built entirely manually so it means the same pre-fix and post-fix."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            # #3590: any opaque id — this test builds both registrations by
            # hand, so the value only has to be ONE id.
            oid = ulid()
            first_ts = "2026-01-01T00:00:00+00:00"
            second_ts = "2026-06-01T00:00:00+00:00"
            sdk._emit_event("ObjectRegistered", id=oid, name="race-name",
                            object_kind="core:other", createdAt=first_ts)
            sdk._emit_event("ObjectRegistered", id=oid, name="race-name",
                            object_kind="core:other", createdAt=second_ts)
            sdk._emit_event("ObjectSuperseded", id=oid, name="race-name",
                            supersedes_by="winner-name")
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "race-name", "status", "createdAt",
                               "supersededBy")
            assert len(rows) == 1, "exactly one Object node after dup replay"
            assert rows[0][0] == "superseded", rows[0]
            assert rows[0][1] == first_ts, (
                "createdAt must be the FIRST line's value, not the second's")
            assert rows[0][2] == "winner-name", rows[0]
        finally:
            sdk.close()


# ── Tests 10-13 (post-implementation pins) ─────────────────────────────────

class TestFailureInjection:
    def test_probe_failure_fails_open_to_journal(self, tmp_path, monkeypatch,
                                                  caplog):
        """A probe-query raise must NOT fail the create: warning + optimistic
        journal (durable bias) — a regression to fail-closed (silent skip)
        would re-open the node-loss bug."""
        import logging

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t10.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            # _GuardedGraph uses __slots__ — patch at class level.
            guarded_type = type(proj.g)
            orig_query = guarded_type.query

            def _raise_on_probe(self, cypher, params=None, timeout=None):
                if "id:$cid" in cypher:
                    raise RuntimeError("probe boom")
                return orig_query(self, cypher, params=params,
                                  timeout=timeout)

            monkeypatch.setattr(guarded_type, "query", _raise_on_probe)
            with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
                sdk.create_entity("object", "fail-open-name",
                                  objectKind="core:other", is_episodic=False)
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "fail-open-name")
            assert len(ors) == 1, (
                "probe failure must fail OPEN to journaling (durable bias): "
                f"{ors}")
            assert _object_row(proj, "fail-open-name"), "create must succeed"
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert _object_row(proj, "fail-open-name"), (
                "fail-open journal must let rebuild restore the node")
        finally:
            sdk.close()

    def test_log_append_failure_warns_and_keeps_live(self, tmp_path,
                                                     monkeypatch, caplog):
        """An EventLog.append raise must not crash the create: warning logged,
        node live; the accepted consequence — rebuild omits the Object (no
        registration line; #2296 backstop covers the loss)."""
        import logging

        from tortoise.log import EventLog

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t11.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            orig_append = EventLog.append

            def _raise_append(self, event):
                raise OSError("disk full (test)")

            monkeypatch.setattr(EventLog, "append", _raise_append)
            with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
                sdk.create_entity("object", "append-fail-name",
                                  objectKind="core:other", is_episodic=False)
            assert _object_row(proj, "append-fail-name"), (
                "append failure must not crash the create; node stays live")
            assert any("failed to append" in r.getMessage()
                       for r in caplog.records), caplog.records
            monkeypatch.setattr(EventLog, "append", orig_append)
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert not _object_row(proj, "append-fail-name"), (
                "accepted consequence: rebuild omits the Object whose "
                "registration line was lost")
        finally:
            sdk.close()

    def test_registration_without_fold_line_restores_live_on_rebuild(
            self, tmp_path, monkeypatch, caplog):
        """Fold-side append failure: live A is superseded but the journal
        holds only the registrations → rebuild restores A LIVE (journal-
        consistent), no phantom fold, no fold-miss warning."""
        import logging

        from tortoise.commit_ops import apply_supersessions
        from tortoise.log import EventLog

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t12.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "strategy-B",
                              objectKind="core:strategy", is_episodic=False)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            orig_append = EventLog.append

            def _raise_on_fold(self, event):
                if event.get("type") == "ObjectSuperseded":
                    raise OSError("fold-line append failed (test)")
                return orig_append(self, event)

            monkeypatch.setattr(EventLog, "append", _raise_on_fold)
            with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
                assert apply_supersessions(
                    proj, sdk,
                    [{"superseded": "strategy-A",
                      "supersedes_by": "strategy-B", "evidence": "fold"}],
                    session_id="s1") == 1, (
                    "the LIVE fold still applies (count-verified)")
            # live A superseded; journal has NO ObjectSuperseded line
            rows = _object_row(proj, "strategy-A", "status")
            assert rows and rows[0][0] == "superseded", rows
            monkeypatch.setattr(EventLog, "append", orig_append)
            journal = _journaled(sdk, events)
            assert not [e for e in journal
                        if e.get("type") == "ObjectSuperseded"], journal
            proj.rebuild_all(str(events), confirm_destructive=True)
            rows = _object_row(proj, "strategy-A", "status")
            assert rows and rows[0][0] is None, (
                "journal-consistent outcome: without the fold line, rebuild "
                "restores the derived-or-absent birth status (absent READS "
                f"live), not superseded: {rows}")
            assert not any("fold" in r.getMessage() and
                           "match" in r.getMessage().lower()
                           for r in caplog.records), (
                "no fold-miss warning — the warning only fires for folds "
                "present in the journal")
        finally:
            sdk.close()


class TestLineOrder:
    def test_capture_journal_line_order_registration_before_fold(
            self, tmp_path):
        """Synchronous post-apply emission: ObjectRegistered lines PRECEDE
        the ObjectSuperseded line in a capture journal — load-bearing for the
        single-log chronological rebuild() path (NO deferred fold sweep),
        which would otherwise resurrect the superseded Object."""
        from tortoise.commit_ops import apply_supersessions

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t13.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "strategy-B",
                              objectKind="core:strategy", is_episodic=False)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            assert apply_supersessions(
                proj, sdk,
                [{"superseded": "strategy-A", "supersedes_by": "strategy-B",
                  "evidence": "fold"}],
                session_id="s1") == 1
            journal = _journaled(sdk, events)
            types = [e.get("type") for e in journal
                     if e.get("type") in ("ObjectRegistered",
                                          "ObjectSuperseded")]
            assert types.index("ObjectSuperseded") > types.index(
                "ObjectRegistered"), types
            # single-log chronological rebuild (no deferred sweep): with
            # [OR..., OS] order the fold applies — the Object stays superseded.
            from tortoise.log import EventLog
            proj.rebuild(EventLog(str(events / "events.jsonl")), confirm_destructive=True)
            rows = _object_row(proj, "strategy-A", "status", "supersededBy")
            assert rows and rows[0][0] == "superseded", rows
            assert rows[0][1] == "strategy-B", rows[0]
        finally:
            sdk.close()


class TestCasReplayParity:
    """#2242: the live-path CAS must NOT leak into replay. Rebuild/restore
    fold ObjectSuperseded lines BLIND (cas=False default) — byte-identical
    to pre-CAS: duplicate/divergent lines (the `test_dup_*` case here, and the
    rest of this file's fold sequences) are applied blind on BOTH replay
    surfaces (rebuild_all sweep + rebuild(EventLog) chronological dispatch).

    ⛔ The `incarnation-reuse` (delete→recreate→re-supersede on ONE id) shape
    named here until #3590 is RETIRED with the name-derived id: a re-create
    mints a NEW id (R5), and a journal that re-registers a hard-deleted id is
    now REFUSED as a non-exempt `object-superseded-miss` (measured — see the
    re-created-incarnation test below).
    """

    def test_fold_reg_fold_recreate_replays_by_id_without_resurrection(
            self, tmp_path, caplog):
        """delete→recreate: [Reg(id1), OS(id1→B), <delete id1>, Reg(id2),
        OS(id2→C)] replays BY ID on both surfaces, with no resurrection.

        The #3590 replacement for the retired incarnation-reuse shape: the
        re-creation is a NEW incarnation (R5 — measured by the id assertion
        below), so the second fold lands on `id2` and the deleted `id1` must
        stay absent. Live and both replay surfaces must agree exactly — a
        name-keyed fold would instead apply both supersedes to whichever
        carrier the name resolved to, and a replay-time mint would produce a
        third id (live != replay). `object-superseded-miss` is non-exempt, so
        a fold that lands on no incarnation fails the run rather than
        silently dropping the supersede — hence the `caplog` assertion that
        the sweep reported no such miss.
        """
        import logging
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t16.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            name = "fr-B"
            sdk.create_entity("object", name, objectKind="core:other",
                              is_episodic=False)                  # Reg 1
            oid = node_id(proj, "Object", name)
            sdk._emit_event("ObjectSuperseded", id=oid, name=name,
                            supersedes_by="fr-succ-B",
                            evidence="first fold")
            assert sdk._delete_entity(oid) is True               # journaled
            sdk.create_entity("object", name, objectKind="core:other",
                              is_episodic=False)                 # Reg 2
            second = node_id(proj, "Object", name)
            assert second != oid, (
                "the re-create reused the deleted incarnation's id — "
                f"resurrection (#3590 R5): {oid}")
            sdk._emit_event("ObjectSuperseded", id=second, name=name,
                            supersedes_by="fr-succ-C",
                            evidence="second fold")
            # MEASURED, not assumed: the "incarnation reuse" journal (Reg 2
            # rewritten onto the DELETED incarnation's id) is now REFUSED —
            # `object-superseded-miss` seq=4, non-exempt, `rebuild_all` raises
            # `NonFoldedEventsError` before this test could assert anything. It
            # is the deferred-sweep ordering: both registrations collapse onto
            # one id-keyed node, the seq-3 delete then removes it, and the
            # seq-4 supersede has no incarnation left. So live == replay is
            # asserted on the shape the code can produce.
            # ⛔ NO live-state assertion here: `_emit_event` appends the record
            # but the LIVE supersede is applied by `commit_ops`'s own sweep, so
            # this journal's live node is still `live`. The observable this
            # class is about is what REPLAY folds from the records — exactly as
            # the pre-#3590 test asserted.
            expected = {second: ("superseded", "fr-succ-C")}
            with caplog.at_level(logging.WARNING,
                                 logger="tortoise.projection"):
                proj.rebuild_all(str(events), confirm_destructive=True)
            rebuilt = {r[0]: (r[1], r[2]) for r in proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id, o.status, o.supersededBy",
                params={"n": name}).result_set}
            assert rebuilt == expected, (
                "rebuild_all must fold both supersede lines BY ID onto the "
                f"current incarnation (pre-CAS parity): {rebuilt!r}")
            assert oid not in rebuilt, (
                "replay RESURRECTED the deleted incarnation (#3590 R5)")
            # The EXPECTED miss is the exempt `supersede-target-deleted` on the
            # deleted incarnation (seq=1) — a named, bounded exemption. What must
            # NOT happen is the CURRENT incarnation's fold (fr-succ-C) missing,
            # which would be the non-exempt `object-superseded-miss`.
            assert not any("matched no Object" in r.getMessage()
                           and "fr-succ-C" in r.getMessage()
                           for r in caplog.records), [
                r.getMessage() for r in caplog.records]
            # dispatch arm — chronological rebuild (the backup-restore path)
            proj.rebuild(EventLog(str(events / "events.jsonl")), confirm_destructive=True)
            rebuilt = {r[0]: (r[1], r[2]) for r in proj.g.query(
                "MATCH (o:Object {name:$n}) RETURN o.id, o.status, o.supersededBy",
                params={"n": name}).result_set}
            assert rebuilt == expected, (
                "rebuild() dispatch must agree with rebuild_all on the "
                f"re-created incarnation: {rebuilt!r}")
        finally:
            sdk.close()

    def test_dup_fold_lines_unchanged_silent(self, tmp_path, caplog):
        """Duplicate ObjectSuperseded lines (crash-retry dup class, same
        successor) replay SILENTLY with the pre-CAS parity outcome on both
        replay surfaces.

        REVIEW (round 1, P1): the dup shape is INVARIANT under first-wins on
        status+supersededBy alone (both modes write dup-succ) — a cas=True
        leak into the sweep would CAS-miss the second line and survive a
        status+supersededBy-only assert. The discriminating observables are
        (a) NO "matched no Object" sweep warn (a leaked CAS fold on the
        terminal second line emits it) and (b) supersededAt == the SECOND
        line's ts (blind last-wins keeps the later stamp; a CAS leak keeps
        the first)."""
        import logging
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t17.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            name = "dup-B"
            sdk.create_entity("object", name, objectKind="core:other",
                              is_episodic=False)
            # #3590: `node_id` reads the graph, so it resolves AFTER the write
            # that creates the node (it is deliberately not a minter).
            oid = node_id(proj, "Object", name)
            sdk._emit_event("ObjectSuperseded", id=oid, name=name,
                            supersedes_by="dup-succ", evidence="fold 1",
                            ts="2026-09-07T10:00:00Z")
            sdk._emit_event("ObjectSuperseded", id=oid, name=name,
                            supersedes_by="dup-succ", evidence="fold 2 (dup)",
                            ts="2026-09-07T11:00:00Z")
            for surface in ("rebuild_all", "rebuild"):
                with caplog.at_level(logging.WARNING,
                                     logger="tortoise.projection"):
                    if surface == "rebuild_all":
                        proj.rebuild_all(str(events), confirm_destructive=True)
                    else:
                        proj.rebuild(
                            EventLog(str(events / "events.jsonl")),
                            confirm_destructive=True)
                rows = _object_row(proj, name, "status", "supersededBy",
                                   "supersededAt")
                assert rows and rows[0][:2] == ["superseded", "dup-succ"], (
                    f"[{surface}] dup lines same outcome: {rows}")
                assert rows[0][2] == "2026-09-07T11:00:00Z", (
                    f"[{surface}] blind last-wins must keep the SECOND "
                    f"line's ts (a cas leak keeps the first): {rows}")
                assert not any("matched no Object" in r.message
                               for r in caplog.records), (
                    f"[{surface}] dup lines must replay SILENTLY — a leaked "
                    f"CAS fold on the terminal second line warns")
                caplog.clear()
        finally:
            sdk.close()


# ── #7951: THE JOURNAL ORDERING CONTRACT ────────────────────────────────────
#
# The contract (and its failure-mode argument) is at the top of
# `tortoise/sdk.py`: **journal first, then project.** `_apply_and_journal` is
# the sanctioned live door for a pre-built entity record; the append is the
# COMMIT POINT and the fold follows. These tests pin the rule rather than the
# docstring:
#   * a failure injected into the FOLD leaves the record on the TRUTH journal
#     and NO node in the derived graph — and a replay heals the lag;
#   * a failed APPEND is not followed by a fold (the derived store must never
#     lead the journal).
# Under the old apply-first ordering the first test's record would never have
# reached the journal and the write would be lost for good.

class TestJournalOrderingContract:
    @staticmethod
    def _record():
        return {
            "type": "ObjectRegistered",
            "id": "obj-order-contract",
            "name": "order-contract-object",
            "object_kind": "core:strategy",
            "createdAt": "2026-01-01T00:00:00Z",
        }

    def test_apply_failure_keeps_journal_committed_and_replay_heals(
            self, tmp_path, monkeypatch):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "order.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            real_apply = type(proj).apply

            def _fail_apply(self, event, *a, **kw):
                # ORDER is asserted at the fold itself: the append has already
                # happened when the fold runs.
                assert [e for e in _journaled(sdk, events)
                        if e.get("type") == "ObjectRegistered"], (
                    "the fold ran before the append — the journal ordering "
                    "contract is violated (#7951)")
                raise RuntimeError("fold boom (test)")

            monkeypatch.setattr(type(proj), "apply", _fail_apply)
            with pytest.raises(RuntimeError, match="fold boom"):
                sdk._apply_and_journal(proj, self._record())
            # (1) TRUTH committed — the record is on the journal.
            assert [e for e in _journaled(sdk, events)
                    if e.get("type") == "ObjectRegistered"], "journal committed"
            # (2) DERIVED lags — the fold failed, so there is no node.
            assert not _object_row(proj, "order-contract-object"), (
                "the fold failed, so the graph must only LAG — no node")
            monkeypatch.setattr(type(proj), "apply", real_apply)
            # (3) RECOVERY — replay materialises the committed record.
            proj.rebuild_all(str(events), confirm_destructive=True)
            assert _object_row(proj, "order-contract-object"), (
                "replay must heal the journal-ahead window (#7951 contract)")
        finally:
            sdk.close()

    def test_failed_append_is_not_followed_by_a_fold(self, tmp_path,
                                                     monkeypatch):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "order2.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()

            def _raise_append(self, event):
                raise OSError("disk full (test)")

            monkeypatch.setattr(EventLog, "append", _raise_append)
            sdk._apply_and_journal(proj, self._record())
            assert not _object_row(proj, "order-contract-object"), (
                "a failed append must not be followed by a fold — the derived "
                "store must never lead the journal (#7951 contract)")
            assert not [e for e in _journaled(sdk, events)
                        if e.get("type") == "ObjectRegistered"]
        finally:
            sdk.close()


# ── #7951: CONTRACT EXCEPTION 1 — a replay engine must not journal ──────────
#
# `tortoise reconcile` (`__main__.py`) reads the journal and folds the records
# it finds missing from the graph. It is a REPLAY engine, not a writer, so the
# contract forbids it from journaling: appending would write back the very
# records it just read, growing the log on every repair. The work list flagged
# this `proj.apply` site as a candidate; it is not.

class TestReconcileIsApplyOnly:
    def test_reconcile_folds_without_appending_to_the_journal(self, tmp_path,
                                                              monkeypatch):
        from types import SimpleNamespace

        from tortoise import __main__ as cli
        from tortoise.projection import FalkorProjection

        events = tmp_path / "events.jsonl"
        EventLog(str(events)).append({
            "version": "1.0",
            "type": "EventRecorded",
            "event": {
                "eventId": "recon-7951",
                "eventKind": "meeting",
                "subject": "alice",
                "object": "session-7951",
                "startedAt": "2026-01-01T00:00:00Z",
            },
            "createdAt": "2026-01-01T00:00:00Z",
        })
        before = events.read_bytes()

        sdk = TortoiseSDK(str(tmp_path / "recon.db"))
        proj = sdk._get_proj()
        monkeypatch.setattr("tortoise.config.is_docker_uri", lambda *_: True)
        monkeypatch.setattr(FalkorProjection, "from_uri",
                            classmethod(lambda cls, uri, graph_name=None: proj))
        # ``_cmd_reconcile`` closes its projection; keep ours open to observe.
        monkeypatch.setattr(FalkorProjection, "close", lambda self: None)
        try:
            rc = cli._cmd_reconcile(
                SimpleNamespace(log=str(events), db="docker://fake"))
            assert rc == 0
            # A replay engine folds but does NOT journal.
            assert events.read_bytes() == before, (
                "reconcile appended to the journal — a replay engine must be "
                "APPLY-ONLY (#7951 contract, exception 1)")
            assert proj.g.query(
                "MATCH (e:Event {eventId:'recon-7951'}) RETURN count(e)"
            ).result_set[0][0] == 1
        finally:
            sdk.close()
