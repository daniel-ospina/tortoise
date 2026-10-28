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
    # turn RECORDS are what reach Postgres, and they are gated on the event log
    # (`sdk._get_event_log()`). Since #10017's step 7 the armed lane journals ONE
    # `TurnsRecorded` carrier holding those snapshots rather than one `PointAdded`
    # per turn; the store they reach is the same one either way. Both halves of
    # the capability test are therefore supplied here.
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

    The transcript lives in the record (the `TurnsRecorded` carrier, or a
    per-turn `PointAdded` before #10017's step 7), so the
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


class _RecordingSeam:
    """A `write_live_record` stand-in that REMEMBERS what the capture handed it.

    Only the door's arguments are recorded; nothing is written anywhere, so the
    leg stays DB-free. It exists so the assertion below cannot pass vacuously:
    the same capture must have put the text SOMEWHERE, and this is the somewhere.
    """

    def __init__(self) -> None:
        self.records: list[dict] = []

    def __call__(self, record, **_kwargs):
        self.records.append(record)
        return None

    def point_contents(self) -> list[str]:
        """The turn text the capture handed the record, in EITHER carrier shape.

        #10017 step 7 moved the record-armed lane from one `PointAdded` per turn
        to ONE `TurnsRecorded` carrier holding them (`turns`), so a seam that
        only read `point` would report "no turn reached the record" on the very
        lane this file is about — and the non-vacuity assertion below would then
        fail for a capture that stored the transcript correctly.
        """
        out: list[str] = []
        for record in self.records:
            if record.get("type") == "PointAdded":
                out.append(record["point"]["content"])
            elif record.get("type") == "TurnsRecorded":
                out.extend(turn["content"] for turn in record.get("turns") or ())
        return out


def _graph_text_hits(proj, needle: str) -> list[tuple[str, str]]:
    """Every node whose stored text contains `needle`, with its label and id.

    The POPULATION is the point (acceptance bullet 5 — *"the property, not the
    call site"*): the scan is over every node, in every property the writers use
    to carry text (`c` on a Point, `content`, `text`, and the JSON carriers
    `payload`/`props` a `:GraphEvent` holds). It therefore does not depend on the
    turn id derivation, the `:Point` label, `is_episodic`, or the `CONTAINS`
    wiring — so a second write path that invents its own shape is inside the
    population. `sdk._capture_turn_ids` (the sibling assertion) is not: it asks
    one id space on one edge.
    """
    return [tuple(row) for row in proj.g.query(
        "MATCH (n) WHERE n.c CONTAINS $needle OR n.content CONTAINS $needle "
        "OR n.text CONTAINS $needle OR n.payload CONTAINS $needle "
        "OR n.props CONTAINS $needle "
        "RETURN labels(n)[0], coalesce(n.id, '')",
        params={"needle": needle}).result_set]


def test_an_armed_capture_puts_the_turn_text_in_no_node_at_all(sdk, monkeypatch):
    """#10000 acceptance bullet 5 — the property, not the call site.

    The record is armed, so the graph must hold no node carrying a turn's text —
    by ANY route: a new label, a different wiring, a `pointKind` of its own. The
    sibling pin asserts the id space this writer uses is empty, which a second
    writer with its own shape would not redden; this one scans the graph.

    Non-vacuity is the record half: the capture must hand the text to the record
    seam, or "nothing in the graph" would be satisfied by a capture that stored
    the transcript nowhere at all (the vacuity #10011's pin had and #10021 fixes
    on its own axis — recorded there, not duplicated here).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    needle = "the transcript line that must never reach the graph"
    _capture(sdk, "property-session", [{"role": "user", "content": needle},
                                       {"role": "assistant", "content": "ok"}])
    assert seam.point_contents() == [f"[user] {needle}", "[assistant] ok"], (
        "the armed capture handed no turn to the record — the graph is not "
        "carrying it either, so this assertion would prove nothing")
    proj = sdk._get_proj()
    assert _graph_text_hits(proj, needle) == [], (
        "a turn's text is in the graph while the record was armed")
