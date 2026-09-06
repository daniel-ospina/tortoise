"""#2295 — SubjectAdded journaling for SDK-created Subjects.

SDK-created Subjects (``create_entity("subject")`` → ``_create_entity`` with
``SubjectAdded``, deterministic ``sub-<sha26(name)>`` id) must survive
``rebuild_all``: ``_create_entity`` journals ``SubjectAdded`` on FIRST
canonical registration (probe-gated — a canonical re-mention never
double-journals), mirroring the merged #2194 Object fix.

Test classes:
- RED (missing-line class — fail at base, no SubjectAdded exists pre-fix):
  round-trip byte-identity, re-mention, stub adoption + EventAPI-lane
  delta-1 variant, reserved props, no-log pop, probe fail-open,
  append-fail, delete pins, EventAPI-coexistence.
- Green-pins (pass at base BY CONSTRUCTION; discriminate a wrong/missing
  fix): duplicate-replay first-wins, falsy-name no-phantom-line.

Subject-vs-Object deltas (#2295 plan §Delta): status rides extra-props for
Subjects (NOT projection-owned — ``_SUBJECT_HANDLED`` lacks status), so the
stub test asserts status PRESENT live (INVERTED from the Object suite's
status-absent); EventAPI ``add_subject`` mints random ulids with no
canonical override (re-id + by-design double-registration pinned).
"""
from __future__ import annotations

import os

sys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
import sys  # noqa: E402

sys.path.insert(0, sys_path)

import pytest  # noqa: E402

from tortoise.ids import ulid  # noqa: E402
from tortoise.log import EventLog  # noqa: E402
from tortoise.sdk import TortoiseSDK, _entity_name_id  # noqa: E402


def _journaled(sdk, events_dir):
    return EventLog(str(events_dir / "events.jsonl")).read_all()


def _sas(journal):
    return [e for e in journal if e.get("type") == "SubjectAdded"]


def _name_sas(journal, name):
    return [e for e in _sas(journal) if e.get("name") == name]


def _subject_row(proj, name, *props):
    """Return result rows for a Subject by name.

    With explicit props → rows of scalars. Without props → the full node as a
    properties dict (RETURN properties(s) — FalkorDB RETURN s yields a Node
    object, not a dict).
    """
    if props:
        cols = ", ".join(f"s.{p}" for p in props)
        r = proj.g.query(
            f"MATCH (s:Subject {{name:$n}}) RETURN {cols}", params={"n": name})
    else:
        r = proj.g.query(
            "MATCH (s:Subject {name:$n}) RETURN properties(s)",
            params={"n": name})
    return r.result_set


def _api_log(proj, path):
    """EventAPI sharing the SDK's projection (same graph) with its OWN log."""
    from tortoise.api import EventAPI
    return EventAPI(EventLog(str(path)), initiated_by="extractor",
                    agent_id="test", projection=proj)


# ── Test 1 (RED): plain Subject round-trip byte-identity ───────────────────

class TestRoundTrip:
    @pytest.mark.parametrize("name", ["acme", "estrategia-ñ-日本語-💡"])
    @pytest.mark.parametrize("is_episodic", [
        True, False,
        pytest.param(None, id="not-passed"),
    ])
    def test_plain_subject_survives_rebuild_all_byte_identical(
            self, tmp_path, name, is_episodic):
        """Indicator 1+2: auto SubjectAdded exists per fresh create; the FULL
        replayed property set equals the pre-wipe live snapshot; createdAt ==
        journaled createdAt (no rebuild-time drift); is_episodic round-trips
        (True/False exercise the extras-lane bool; not-passed stays absent).

        Parametrized over ASCII + non-ASCII names (digest/param regression on
        non-ASCII would silently fail-closed on genuinely-new registrations)
        and all three is_episodic states."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t1.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            if is_episodic is None:
                sdk.create_entity("subject", name,
                                  subjectKind="core:team")
            else:
                sdk.create_entity("subject", name,
                                  subjectKind="core:team",
                                  is_episodic=is_episodic)
            live_rows = _subject_row(proj, name)
            assert live_rows, "subject must exist live"
            live_props = {k: v for k, v in live_rows[0][0].items()
                          if k != "embedding"}
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, name)
            assert len(sas) == 1, (
                f"exactly one SubjectAdded expected: {sas}")
            line = sas[0]
            assert line["id"] == _entity_name_id("Subject", name), line
            assert line["name"] == name
            assert line["subject_kind"] == "core:team", line
            assert line["subjectKind"] == "core:team", line
            assert line["status"] == "live", line
            assert line.get("createdAt"), line
            if is_episodic is None:
                assert "is_episodic" not in line, line
            else:
                assert line["is_episodic"] == is_episodic, line
            proj.rebuild_all(str(events))
            rebuilt = _subject_row(proj, name)
            assert rebuilt, "Subject must survive rebuild"
            rebuilt_props = {k: v for k, v in rebuilt[0][0].items()
                             if k != "embedding"}
            assert rebuilt_props == live_props, (
                f"replayed prop set diverges from live: "
                f"live={live_props!r} rebuilt={rebuilt_props!r}")
            assert rebuilt[0][0].get("createdAt") == line["createdAt"], (
                "createdAt must not drift to rebuild time")
        finally:
            sdk.close()


# ── Test 2 (RED): only-on-create semantics ─────────────────────────────────

class TestOnlyOnCreate:
    def test_remenion_does_not_double_journal_and_subjectkind_churn_live_only(
            self, tmp_path):
        """A canonical re-mention never double-journals; its subjectKind
        mutation is live-only. Casing pins: live node `subjectKind` == the
        SECOND value; journal line `subject_kind` (snake) == the FIRST value;
        rebuilt node `subjectKind` == the FIRST value (journal keeps the
        first registration — accepted only-on-create divergence)."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t2.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("subject", "strategy-owner",
                              subjectKind="core:org", is_episodic=False)
            sdk.create_entity("subject", "strategy-owner",
                              subjectKind="core:team", is_episodic=False)
            # the mutation IS live (ON MATCH subjectKind coalesce updates)
            rows = _subject_row(proj, "strategy-owner", "subjectKind")
            assert rows and rows[0][0] == "core:team", rows
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, "strategy-owner")
            assert len(sas) == 1, (
                f"re-mention must NOT double-journal: {len(sas)} lines")
            assert sas[0]["subject_kind"] == "core:org", (
                "journal must hold FIRST-registration subject_kind, not the "
                "churned live value")
            proj.rebuild_all(str(events))
            rebuilt = _subject_row(proj, "strategy-owner", "subjectKind")
            assert rebuilt and rebuilt[0][0] == "core:org", (
                "rebuild reverts to first-registration subjectKind "
                "(accepted only-on-create divergence)")
        finally:
            sdk.close()


# ── Tests 3 + 3v (RED): stub adoption ──────────────────────────────────────

class TestStubAdoption:
    def test_stub_adoption_journals_canonicalization(self, tmp_path):
        """#1918/#2295 heal: a name-stub under a random ulid (raw CREATE,
        connector produces-edge shape — the ``_event_plain_merge`` Subject
        mint has no createdAt) adopted by an SDK create must journal the
        canonical registration (probe by canonical id+name misses) AND adopt
        createdAt on the live node (ON MATCH coalesce — delta 1, the
        load-bearing edit the issue body missed) so rebuild restores the
        canonical node with the journaled id + createdAt.

        status is PRESENT live (INVERTED from the Object suite's status-absent
        assert): Subject status rides _persist_extra_props on both lanes
        (∉ _SUBJECT_HANDLED), so the adopted live node AND the rebuilt node
        both carry status:'live'."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t3.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            stub_id = ulid()
            proj.g.query(
                "CREATE (s:Subject {name:$n, id:$id})",
                params={"n": "connector-person", "id": stub_id})
            sdk.create_entity("subject", "connector-person",
                              subjectKind="core:other", is_episodic=False)
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, "connector-person")
            assert len(sas) == 1, (
                "stub adoption must journal the canonical registration "
                f"(probe by canonical id+name found no canonical row): {sas}")
            line = sas[0]
            assert line["id"] == _entity_name_id("Subject",
                                                 "connector-person")
            # live adopted node carries the journaled createdAt (ON MATCH
            # coalesce(s.createdAt, $ca)) — delta-1 discriminator
            rows = _subject_row(proj, "connector-person", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "stub must be canonicalized to the sub-<sha26> id")
            assert rows[0][1] == line["createdAt"], (
                "live adopted createdAt must equal the journaled value "
                "(delta 1 ON MATCH adoption)")
            # status PRESENT (delta-3 inversion — Subject status rides extras)
            live_props = _subject_row(proj, "connector-person")[0][0]
            assert live_props.get("status") == "live", live_props
            proj.rebuild_all(str(events))
            rows = _subject_row(proj, "connector-person", "id", "createdAt")
            assert rows and rows[0][0] == line["id"], (
                "rebuild must restore the canonical id, not the stub ulid")
            assert rows[0][1] == line["createdAt"], rows[0]
            rebuilt_props = _subject_row(proj, "connector-person")[0][0]
            assert rebuilt_props.get("status") == "live", rebuilt_props
        finally:
            sdk.close()

    def test_eventapi_mention_adopts_created_at_on_stub(self, tmp_path):
        """delta-1-only RED variant: a createdAt-LESS raw stub adopted by an
        EventAPI ``add_subject`` (which journals UNCONDITIONALLY at base —
        no probe in the EventAPI lane) must get the journaled createdAt on
        the live node. RED at base for delta 1 ALONE: the line exists but
        ``_upsert_subject`` ON MATCH has no createdAt clause → live node
        stays createdAt-less. Post-fix pins the EventAPI-mention parity
        clause in delta 1's edit comment."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t3v.db"))
        try:
            proj = sdk._get_proj()
            api = _api_log(proj, events / "api.jsonl")
            stub_id = ulid()
            proj.g.query(
                "CREATE (s:Subject {name:$n, id:$id})",
                params={"n": "mention-person", "id": stub_id})
            api.add_subject("mention-person")
            journal = EventLog(str(events / "api.jsonl")).read_all()
            sas = _name_sas(journal, "mention-person")
            assert len(sas) == 1, sas
            line = sas[0]
            assert line.get("createdAt"), line
            rows = _subject_row(proj, "mention-person", "createdAt")
            assert rows and rows[0][0] == line["createdAt"], (
                "live stub adopted by an EventAPI mention must carry the "
                "journaled createdAt (delta 1 ON MATCH adoption — EventAPI "
                f"parity): live={rows!r} line_ts={line['createdAt']!r}")
        finally:
            sdk.close()


# ── Green-pin: duplicate-registration replay idempotency ───────────────────

class TestDuplicateReplay:
    def test_duplicate_registration_lines_replay_idempotently(self, tmp_path):
        """Green-pin: two SubjectAdded lines for one name (the probe-window
        race shape) with DIFFERENT createdAt values → rebuild yields one node,
        createdAt == FIRST line's value (the ON CREATE / ON MATCH
        coalesce(s.createdAt, $ca) split must not flip it — a REVERSED
        coalesce would also churn LIVE createdAt on every EventAPI mention,
        an only-on-create violation). Built entirely manually so it means the
        same pre-fix and post-fix."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t4.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            oid = _entity_name_id("Subject", "race-person")
            first_ts = "2026-01-01T00:00:00+00:00"
            second_ts = "2026-06-01T00:00:00+00:00"
            sdk._emit_event("SubjectAdded", id=oid, name="race-person",
                            subject_kind="core:other", createdAt=first_ts)
            sdk._emit_event("SubjectAdded", id=oid, name="race-person",
                            subject_kind="core:other", createdAt=second_ts)
            proj.rebuild_all(str(events))
            rows = _subject_row(proj, "race-person", "id", "createdAt")
            assert len(rows) == 1, "exactly one Subject node after dup replay"
            assert rows[0][0] == oid, rows[0]
            assert rows[0][1] == first_ts, (
                "createdAt must be the FIRST line's value, not the second's")
        finally:
            sdk.close()


# ── Tests 5-6 (RED): reserved props dropped both lanes + GraphEvent pin ────

class TestReservedProps:
    def test_journaled_line_and_live_node_drop_reserved_props(self, tmp_path):
        """The unconditional point/payload pop (widen to Subject): excluded
        from the journal mirror AND not persisted live; envelope keys
        top-level only; no envelope pollution post-rebuild; SubjectAdded
        never hits the GraphEvent store."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t5.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("subject", "reserved-person",
                              subjectKind="core:other", is_episodic=False,
                              point="x", payload="y")
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, "reserved-person")
            assert len(sas) == 1, sas
            line = sas[0]
            assert "point" not in line and "payload" not in line, line
            for k in ("event_id", "ts", "type", "initiated_by",
                      "projection_version"):
                assert k in line, f"envelope key {k} missing: {line}"
            rows = _subject_row(proj, "reserved-person")
            assert rows, "subject must exist live"
            node = rows[0][0]
            assert "point" not in node and "payload" not in node, node
            proj.rebuild_all(str(events))
            rows = _subject_row(proj, "reserved-person")
            node = rows[0][0]
            for k in ("event_id", "ts", "initiated_by", "projection_version"):
                assert k not in node, f"envelope key {k} polluted node: {node}"
            # GraphEvent-store membership pin: SubjectAdded ∉
            # _GRAPH_EVENT_TYPES → JSONL-only (a future promotion to the set
            # would silently double-write the #432 store)
            r = proj.g.query(
                "MATCH (e:GraphEvent {type:'SubjectAdded'}) "
                "RETURN count(e)").result_set
            assert r and r[0][0] == 0, (
                "SubjectAdded must never write the GraphEvent store")
        finally:
            sdk.close()

    def test_no_log_sdk_no_journal_and_unconditional_pop(self, tmp_path):
        """RED: journal-less SDK Subject create with scalar reserved props —
        no log, no synthesis artifacts, and the reserved props are dropped
        live (the widened unconditional pop applies on BOTH lanes)."""
        sdk = TortoiseSDK(str(tmp_path / "t6.db"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("subject", "no-log-person",
                              subjectKind="core:other", is_episodic=False,
                              point="x", payload="y")
            assert not (tmp_path / "events").exists(), "no log must be written"
            rows = _subject_row(proj, "no-log-person")
            assert rows, "subject must exist live"
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


# ── Tests 7-8 (RED): failure injection ─────────────────────────────────────

class TestFailureInjection:
    def test_probe_failure_fails_open_to_journal(self, tmp_path, monkeypatch,
                                                 caplog):
        """A probe-query raise must NOT fail the create: warning + optimistic
        journal (durable bias) — a regression to fail-closed (silent skip)
        would re-open the node-loss bug. The label-scoped fragment matches
        the C1 probe literal; the caplog WARNING assert makes the injection
        REAL (a silent fragment mismatch would false-green — do not inherit
        the Object suite's latent weakness)."""
        import logging

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t7.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            guarded_type = type(proj.g)
            orig_query = guarded_type.query

            def _raise_on_probe(self, cypher, params=None, timeout=None):
                if "s:Subject {id:$cid, name:$name}" in cypher:
                    raise RuntimeError("probe boom")
                return orig_query(self, cypher, params=params,
                                  timeout=timeout)

            monkeypatch.setattr(guarded_type, "query", _raise_on_probe)
            with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
                sdk.create_entity("subject", "fail-open-person",
                                  subjectKind="core:other",
                                  is_episodic=False)
            assert any("probe failed" in r.getMessage()
                       for r in caplog.records), (
                "probe-failure warning must fire (injection was real): "
                f"{[r.getMessage() for r in caplog.records]}")
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, "fail-open-person")
            assert len(sas) == 1, (
                "probe failure must fail OPEN to journaling (durable bias): "
                f"{sas}")
            assert _subject_row(proj, "fail-open-person"), "create must succeed"
            proj.rebuild_all(str(events))
            assert _subject_row(proj, "fail-open-person"), (
                "fail-open journal must let rebuild restore the node")
        finally:
            sdk.close()

    def test_log_append_failure_warns_and_keeps_live(self, tmp_path,
                                                     monkeypatch, caplog):
        """An EventLog.append raise must not crash the create: warning logged,
        node live; the accepted consequence — rebuild omits the Subject (no
        registration line; #2296 backstop covers the loss)."""
        import logging

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t8.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            orig_append = EventLog.append

            def _raise_append(self, event):
                raise OSError("disk full (test)")

            monkeypatch.setattr(EventLog, "append", _raise_append)
            with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
                sdk.create_entity("subject", "append-fail-person",
                                  subjectKind="core:other",
                                  is_episodic=False)
            assert _subject_row(proj, "append-fail-person"), (
                "append failure must not crash the create; node stays live")
            assert any("failed to append" in r.getMessage()
                       for r in caplog.records), caplog.records
            monkeypatch.setattr(EventLog, "append", orig_append)
            proj.rebuild_all(str(events))
            assert not _subject_row(proj, "append-fail-person"), (
                "accepted consequence: rebuild omits the Subject whose "
                "registration line was lost")
        finally:
            sdk.close()


# ── Tests 9a-9b (RED/green-pins): delete non-durability ────────────────────

class TestDeleteNonDurability:
    """Pins for the accepted delete asymmetry (#2295 plan; #2296 scope hook —
    the durability write-surface invariant must cover deletion).

    _delete_entity is a bare DETACH DELETE — no tombstone in the journal
    vocabulary, so a deleted canonical Subject's SubjectAdded line still
    replays: test 9a — deleted Subject RESURRECTS on the next rebuild_all;
    test 9b — delete→recreate journals TWO first-registrations; replay
    first-wins the earlier line's createdAt (≠ the live node's second).
    """

    def test_deleted_subject_resurrects_on_rebuild(self, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9a.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("subject", "delete-me-A",
                              subjectKind="core:other", is_episodic=False)
            oid = _entity_name_id("Subject", "delete-me-A")
            journal = _journaled(sdk, events)
            assert len(_name_sas(journal, "delete-me-A")) == 1
            assert sdk._delete_entity(oid) is True, "node must be deleted"
            rows = _subject_row(proj, "delete-me-A")
            assert not rows, "live node must be gone after delete"
            proj.rebuild_all(str(events))
            rows = _subject_row(proj, "delete-me-A", "createdAt")
            assert rows, (
                "deleted Subject resurrects on rebuild "
                "(no delete tombstone in the journal vocabulary)")
            assert rows[0][0] == journal[0]["createdAt"], (
                "resurrected node carries the journaled createdAt")
        finally:
            sdk.close()

    def test_delete_recreate_replays_first_incarnation(self, tmp_path):
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9b.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk.create_entity("subject", "delete-me-B",
                              subjectKind="core:other", is_episodic=False)
            oid = _entity_name_id("Subject", "delete-me-B")
            assert sdk._delete_entity(oid) is True
            sdk.create_entity("subject", "delete-me-B",
                              subjectKind="core:other", is_episodic=False)
            journal = _journaled(sdk, events)
            sas = _name_sas(journal, "delete-me-B")
            assert len(sas) == 2, (
                "delete→recreate must journal a second first-registration "
                "(the probe misses post-delete)")
            live_rows = _subject_row(proj, "delete-me-B", "createdAt")
            assert live_rows and live_rows[0][0] == sas[1]["createdAt"], (
                "live node carries the SECOND registration's createdAt")
            proj.rebuild_all(str(events))
            rows = _subject_row(proj, "delete-me-B", "createdAt")
            assert rows and rows[0][0] == sas[0]["createdAt"], (
                "replay first-wins the FIRST registration's createdAt — "
                "accepted delete→recreate divergence (#2296 hook)")
        finally:
            sdk.close()


# ── Green-pin: falsy-name no-phantom-line ──────────────────────────────────

class TestFalsyName:
    def test_empty_name_create_does_not_mint_a_phantom_line(self, tmp_path):
        """Green-pin: an empty-name Subject create on a journaled SDK mints
        zero SubjectAdded lines AND zero Subject nodes — the truthy-name gate
        (event.get("name")) mirrors _upsert_subject's falsy-name early-return.
        A gate missing the truthy-name check would mint a phantom line on a
        no-op path (junk-line journal growth). Driven via _create_entity to
        bypass any public validation."""
        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t9c.db"),
                          event_log_path=str(events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            sdk._create_entity("Subject", _entity_name_id("Subject", ""),
                               {"name": "", "subjectKind": "other",
                                "status": "live"}, "SubjectAdded")
            journal = _journaled(sdk, events)
            assert not _sas(journal), f"no phantom lines: {_sas(journal)}"
            rows = _subject_row(proj, "")
            assert not rows, "no Subject node may be minted for an empty name"
        finally:
            sdk.close()


# ── Test 10 (RED): EventAPI random-ulid coexistence (by-design pin) ────────

class TestEventAPICoexistence:
    def test_random_ulid_mention_between_sdk_creates_is_by_design(
            self, tmp_path):
        """By-design double-registration pin (delta 4): EventAPI add_subject
        mints a random ulid with NO canonical override → a mention between
        two SDK creates re-ids the live node (#1918 accepted), so the second
        SDK create probe-misses and journals a second canonical line.

        Layout pin: rebuild_all replays *.jsonl SORTED — B must NOT be
        replayed (replayed FIRST it first-wins B's createdAt ≠ live; replayed
        LAST it re-ids the node to the ulid). Rebuild the SDK log ONLY (A + C
        reproduce live — C already re-converged B's transient re-id live).
        Cross-file replay reorder is an accepted divergence (#330 class)."""
        sdk_events = tmp_path / "sdk_events"
        api_events = tmp_path / "api_events"
        sdk_events.mkdir()
        api_events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t10.db"),
                          event_log_path=str(sdk_events / "events.jsonl"))
        try:
            proj = sdk._get_proj()
            api = _api_log(proj, api_events / "api.jsonl")
            name = "shared-person"
            # A: SDK first canonical registration
            sdk.create_entity("subject", name,
                              subjectKind="core:org", is_episodic=False)
            # B: EventAPI random-ulid mention — re-ids the live node (#1918)
            api.add_subject(name)
            # C: SDK re-mention — probe misses (live id is B's ulid) → line C
            sdk.create_entity("subject", name,
                              subjectKind="core:org", is_episodic=False)
            journal = _journaled(sdk, sdk_events)
            sas = _name_sas(journal, name)
            assert len(sas) == 2, (
                "SDK log must hold exactly two canonical SubjectAdded lines "
                f"(A + C; B lives on the API's own log): {sas}")
            assert sas[0]["id"] == sas[1]["id"] == _entity_name_id(
                "Subject", name), sas
            api_journal = EventLog(str(api_events / "api.jsonl")).read_all()
            assert len(_name_sas(api_journal, name)) == 1, api_journal
            # live: re-canonicalized by C, createdAt first-won A's value
            live_rows = _subject_row(proj, name, "id", "createdAt")
            assert live_rows and live_rows[0][0] == _entity_name_id(
                "Subject", name), live_rows
            assert live_rows[0][1] == sas[0]["createdAt"], (
                "live createdAt must be A's (first registration)")
            # rebuild against the SDK log ONLY
            proj.rebuild_all(str(sdk_events))
            rows = _subject_row(proj, name, "id", "createdAt")
            assert rows and rows[0][0] == _entity_name_id(
                "Subject", name), (
                "rebuilt node must be canonical (B's ulid must not stick)")
            assert rows[0][1] == sas[0]["createdAt"], (
                "rebuilt createdAt must equal the SDK A-line value (== live)")
        finally:
            sdk.close()
