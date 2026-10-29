"""#10017 R1 — a REBUILD must not resurrect the turns a purge removed.

The recorded ruling (owner, #10017, 2026-10-26T15:48:12Z): *"the legacy purge
journals the deletion. Deleting a turn from the graph emits `EntityMutated`
op=delete."* §9.1 places raw turns outside the graph, so #10017's purge removes
legacy turn nodes the record now holds — and a graph delete that is not
journaled is a RESURRECTION: the journal still carries the `PointAdded` that
created each turn, so a later `rebuild()` replays a history that never contained
the removal and re-materialises every turn the delete phase removed, while
reporting success (`tortoise/log.py`'s class, #3316).

What the removal record is, in two halves, because one op must not carry two
end-states (#3300):

* **absent `cache_tier`** — exactly the #3299 hard delete (`delete_point`, the
  stale-turn sweep, the session delete). Unchanged, and pinned here.
* **`cache_tier = 'record_only'`** — the GRAPH-tier removal: the graph fold
  hard-deletes the node and never reads the key, while the RECORD fold DEMOTES
  the row's tier instead of deleting it. The record is the turn's home and the
  transcript is the thing the purge exists to preserve; `delete_point`'s
  `DELETE FROM public.point` would destroy it on the purge run itself, because
  `_emit_event` feeds every appended record straight to
  `record_writer.write_records`.

⛔ The tests are ordered to make the failure visible, not just the fix: the first
leg reproduces the DEFECT (a delete with no journal record, a rebuild that puts
the turns back). A replay test that cannot show the resurrection when the fix is
removed proves nothing — the mutation named in the commit message
(``_journal_turn_removal`` made a no-op) reddens
``test_a_journaled_removal_survives_the_rebuild``.

This file is the RECORDABLE half only: `_journal_turn_removal` emits records and
deletes nothing. The destructive half — the census gate, the `DETACH DELETE`,
the preview-then-token confirmation — is a separate, separately-gated tool.

Offline only: ``TORTOISE_SESSION_LLM_MOCK=1`` installs the deterministic
MockModel extractor (zero network, zero provider spend). The record's own
statements are asserted as PLANNED SQL (``record_projection``), which is where
the "keep the row" decision lives; nothing here talks to Postgres.
"""

from __future__ import annotations

import pytest

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
