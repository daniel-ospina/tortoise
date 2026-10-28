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


def arm_record(monkeypatch, *, org: str = "org-10000",
               graph: str = "graph-10000") -> None:
    """Arm the record the way a real single-tenant deployment does.

    THREE facts, not one (#10067): the DSN, the declaration that this process
    serves one graph, and that graph's id pair. `TORTOISE_RECORD_DSN` alone is
    NOT the capability — `write_live_record` refuses an armed write it cannot
    attribute (`no_tenant`, #9188 F4) — so a test that set the DSN alone was
    asserting a path on which the turn reached NEITHER store.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setenv(record_writer.RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(record_writer.RECORD_ORG_ENV, org)
    monkeypatch.setenv(record_writer.RECORD_GRAPH_ENV, graph)


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

    The transcript lives in the record (the `TurnsRecorded` carrier, #10067,
    which projects the turn rows at `cache_tier = 'record_only'`), so the thing
    that must be true of the GRAPH is the absence of turn nodes — and the
    Session's counters must still be written, or the quota/metering reads that
    depend on `turn_count`/`capture_redactions` would silently see nothing.
    """
    arm_record(monkeypatch)
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
        """Every turn TEXT the capture handed the record, in either shape.

        Both are real: `PointAdded` carries one snapshot under `point` and is the
        record's carrier where the GRAPH holds the turn; `TurnsRecorded` (#10067)
        carries the whole capture's snapshots under `turns` and is its carrier on
        the record-only lane. A helper that knew only one shape would read the
        other as "the capture handed the record nothing" — the vacuity this class
        exists to prevent.
        """
        contents: list[str] = []
        for record in self.records:
            if record.get("type") == "PointAdded" and isinstance(
                    record.get("point"), dict):
                contents.append(record["point"]["content"])
            elif record.get("type") == "TurnsRecorded":
                contents.extend(snap["content"]
                                for snap in record.get("turns") or [])
        return contents


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
    arm_record(monkeypatch)
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


def test_an_armed_record_that_cannot_name_a_tenant_keeps_the_turns_in_the_graph(
        sdk, monkeypatch):
    """#10067 — the third conjunct of the capability, and the one that lost data.

    `TORTOISE_RECORD_DSN` + a journal is NOT "the record holds the turns": the
    seam refuses an armed write it cannot attribute (`no_tenant`, #9188 F4), and
    on a multi-tenant process with no request scope that is every write. Before
    this pin the capture read the capability as true, skipped the graph write,
    and journalled per-turn `PointAdded` records the record never materialised —
    the transcript in NEITHER store, the capture reporting success.

    The assertion is on the STORE the read path serves, not on a flag: the turn
    nodes are in the graph and the record seam was handed no turn record.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    # Deliberately NOT declared single-tenant, and no request scope: the process
    # cannot say whose rows these would be, which is the production shape
    # `record_tenant()` refuses (`.` -> `None`).
    monkeypatch.delenv(record_writer.RECORD_SINGLE_TENANT_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    proj = sdk._get_proj()
    _capture(sdk, "no-tenant-session", [{"role": "user", "content": "a"},
                                        {"role": "assistant", "content": "b"}])
    assert sorted(sdk_mod._capture_turn_ids(proj, "no-tenant-session")) == [
        "no-tenant-session_t0", "no-tenant-session_t1"], (
        "the record could not be told whose turn these are, so the graph is the "
        "only store that can hold them — and it did not get them either")
    assert [r for r in seam.records
            if r.get("type") == "TurnsRecorded"] == [], (
        "a record-only carrier was journalled for a record write that cannot "
        "land — its rows would never exist, so the journal would name a home "
        "that is not there")
    assert seam.point_contents() == ["[user] a", "[assistant] b"], (
        "the fallback must journal the NODE-CREATING record, because the graph "
        "is the store that holds these turns")


def test_a_tenant_CARRIED_on_the_sdk_arms_the_record_only_lane(sdk, monkeypatch):
    """#10067 review P1 — the HOSTED REST shape: by-value tenant, no scope.

    The capability read used to ask `record_writer.record_tenant()`, which sees
    the ambient scope and the declared single-tenant env pair and NOTHING else —
    while the seam applies the CARRIED tenant first (it is handed
    `tenant=self._record_tenant`). The hosted REST lane carries by value and
    enters no ambient scope by design (`_bind_record_tenant`, `_data_sdk`), so on
    that lane the read answered "no tenant" and the writer kept the turns in the
    GRAPH for a record that was accepting every row: the record-only tier
    silently reverted on the one multi-tenant capture lane, re-creating exactly
    what #10067 exists to end.

    This fixture is the production shape — DSN set, journal set, NO scope and NO
    declared pair — and the only arming is `_bind_record_tenant`. Every assertion
    is on a STORE, so the test cannot be satisfied by a flag: the graph holds no
    turn node, the journal carries the carrier (and no per-turn `PointAdded`),
    and the seam was really handed the transcript.
    """
    from tortoise import record_writer

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.delenv(record_writer.RECORD_SINGLE_TENANT_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    # Non-vacuity: the AMBIENT question really is unanswered here, so the only
    # thing that can arm the record-only lane is the carrier on the SDK.
    assert record_writer.record_tenant() is None

    seam = _RecordingSeam()
    monkeypatch.setattr("tortoise.record_writer.write_live_record", seam)
    sdk._bind_record_tenant("org-10067", "graph-10067")
    proj = sdk._get_proj()
    _capture(sdk, "carried-tenant-session", [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"}])

    assert sdk_mod._capture_turn_ids(proj, "carried-tenant-session") == [], (
        "the record CAN attribute this write (the tenant is carried on the SDK), "
        "so the graph must keep no turn node — otherwise the record-only tier "
        "does not reach the hosted REST lane at all")
    assert [r for r in seam.records if r.get("type") == "TurnsRecorded"], (
        "the capture journalled no record carrier, so the record holds no turn "
        "rows and the transcript lives nowhere")
    assert not [r for r in seam.records if r.get("type") == "PointAdded"], (
        "a NODE-CREATING per-turn record was journalled on the record-only lane "
        "— the #10067 defect")
    assert seam.point_contents() == ["[user] a", "[assistant] b"]
