"""#2194 — ObjectRegistered journaling for SDK capture-created Objects.

Capture-created Objects (and their ObjectSuperseded folds) must survive
``rebuild_all``: ``_create_entity`` journals ``ObjectRegistered`` on FIRST
canonical registration (probe-gated — a canonical re-mention never
double-journals), so the pass-1b replay + fold sweep have a node to fold.

Test classes:
- Tests 1-7: RED at base (no journal / no unconditional reserved-prop pop).
- Tests 8-9: green-pin guards (pre-existing behavior the fix must not break).
"""
from __future__ import annotations

import os

sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
import sys  # noqa: E402

sys.path.insert(0, sys_path)

from tortoise.ids import ulid  # noqa: E402
from tortoise.log import EventLog  # noqa: E402
from tortoise.sdk import TortoiseSDK, _entity_name_id  # noqa: E402


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
            proj.rebuild_all(str(events))
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

    def test_plain_object_survives_rebuild_all_byte_identical(self, tmp_path):
        """Indicator 1+3: auto ObjectRegistered exists per fresh create; the
        FULL replayed property set equals the pre-wipe live snapshot;
        createdAt == journaled createdAt (no rebuild-time drift)."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t1b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("object", "acme",
                              objectKind="core:org", is_episodic=False)
            live_rows = _object_row(proj, "acme")
            assert live_rows, "object must exist live"
            live_props = {k: v for k, v in live_rows[0][0].items()
                          if k != "embedding"}
            journal = _journaled(sdk, events)
            ors = _name_ors(journal, "acme")
            assert len(ors) == 1, f"exactly one ObjectRegistered expected: {ors}"
            line = ors[0]
            assert line["id"] == _entity_name_id("Object", "acme"), line
            assert line["name"] == "acme"
            assert line["object_kind"] == "core:org"
            assert line["status"] == "live"
            assert line.get("createdAt"), line
            assert line["is_episodic"] == False  # noqa: E712
            proj.rebuild_all(str(events))
            rebuilt = _object_row(proj, "acme")
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

class TestOnlyOnCreate:
    def test_remenion_does_not_double_journal_and_prop_churn_is_live_only(
            self, tmp_path):
        """Boundary 1 + accepted divergence pin: a canonical re-mention
        (MERGE by name, ON MATCH) never double-journals; its prop mutations
        are live-only — the journal keeps FIRST-registration props and
        rebuild reverts to them."""
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
            assert len(ors) == 1, (
                f"re-mention must NOT double-journal: {len(ors)} lines")
            assert ors[0]["object_kind"] == "dev:issue", (
                "journal must hold FIRST-registration props, not the "
                "churned live value")
            proj.rebuild_all(str(events))
            rebuilt = _object_row(proj, "strategy-A", "objectKind")
            assert rebuilt and rebuilt[0][0] == "dev:issue", (
                "rebuild reverts to first-registration props (accepted "
                "only-on-create divergence)")
        finally:
            sdk.close()

    def test_remenion_after_fold_does_not_journal_or_resurrect(self, tmp_path):
        """A superseded name re-mentioned: zero new ObjectRegistered lines,
        no status reset live, no resurrect on rebuild."""
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
            from tortoise.commit_ops import apply_supersessions
            assert apply_supersessions(
                proj, sdk,
                [{"superseded": "strategy-A", "supersedes_by": "strategy-B",
                  "evidence": "fold"}],
                session_id="s1") == 1
            # re-mention the SUPERSEDED name (post-fix: probe hits → skip)
            sdk.create_entity("object", "strategy-A",
                              objectKind="core:strategy", is_episodic=False)
            rows = _object_row(proj, "strategy-A", "status")
            assert rows and rows[0][0] == "superseded", (
                "re-mention must not reset superseded (#1350 clobber guard)")
            journal = _journaled(sdk, events)
            assert len(_name_ors(journal, "strategy-A")) == 1, (
                "re-mention of a superseded Object must not journal")
            proj.rebuild_all(str(events))
            rows = _object_row(proj, "strategy-A", "status", "supersededBy")
            assert rows and rows[0][0] == "superseded", (
                "no resurrect on rebuild")
            assert rows[0][1] == "strategy-B", rows[0]
        finally:
            sdk.close()


# ── Test 5 (RED): stub adoption ────────────────────────────────────────────

class TestStubAdoption:
    def test_stub_adoption_journals_canonicalization(self, tmp_path):
        """#1155/#2164 ISSUE-B heal: a name-stub under a random ulid (raw
        CREATE, connector produces-edge shape) adopted by an SDK create must
        journal the canonical registration (probe by canonical id+name misses)
        AND adopt createdAt on the live node (ON MATCH coalesce), so rebuild
        restores the canonical node byte-identically."""
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
            assert line["id"] == _entity_name_id("Object", "connector-name")
            # live adopted node carries the journaled createdAt (ON MATCH
            # coalesce(o.createdAt, $ca)) — byte-identity on the stub path
            rows = _object_row(proj, "connector-name", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "stub must be canonicalized to the obj-<sha26> id")
            assert rows[0][1] == line["createdAt"], (
                "live adopted createdAt must equal the journaled value")
            proj.rebuild_all(str(events))
            rows = _object_row(proj, "connector-name", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "rebuild must restore the canonical id, not the stub ulid")
            assert rows[0][1] == line["createdAt"], rows[0]
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
            proj.rebuild_all(str(events))
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
        fabricate the Object."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t8.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            oid = _entity_name_id("Object", "ghost-name")
            sdk._emit_event("ObjectSuperseded", id=oid, name="ghost-name",
                            supersedes_by="successor-name")
            import logging
            with caplog.at_level(logging.WARNING,
                                 logger="tortoise.projection"):
                proj.rebuild_all(str(events))
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
            oid = _entity_name_id("Object", "race-name")
            first_ts = "2026-01-01T00:00:00+00:00"
            second_ts = "2026-06-01T00:00:00+00:00"
            sdk._emit_event("ObjectRegistered", id=oid, name="race-name",
                            object_kind="core:other", createdAt=first_ts)
            sdk._emit_event("ObjectRegistered", id=oid, name="race-name",
                            object_kind="core:other", createdAt=second_ts)
            sdk._emit_event("ObjectSuperseded", id=oid, name="race-name",
                            supersedes_by="winner-name")
            proj.rebuild_all(str(events))
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
            proj.rebuild_all(str(events))
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
            proj.rebuild_all(str(events))
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
            proj.rebuild_all(str(events))
            rows = _object_row(proj, "strategy-A", "status")
            assert rows and rows[0][0] == "live", (
                "journal-consistent outcome: without the fold line, rebuild "
                f"restores live, not superseded: {rows}")
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
            proj.rebuild(EventLog(str(events / "events.jsonl")))
            rows = _object_row(proj, "strategy-A", "status", "supersededBy")
            assert rows and rows[0][0] == "superseded", rows
            assert rows[0][1] == "strategy-B", rows[0]
        finally:
            sdk.close()

