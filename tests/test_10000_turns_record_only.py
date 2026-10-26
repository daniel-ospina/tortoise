"""#10000 — the turns are RECORD rows, and the graph is only the fallback.

The capability rule this file pins: the capture writer puts turns in the graph
ONLY when the record cannot hold them (no journal configured, or the record not
armed). With both available the graph keeps the Session's redaction count and
nothing else — no turn node, no `CONTAINS` edge — and the transcript still comes
back, through the record.
"""

from __future__ import annotations

import pytest

import tortoise.sdk as sdk_mod


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    # The record can only hold the turns when a JOURNAL is configured too: the
    # per-turn `PointAdded` records are what reach Postgres, and they are gated on
    # the event log (`sdk._get_event_log()`). Both halves of the capability test
    # are therefore supplied here.
    return TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))


def _capture(sdk, sid: str, turns: list[dict], **kw):
    proj = sdk._get_proj()
    return sdk_mod._write_session_and_turns(
        proj, sdk, sid, turns, now="2026-10-25T00:00:00+00:00", **kw)


def test_unarmed_record_keeps_turns_in_the_graph(sdk, monkeypatch):
    """The transitional guarantee: with no record DSN the behaviour is unchanged.

    This is the assertion that makes the change safe to land before #9548 arms
    the record — an unarmed deployment must be byte-identical to the pre-#10000
    one, turn nodes included.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    _capture(sdk, "fallback-session", [{"role": "user", "content": "a"},
                                       {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "fallback-session")) == [
        "fallback-session_t0", "fallback-session_t1"]


def test_armed_record_writes_no_turn_nodes_but_keeps_the_session(sdk, monkeypatch):
    """Record armed -> the graph holds the Session only.

    The transcript lives in the record (the per-turn `PointAdded` records), so the
    thing that must be true of the GRAPH is the absence of turn nodes — and the
    Session's counters must still be written, or the quota/metering reads that
    depend on `turn_count`/`capture_redactions` would silently see nothing.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr(
        "tortoise.record_writer.write_live_record",
        lambda *a, **k: None, raising=False)
    proj = sdk._get_proj()
    _capture(sdk, "record-session", [{"role": "user", "content": "a"},
                                     {"role": "assistant", "content": "b"}])
    assert sdk_mod._capture_turn_ids(proj, "record-session") == [], (
        "a turn node reached the graph while the record was armed")
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count, s.turn_max_index",
        params={"sid": "record-session"}).result_set
    assert rows and rows[0][0] == 2, "the Session's turn_count was not written"
    assert rows[0][1] == 1, "turn_max_index is not the id-space high-water mark"


def test_shorter_recapture_sweeps_by_id_space_not_by_count(sdk, monkeypatch):
    """The #1920 behaviour that made a naive `turn_count` sweep wrong.

    At `turn_offset=2` the Session stores `turn_count=2` while the ids are
    `_t2`/`_t3`. A sweep bounded by the COUNT deletes nothing; the sweep must use
    the id space, which is what `turn_max_index` carries.
    """
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    proj = sdk._get_proj()
    turns = [{"role": "user", "content": "t0"}, {"role": "assistant", "content": "t1"},
             {"role": "user", "content": "t2"}, {"role": "assistant", "content": "t3"}]
    _capture(sdk, "sweep-session", turns[0:2])
    _capture(sdk, "sweep-session", turns[2:4], turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "sweep-session")) == [
        "sweep-session_t0", "sweep-session_t1",
        "sweep-session_t2", "sweep-session_t3"]
    _capture(sdk, "sweep-session", turns[2:3], turn_offset=2, session_existed=True)
    assert sorted(sdk_mod._capture_turn_ids(proj, "sweep-session")) == [
        "sweep-session_t0", "sweep-session_t1", "sweep-session_t2"], (
        "the surplus turn beyond the new window was not swept")
