"""#10017 R1 + R2 — a REBUILD must not resurrect the turns a purge removed.

Two recorded rulings from #10017 (owner, 2026-10-26T15:48:12Z), implemented
together because they are two routes to ONE resurrection:

**R2 — the record-carrier event.** On a record-armed deployment the writer
already refuses to mint turn nodes (#10000) but still journalled each turn as
``PointAdded``, a NODE-CREATING record. So a journal rebuild produced a graph
the running deployment did not have, and it re-created every turn #10017's purge
had removed. The carrier is ``TurnRecorded``: ``PointAdded``'s record effect (the
same ``public.point`` row, the same ``record_read`` turn read) and NO graph fold,
with ``cache_tier = 'record_only'`` on the row so ``rebuild_from_record`` skips it
too. Two rebuild routes, one graph.

**R1 — the delete is journaled.** Deleting a legacy turn from the graph emits
``EntityMutated`` with ``op=delete`` through the EXISTING seam
(``_journal_entity_mutation``), carrying the identity of what was removed. Its
record effect is a TIER DEMOTE, not a row delete: the record is the turn's home,
and the transcript is the thing the purge exists to preserve.

⛔ The tests are ordered to make the failure visible, not just the fix: the first
leg reproduces the DEFECT (a delete with no journal record, a rebuild that puts
the turns back). A replay test that cannot show the resurrection when the fix is
removed proves nothing — the mutation named in the commit message
(``_journal_turn_removal`` made a no-op) reddens
``test_a_journaled_removal_survives_the_rebuild``.

Offline only: ``TORTOISE_SESSION_LLM_MOCK=1`` installs the deterministic
MockModel extractor (zero network, zero provider spend), and the record DSN is a
synthetic string whose writer is stubbed — nothing here talks to Postgres. The
record's own statements are asserted as PLANNED SQL (``record_projection``), which
is where the "keep the row" decision lives.
"""

from __future__ import annotations

import pytest

import tortoise.log as log_mod
import tortoise.projection as projection_mod
import tortoise.record_projection as rp
import tortoise.sdk as sdk_mod
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

SESSION_ID = "sess_turn_carrier_10017"
CONV = [
    {"role": "user", "content": "We chose FalkorDB for the hot cache."},
    {"role": "assistant", "content": "Noted, that is a reasonable choice."},
    {"role": "user", "content": "The transcript lives in Postgres now."},
]
EXPECTED_TURN_IDS = [f"{SESSION_ID}_t{i}" for i in range(len(CONV))]

#: The `point` snapshot keys the capture writer puts on a turn record, as
#: DECLARED. Kept as an explicit expectation rather than derived from the
#: emitted record: a test that reads its expectation off the thing under test
#: agrees with any key set, including a wrong one.
TURN_SNAPSHOT_KEYS = frozenset({
    "id", "content", "pointKind", "speaker", "is_episodic", "status",
    "createdAt", "embedding", "cache_tier",
})


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _journal(tmp_path) -> str:
    return str(tmp_path / "events" / "sdk.jsonl")


def _records(log_path: str) -> list[dict]:
    return [r for r in EventLog(log_path).read_all() if isinstance(r, dict)]


def _turn_ids(sdk) -> list[str]:
    """The turn NODES the graph holds — the population a rebuild can resurrect."""
    return sorted(sdk_mod._capture_turn_ids(sdk._get_proj(), SESSION_ID))


# ════════════════════════════════════════════════════════════════════════════
# R2 — the carrier: a turn is a record row and NO graph node
# ════════════════════════════════════════════════════════════════════════════


def _capture_with_record_armed(sdk, monkeypatch, turns=CONV) -> None:
    """Capture on the record-held lane, with Postgres stubbed out entirely."""
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        lambda *a, **k: None, raising=False)
    res = sdk.capture_session(turns, session_id=SESSION_ID)
    assert res["ok"] is True


@pytest.fixture()
def armed_sdk(tmp_path, monkeypatch):
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=_journal(tmp_path))
    try:
        yield sdk
    finally:
        sdk.close()


def test_the_record_held_lane_journals_the_carrier_not_point_added(
        armed_sdk, monkeypatch):
    """The journal must state the placement the WRITE made (#10067).

    Both halves of one capture disagreed: the graph got no turn node, the
    journal got a node-creating record. The record type is the record of what
    the write did, so it is decided by the same capability.
    """
    _capture_with_record_armed(armed_sdk, monkeypatch)
    records = _records(_journal_of(armed_sdk))
    types = [r["type"] for r in records]
    assert "TurnRecorded" in types, types
    assert types.count("TurnRecorded") == len(CONV), types
    # ⚠️ `PointAdded` is still the right record for the EXTRACTION lane's claim
    # Points (they ARE graph nodes), so the assertion is on the TURNS: no turn
    # id may be journalled as a node-creating record.
    turn_shape = set(EXPECTED_TURN_IDS)
    node_creating_turns = [
        r for r in records
        if r["type"] == "PointAdded"
        and (r.get("point") or {}).get("id") in turn_shape
    ]
    assert not node_creating_turns, (
        "a turn was journalled as `PointAdded` on the record-held lane — the "
        f"#10067 defect: its replay mints turn nodes the graph deliberately "
        f"does not hold — saw {[r['point']['id'] for r in node_creating_turns]}")


def _journal_of(sdk) -> str:
    return sdk._get_event_log().path


def test_the_carrier_carries_the_tier_and_the_writers_own_keys(
        armed_sdk, monkeypatch):
    """R2's second half: the ROW declares which store it is for.

    Two things are pinned here, both because they are silent when wrong: the
    snapshot's key set is EXACTLY the writer's (a `Point`-shaped key the writer
    never emits becomes a phantom node property on replay, and a key it emits
    but the record drops loses data), and `cache_tier` is `record_only`.
    """
    _capture_with_record_armed(armed_sdk, monkeypatch)
    carriers = [r for r in _records(_journal_of(armed_sdk))
                if r["type"] == "TurnRecorded"]
    assert len(carriers) == len(CONV)
    for record in carriers:
        snapshot = record["point"]
        assert frozenset(snapshot) == TURN_SNAPSHOT_KEYS, (
            f"the carrier's snapshot drifted from the writer's payload: "
            f"missing={sorted(TURN_SNAPSHOT_KEYS - set(snapshot))} "
            f"extra={sorted(set(snapshot) - TURN_SNAPSHOT_KEYS)}")
        assert snapshot["cache_tier"] == rp.RECORD_ONLY_TIER
        assert snapshot["is_episodic"] is True


def test_a_rebuild_from_the_carrier_journal_creates_no_turn_nodes(
        armed_sdk, monkeypatch, tmp_path):
    """THE R2 REPLAY PROPERTY — a wipe+replay of the journal yields the graph
    the deployment actually has: Sessions and zero turn nodes."""
    _capture_with_record_armed(armed_sdk, monkeypatch)
    proj = armed_sdk._get_proj()
    assert _turn_ids(armed_sdk) == [], "the armed capture minted a turn node"

    log_path = _journal_of(armed_sdk)
    proj.rebuild(EventLog(log_path), confirm_destructive=True)

    assert _turn_ids(armed_sdk) == [], (
        "the rebuild re-created the turn nodes the capture deliberately kept "
        "out of the graph (#10067) — a rebuild that is a regression")


def test_the_carrier_folds_NOWHERE_in_the_graph():
    """`_NO_PROJECTION_FOLD` is the ONE declaration both engines read
    (`apply()` and `rebuild_all` pass 1b), so one entry covers both — and a
    second, per-engine list is how the two engines come to disagree."""
    assert rp.TURN_RECORD_TYPE in projection_mod._NO_PROJECTION_FOLD


def test_a_torn_tail_carrying_the_carrier_does_not_block_recovery():
    """R2, third home: `log.py`'s torn-tail allow-list.

    Its polarity is deliberate — a NEW type defaults to REFUSED — so omitting
    the carrier would make `rebuild`/`recover_from_log`/`backup.restore` refuse
    an intact journal whose last append was a capture cut off mid-line. The R1
    REMOVAL record must stay OUT of it: a tolerated torn delete is a
    resurrection.
    """
    assert rp.TURN_RECORD_TYPE in log_mod.TORN_TAIL_HARMLESS_EVENT_TYPES


# ════════════════════════════════════════════════════════════════════════════
# R1 — the delete is JOURNALED, so the replay knows the turn was removed
# ════════════════════════════════════════════════════════════════════════════


def _delete_turn_nodes_like_a_purge(sdk) -> list[str]:
    """Delete the session's turn nodes the way the gated purge tool will.

    Scoped, per session, and returning the ids the DELETE ITSELF removed —
    the `#7951` rule that closed the gather-then-delete TOCTOU window. This is
    the ONLY destructive act in this file and it runs on a throwaway store
    under `tmp_path`; no real graph is touched.
    """
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.is_episodic = true AND p.pointKind = 'event' "
        "DETACH DELETE p RETURN p.id",
        params={"sid": SESSION_ID},
    ).result_set
    return sorted(r[0] for r in rows if r and isinstance(r[0], str))


def test_a_delete_with_no_journal_record_RESURRECTS_the_turns(tmp_path):
    """THE DEFECT, reproduced — and the reason R1 exists.

    This leg passes today and must keep passing: it is the measurement that the
    fix's test is not vacuous. A purge that deletes turn nodes without a record
    leaves a journal whose replay re-materialises every one of them, while
    reporting success (`log.py`'s RESURRECTION, #3316).
    """
    log_path = _journal(tmp_path)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        sdk.capture_session(CONV, session_id=SESSION_ID)   # graph-held lane
        removed = _delete_turn_nodes_like_a_purge(sdk)
        assert removed == EXPECTED_TURN_IDS
        assert _turn_ids(sdk) == []

        sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)

        assert _turn_ids(sdk) == EXPECTED_TURN_IDS, (
            "the unjournaled delete did NOT resurrect the turns — then this "
            "file's positive leg would prove nothing")
    finally:
        sdk.close()


def test_a_journaled_removal_survives_the_rebuild(tmp_path):
    """THE R1 REPLAY PROPERTY. Same delete, journaled through the ONE seam.

    Mutation that reddens it: make `TortoiseSDK._journal_turn_removal` emit
    nothing (or call it before the delete and drop the records); the replay then
    re-creates every turn and this assertion fails.
    """
    log_path = _journal(tmp_path)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        sdk.capture_session(CONV, session_id=SESSION_ID)
        removed = _delete_turn_nodes_like_a_purge(sdk)
        outcomes = sdk._journal_turn_removal(removed)

        assert set(outcomes) == set(EXPECTED_TURN_IDS)
        assert all(v is True for v in outcomes.values()), outcomes

        # The record is the ONE `EntityMutated` shape, op=delete, carrying the
        # deleted identity AND the tier the row is left in.
        removal_records = [r for r in _records(log_path)
                           if r["type"] == "EntityMutated"
                           and r.get("op") == "delete"
                           and r.get("id") in EXPECTED_TURN_IDS]
        assert len(removal_records) == len(EXPECTED_TURN_IDS)
        for record in removal_records:
            assert record["label"] == "Point"
            assert record["cache_tier"] == rp.RECORD_ONLY_TIER
            assert "state" not in record, (
                "a delete carries no `state` (#3299) — the tier is the one "
                "declared key, and `state` would be re-applied as node props")

        sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)

        assert _turn_ids(sdk) == [], (
            "the journaled removal did not survive the rebuild — the turns "
            "were resurrected and the purge would be undone by the next replay")
        assert sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.turn_count",
            params={"sid": SESSION_ID}).result_set[0][0] == len(CONV), (
            "the Session container must survive the turn removal")
    finally:
        sdk.close()


def test_the_removal_KEEPS_the_record_row_and_demotes_its_tier():
    """R1's record half, asserted as PLANNED SQL.

    The purge deletes graph turns precisely because the record holds them
    (that is what `turn_parity_census` proves before the delete is allowed), so
    the removal record must not delete the row — and it must not delete the
    `node_identity` parent either, which would cascade the row AND its
    CONTAINS relation away.

    Measured consequence of getting this wrong, which is why it is a test rather
    than a comment: `_emit_event` feeds every appended record straight to
    `record_writer.write_records`, so `delete_point`'s delete shape would
    destroy the copied transcript IMMEDIATELY on the purge run, not only on some
    later replay.
    """
    record = {"type": "EntityMutated", "id": f"{SESSION_ID}_t1",
              "label": "Point", "op": "delete",
              "cache_tier": rp.RECORD_ONLY_TIER}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")

    assert len(statements) == 1, [s.sql for s in statements]
    sql = statements[0].sql
    assert sql.startswith("UPDATE public.point SET cache_tier"), sql
    assert "DELETE" not in sql, sql
    assert rp.RECORD_ONLY_TIER in statements[0].params
    assert not any(s.sql.startswith("DELETE FROM public.node_identity")
                   for s in statements), (
        "an identity-parent delete cascades the turn row and its CONTAINS "
        "relation away — the transcript the purge must preserve")

    # And the opposite direction, so the discriminator cannot be a silent
    # no-op: WITHOUT the tier the very same record is a real hard delete.
    plain = rp.mutations_for({k: v for k, v in record.items()
                              if k != "cache_tier"},
                             org_id="o", graph_id="g")
    assert [s.sql.split()[0] for s in plain] == ["DELETE", "DELETE"], (
        "a tier-less `EntityMutated` op=delete must keep its #3299 meaning")


def test_a_tier_scoped_delete_of_a_non_point_is_REFUSED():
    """The tier column exists only on `public.point`; emitting the demote for
    another label would be SQL against a column that is not there."""
    with pytest.raises(rp.RefusedJournalOperation, match="cache_tier"):
        rp.mutations_for({"type": "EntityMutated", "id": "o1", "label": "Object",
                          "op": "delete", "cache_tier": rp.RECORD_ONLY_TIER},
                         org_id="o", graph_id="g")


def test_a_single_id_string_is_refused_not_iterated(tmp_path):
    """A bare string would be journalled one CHARACTER at a time — every one of
    those records matching nothing, and the removal silently unrecorded."""
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=_journal(tmp_path))
    try:
        with pytest.raises(ValueError, match="iterable"):
            sdk._journal_turn_removal(f"{SESSION_ID}_t0")
    finally:
        sdk.close()


def test_the_tier_domain_matches_the_columns_own_CHECK():
    """Two declarations of one domain (`record_projection`'s constant and the
    migration's CHECK); a change landing on one side only is a silent drift the
    database would refuse at write time — or, worse, accept."""
    from pathlib import Path

    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(
        (Path(rp.__file__).resolve().parent.parent / "supabase" / "migrations")
        .glob("*point_cache_tier*.sql")))
    assert sql, "the cache_tier migration is missing"
    assert f"'{rp.GRAPH_TIER}'" in sql and f"'{rp.RECORD_ONLY_TIER}'" in sql
