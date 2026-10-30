"""#10017 — the raw-turn CARRIER: declared, registered, and it materialises NO node.

THE PROPERTY THIS FILE EXISTS FOR, in one sentence: **a record-sourced rebuild
and a journal replay agree that a capture's turns are not graph state.** The two
routes reached that graph by different roads before this — the journal replay
because the turn fold is being retired, the record route because
`cache_projection.read_record_view` would happily node-ify a turn row it reads
from `public.point` — and two rebuild routes producing two different graphs is a
worse ambiguity than the duplication the ruling removes.

WHAT IS PINNED HERE, AND WHY EACH PIN CAN FAIL
==============================================
§1 the CARRIER is DECLARED in every registry a journal type is declared in, and
   is deliberately ABSENT from `log.TORN_TAIL_HARMLESS_EVENT_TYPES` — including
   the `EntityMutated` hard delete the stale sweep uses, which must stay refused
   for the same reason (`#10017` step 5).
§2 the PAYLOAD is the WRITER'S OWN: the per-turn snapshot `sdk._write_capture_turns`
   builds today, read off a REAL capture, with `contains_session` on the event
   envelope (#3947) and nothing added. Step 7 is then a move, not a reshape.
§3 NEITHER replay engine creates a turn node from a record-carried journal —
   measured on `recover_from_log` (the one-record `apply()` engine) AND
   `rebuild_all` (pass-1b), and non-vacuously: the Session node is there and the
   journal really carries the transcript.
§4 the RECORD route agrees: the carrier's turns are `public.point` rows at
   `cache_tier = 'record_only'`, `read_record_view` plans no node for them, and a
   graph-tier control row in the same read DOES become one (so the assertion is
   about the tier, not about an empty view).
§5 the STALE SWEEP: a shorter re-capture removes a turn on BOTH routes — the
   rebuild never had it as a node, and the record's hard-delete record removes
   its row — so neither route resurrects it.
§6 the ROW WRITE is the carrier's own (`_turns_statements`): one `public.point`
   row per turn at `cache_tier = 'record_only'`, taken from the TYPE's
   declaration rather than from a payload, with no relation row and no empty
   carrier.
§7 the PRODUCER (#10067): a record-armed capture journals ONE carrier and no
   per-turn `PointAdded`, its plan writes the turn rows, and a replay of that
   very journal materialises no turn node.

⛔ STEP 7 HAS LANDED (#10067): `sdk._write_capture_turns` emits
`TURNS_RECORD_TYPE` on the record-only lane, so §1–§6 pin the contract the
producer now meets and §7 pins the producer itself. §3/§5 still build their
journals by moving a REAL capture's records onto the carrier
(`_record_carried`), which keeps them about the REPLAY ENGINES; §7 covers the
writer, from its own output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_10017_cache_tier import _read_view  # the tier suite's reader control

from tortoise import log as tlog
from tortoise import record_projection as rp
from tortoise.projection import _NO_POINT_FOLD, _NO_PROJECTION_FOLD

SESSION = "sess-10017-carrier"
TURNS = [{"role": "user", "content": "the first turn"},
         {"role": "assistant", "content": "the second turn"}]
NOW = "2026-10-25T00:00:00+00:00"


# ── fixtures: the REAL capture writer, the record UNARMED ───────────────────


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    """A capture with a journal wired and the record NOT armed.

    Unarmed is the state step 7 changes, and it is also what makes the JSONL the
    only turn carrier in this fixture — the same setup
    `tests/test_10000_turns_record_only.py` uses for its graph half.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    from tortoise.sdk import TortoiseSDK

    path = tmp_path / "capture" / "events.jsonl"
    path.parent.mkdir(parents=True)
    sdk = TortoiseSDK(db_path=str(tmp_path / "capture.db"),
                      event_log_path=str(path))
    yield sdk, path
    sdk.close()


def _capture(sdk, turns, **kw):
    import tortoise.sdk as sdk_mod

    return sdk_mod._write_session_and_turns(
        sdk._get_proj(), sdk, SESSION, turns, now=NOW, **kw)


def _journal(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _is_turn(record: dict) -> bool:
    """A record about ONE per-turn Point — the population the carrier replaces."""
    if record.get("type") == "PointAdded":
        point_id = (record.get("point") or {}).get("id")
        return str(point_id or "").startswith(f"{SESSION}_t")
    if record.get("type") == "PointRetracted":
        return str(record.get("id") or "").startswith(f"{SESSION}_t")
    return False


def _carrier(snaps, index: int = 0) -> dict:
    """The record step 7 will emit, built from the writer's own snapshots.

    `contains_session` rides the ENVELOPE (top level of the record), exactly as
    the capture writer puts it on `PointAdded` — it is the structural fact the
    projection's edge fold restores, never a node property (#3947).

    ``event_id`` is PER RECORD (a journal's record identity is one per line): a
    shared id would model a shape no producer emits and would let a dropped
    second carrier hide behind the first.
    """
    return {
        "event_id": f"01J10017CARRIER{index}",
        "ts": NOW,
        "type": rp.TURNS_RECORD_TYPE,
        "sessionId": SESSION,
        "contains_session": SESSION,
        "turns": [dict(snap) for snap in snaps],
    }


def _record_carried(groups: list[list[dict]]) -> list[dict]:
    """The journal step 7 will produce for these captures, record-carried.

    Each capture's per-turn `PointAdded` records become ONE `TurnsRecorded`
    carrier holding those very snapshots — placed where the first of them was,
    so journal ORDER is unchanged. The stale sweep's hard-delete `EntityMutated`
    survives, because that is what removes the record ROW. A `PointRetracted`
    for a turn id is dropped if one is ever present — the producer's sweep call
    is payload-only, so it journals NOTHING — and the filter is here so a re-cut
    journal can never model a record the writer does not emit.
    """
    turn_ids = {r["point"]["id"] for g in groups for r in g
                if r.get("type") == "PointAdded" and _is_turn(r)}
    out: list[dict] = []
    for index, group in enumerate(groups):
        snaps = [r["point"] for r in group
                 if r.get("type") == "PointAdded" and _is_turn(r)]
        first = next((i for i, r in enumerate(group)
                      if r.get("type") == "PointAdded" and _is_turn(r)), None)
        for i, record in enumerate(group):
            if record.get("type") == "PointAdded" and _is_turn(record):
                if i == first:
                    out.append(_carrier(snaps, index))
                continue
            if record.get("type") == "PointRetracted" and record.get("id") in turn_ids:
                continue
            out.append(record)
    return out


def _write_journal(directory: Path, records: list[dict]) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "events.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(directory)


def _fresh_projection(tmp_path, monkeypatch):
    """An embedded projection whose graph is provably EMPTY.

    `recover_from_log` only rebuilds a lost graph (0 nodes), so the precondition
    is asserted rather than assumed: a graph that already held nodes would make
    the replay a no-op and every "no turn node" assertion below vacuously true.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path / "replay-base"))
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    from tortoise.sdk import TortoiseSDK

    client = TortoiseSDK(db_path=str(tmp_path / "replay.db"))
    proj = client._get_proj()
    proj.g.query("MATCH (n) DETACH DELETE n")
    assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    return client, proj


def _node_ids(proj) -> set[str]:
    return {row[0] for row in proj.g.query(
        "MATCH (n) RETURN coalesce(n.id, '')").result_set}


_TEXT_SCAN = (
    "MATCH (n) WHERE n.c CONTAINS $needle OR n.content CONTAINS $needle "
    "OR n.text CONTAINS $needle OR n.payload CONTAINS $needle "
    "OR n.props CONTAINS $needle RETURN labels(n)[0], coalesce(n.id, '')")


def _text_hits(proj, needle: str) -> list[tuple[str, str]]:
    """Every node carrying *needle*, by ANY property that holds text.

    The population, not the id space: a second writer with its own label or
    wiring would satisfy an id-shaped assertion and fail this one.
    """
    return [tuple(row) for row in
            proj.g.query(_TEXT_SCAN, params={"needle": needle}).result_set]


# ── §1 the declaration, in every registry that declares a journal type ─────


def test_the_carrier_is_declared_wherever_a_journal_type_is():
    """`SessionNarrated`'s seams, one for one — a type missing from any of them
    fails LOUDLY in its own place, which is why all of them are asserted here."""
    assert rp.TURNS_RECORD_TYPE == "TurnsRecorded", (
        "the ruling on #10017 names the type `TurnsRecorded`; the producer will "
        "import this constant, so it must not drift")
    # The record's total-clause registry (and its import-time equality with
    # RECORD_MUTATIONS).
    assert rp.TURNS_RECORD_TYPE in rp.JOURNAL_EVENT_TYPES
    assert isinstance(rp.RECORD_MUTATIONS[rp.TURNS_RECORD_TYPE].effect,
                      rp.NoRecordEffect)
    # The GRAPH fold: `_NO_PROJECTION_FOLD` is read by BOTH replay dispatchers
    # (`apply()` and `rebuild_all`'s pass-1b) and by `_NO_POINT_FOLD`, which
    # unions it — so one membership registers all three.
    assert rp.TURNS_RECORD_TYPE in _NO_PROJECTION_FOLD
    assert rp.TURNS_RECORD_TYPE in _NO_POINT_FOLD


def test_the_carrier_declares_no_NODE_statement_its_rows_are_its_own():
    """`NoRecordEffect` is a decision with a reason — and since step 7 it is
    about the NODE, not about the rows.

    Named for what it asserts: the carrier DECLARES no effect, yet it does write
    rows — through `_turns_statements`, its own column statement. The declared
    effect stays a deliberate no-op because §9.1's record home for
    the turns is `public.point`, reached by the carrier's OWN column statement
    (`record_projection._turns_statements`, #10067) and never by a
    one-node-snapshot-per-record path: the carrier holds a LIST of turns, so a
    declared `NodeSnapshot` could only ever project ONE row of it. What must
    remain true is the node half — the declared mutation materialises nothing.
    """
    carrier = _carrier([{"id": f"{SESSION}_t0", "content": "c",
                         "pointKind": "event", "speaker": "user",
                         "is_episodic": True, "status": "draft",
                         "createdAt": NOW, "embedding": None}])
    assert rp._declared_mutations(carrier, org_id="o", graph_id="g") == [], (
        "the carrier's DECLARED effect must stay empty — the node half — because "
        "its rows come from `_turns_statements`, not from a node snapshot")
    # The node half is the EMPTY declaration above; the ROW half is the
    # carrier's own statement set, and asserting it is PRESENT is what stops
    # "declares nothing" from reading as "writes nothing at all". Both sides are
    # `mutations_for` — the one function the live door (`write_live_record` →
    # `replay`) and `replay_plan` both call.
    # ⛔ NOT `assert replay_plan([carrier], …)`: `replay_plan` APPENDS the
    # journal-position row for every record while `position=True` (the default,
    # and this test passes no `position`), so the list is non-empty for ANY
    # registered type — an assertion worded "its rows are missing" that cannot
    # fail for that reason. A `node_identity`-absence assertion is not the
    # substitute either: the turn rows legitimately need the identity parent
    # (`_turns_statements` emits one before each upsert).
    rows = _point_row_statements(carrier)
    assert rows, (
        "the carrier wrote no turn rows at all — the empty declaration above "
        "would then be satisfied by a type that writes nothing")


# ── §6 step 7 (#10067): the carrier WRITES the rows — the tier is its own ──


def _turn_snapshot(turn_id: str, text: str = "c", **extra) -> dict:
    return {"id": turn_id, "content": text, "pointKind": "event",
            "speaker": "user", "is_episodic": True, "status": "draft",
            "createdAt": NOW, "embedding": None, **extra}


def _point_row_statements(carrier: dict) -> list:
    return [st for st in rp.mutations_for(carrier, org_id="o", graph_id="g")
            if st.sql.startswith("INSERT INTO public.point ")]


def test_the_carrier_writes_one_point_row_per_turn_at_the_record_only_tier():
    """§6 — the row WRITE that `_record_rows` above could only MODEL.

    The carrier replaced one `PointAdded` PER TURN, and those records were also
    the record's turn-row write — so a switchover that swapped the emission
    without teaching the carrier to write its rows would have moved the
    transcript out of the graph and into NO store (measured: the record then
    projects zero turn rows). This is the pin for that half, and the tier is
    what keeps `rebuild_from_record` from re-creating the nodes the journal
    route correctly omits.
    """
    turns = [_turn_snapshot(f"{SESSION}_t0", "[user] one"),
             _turn_snapshot(f"{SESSION}_t1", "[assistant] two")]
    rows = _point_row_statements(_carrier(turns))
    assert len(rows) == len(turns), (
        f"the carrier planned {len(rows)} row(s) for {len(turns)} turn(s)")
    assert [r.params[3] for r in rows] == [t["id"] for t in turns], (
        "each turn row must key on its OWN snapshot's id — the envelope id is "
        "the SESSION, so reading it files a whole capture under one key")
    for row, turn in zip(rows, turns, strict=True):
        columns = row.sql.split("(", 1)[1].split(")", 1)[0].replace(" ", "")
        assert "cache_tier" in columns.split(","), (
            "the tier must be a COLUMN write: `_RECORD_ONLY_KEYS` strips a "
            "payload `cache_tier`, so a payload path lands the DEFAULT 'graph'")
        assert "record_only" in row.params, row.params
        assert turn["content"] in row.params, (
            "the row does not carry the turn's text — the record would hold a "
            "key and no transcript")


def test_the_tier_is_the_types_declaration_and_never_a_payload_value():
    """§6 — placement authority stays on the record TYPE.

    A snapshot that names its own tier must not be believed: `graph` is the
    schema DEFAULT, so a payload could silently put a turn back in the cache —
    and `record_projection._RECORD_ONLY_KEYS` strips the key before the insert,
    which is why the strip alone is not enough and the column is written from
    the declaration instead.
    """
    spoofed = _turn_snapshot(f"{SESSION}_t0", cache_tier="graph")
    row, = _point_row_statements(_carrier([spoofed]))
    assert row.params.count("record_only") == 1, row.params
    assert "graph" not in row.params, (
        f"a payload-supplied tier reached the row: {row.params}")


def test_the_carrier_writes_no_relation_row_for_a_turn():
    """§6 — a turn is not a graph node, so it is not an EDGE endpoint either.

    The per-turn `PointAdded` records carried `contains_session` on the envelope
    and `_ENVELOPE_RELATIONS` turned that into a `session→turn CONTAINS` row.
    Keeping it on the carrier would make the record richer than the graph — the
    one direction Interface A forbids — and nothing reads it: `record_read`
    serves a session's turns by `node_key` range, never through `public.relation`.
    """
    carrier = _carrier([_turn_snapshot(f"{SESSION}_t0")])
    assert carrier["contains_session"] == SESSION
    statements = rp.mutations_for(carrier, org_id="o", graph_id="g")
    # ⛔ GUARD ASSERTION before the absence: pre-#10067 this same carrier
    # projected `[]` here (it declares `NoRecordEffect`, and
    # `_narrative_statements` declines a shape with no narrative field), so
    # `any(… for st in [])` was False for the code the row write replaced — the
    # assertion held while nothing was written at all.
    rows = _point_row_statements(carrier)
    assert rows, (
        "the carrier produced no turn rows — the absence below would then be "
        "vacuous, as it was while the row write did not exist")
    assert not any("public.relation" in st.sql for st in statements), (
        "the carrier planned a relation row for a turn that is not a node")


def test_a_carrier_with_no_turn_set_is_REFUSED_not_a_silent_no_op():
    """§6 — the carrier IS the transcript, so an empty one records nothing.

    The producer's own gate returns before it emits (`if not turn_texts`), so
    this shape has no producer — and a silent `[]` here would make the NEXT
    producer's broken carrier, the one that drops a capture's whole turn set,
    indistinguishable from a healthy one.
    """
    for empty in ({}, {"turns": []}, {"turns": "not-a-list"}):
        carrier = {"event_id": "e", "ts": NOW,
                   "type": rp.TURNS_RECORD_TYPE, "sessionId": SESSION, **empty}
        with pytest.raises(rp.RecordProjectionError):
            rp.mutations_for(carrier, org_id="o", graph_id="g")


def _graph_event_payloads(proj, type_: str) -> list[dict]:
    """Every `:GraphEvent` row of *type_* in this graph, decoded.

    The store `poll_events` serves from, and the ONLY place the sweep's
    `PointRetracted` (payload-only ⇒ never journalled) can be observed at all.
    """
    return [json.loads(row[0]) for row in proj.g.query(
        "MATCH (e:GraphEvent {type:$t}) RETURN e.payload",
        params={"t": type_}).result_set]


def test_the_stale_sweep_keeps_the_feed_PAIR_and_records_the_removal_once(
        tmp_path, monkeypatch):
    """§6 — a shorter re-capture removes the ROW, and the FEED stays PAIRED.

    Two stores, one meaning each (#3300), exactly as `delete_point` does it:

    * the `:GraphEvent` / `poll_events` stream is a THIRD store, and it is
      unchanged: this lane still emits a payload-only `PointAdded` per turn, so
      a subscriber sees the ADDITIONS — and the sweep's payload-only
      `PointRetracted` is what tells that same subscriber the turn is gone. ⛔
      This is the half a guard once removed, and its ONLY measured effect was to
      drop this row: a consumer then held a turn that appears and never leaves
      (the #1920 orphan class, moved onto the change feed), while
      `docs/event-catalog.md` still named the sweep as a hard-delete
      `PointRetracted` producer.
    * the JSONL journal gets the `EntityMutated` op=delete — the RECORD's hard
      delete of the `public.point` ROW — and NO `PointRetracted`: that call
      passes a payload only (`point=None and id=None`), so `_emit_event`
      returns before the append and journals nothing. One removal, one record,
      one subscriber row — no #3300 double-delete is possible here.

    Non-vacuity (this is why the test was rewritten): every feed assertion below
    reads the STORE the change lives in. The earlier version asserted only on
    `_journal(path)`, which could not observe the change at all — the call never
    wrote a JSONL line — so it passed unchanged on the code it was meant to
    redden.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", "org-10067")
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", "graph-10067")
    from tortoise.sdk import TortoiseSDK

    path = tmp_path / "cap" / "events.jsonl"
    path.parent.mkdir(parents=True)
    sdk = TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                      event_log_path=str(path))
    try:
        _capture(sdk, TURNS)
        _capture(sdk, TURNS[0:1])          # shorter: `_t1` is now surplus
        proj = sdk._get_proj()
        turn_node_ids = [i for i in _node_ids(proj)
                         if i.startswith(f"{SESSION}_t")]
        added = [(p or {}).get("id")
                 for p in _graph_event_payloads(proj, "PointAdded")]
        retracted = [(p or {}).get("id")
                     for p in _graph_event_payloads(proj, "PointRetracted")]
    finally:
        sdk.close()

    # The FEED: the additions are still announced (unchanged by #10067)...
    assert {f"{SESSION}_t0", f"{SESSION}_t1"} <= set(added), (
        f"the record-only lane stopped announcing its turns to poll_events "
        f"(added={added})")
    # ...and the sweep is announced on the SAME surface, exactly once.
    assert retracted == [f"{SESSION}_t1"], (
        "the stale-turn sweep must leave ONE `PointRetracted` subscriber row "
        f"for the swept turn — a feed that shows an addition and never its "
        f"removal is one-sided (got {retracted})")

    # The JOURNAL: the removal is the hard-delete RECORD, and nothing else.
    journal = _journal(path)
    swept = [r for r in journal
             if r.get("type") == "EntityMutated" and r.get("op") == "delete"
             and r.get("id") == f"{SESSION}_t1"]
    assert swept, "the swept turn's row removal was not journalled"
    assert not [r for r in journal
                if r.get("type") == "PointRetracted"
                and str(r.get("id") or "").startswith(f"{SESSION}_t")], (
        "a node-lifecycle retraction reached the JSONL for a turn that was "
        "never a graph node — its fold tombstones, the wrong meaning here")
    # Non-vacuity: the graph really does hold no turn node on this lane, so the
    # feed's `PointRetracted` retracts no node and the ROW is the only state.
    assert not turn_node_ids, turn_node_ids


def test_a_torn_carrier_line_is_REFUSED_and_so_is_the_stale_sweep_delete():
    """The polarity, and the symmetry that would break it.

    "`SessionNarrated` is harmless, so its sibling is too" is wrong: the
    narrative carrier's record home is one row of SUMMARY text, while the turn
    carrier IS the transcript — a torn `TurnsRecorded` line drops a capture's
    turn set. So it must NOT be in the allowlist, and the classification is
    asserted through the classifier rather than by reading the set twice.
    """
    assert rp.TURNS_RECORD_TYPE not in tlog.TORN_TAIL_HARMLESS_EVENT_TYPES
    torn = '{"event_id": "e", "ts": "T", "type": "TurnsRecorded", "turns": [{"id"'
    assert tlog.torn_record_may_revive_state(torn) is True
    # The stale-turn purge is a HARD DELETE: a torn one loses the removal and a
    # replay resurrects the turns. `EntityMutated` must stay out of the set —
    # which is the same statement as "the delete itself is never tolerated".
    assert "EntityMutated" not in tlog.TORN_TAIL_HARMLESS_EVENT_TYPES
    assert tlog.torn_record_may_revive_state(
        '{"event_id": "e", "ts": "T", "type": "EntityMutated", "id": "x", "op": "delete"'
    ) is True


# ── §2 the payload is the WRITER'S, not a fixture's ────────────────────────


def _assert_snapshot_contract(snapshot: dict, *, optional=frozenset(),
                             context: str = "") -> None:
    """Assert ONE journalled turn snapshot against the declared key contract.

    ``TURNS_SNAPSHOT_KEYS`` is the ALWAYS set, ``TURNS_SNAPSHOT_OPTIONAL_KEYS``
    the at-most-one-branch set, and ``TURNS_SNAPSHOT_UNATTESTABLE_KEYS`` the pair a
    capture MAY omit TOGETHER when its prior-row read could not attest the stored
    values (#10021 review P3). Asserting plain equality here made the
    unattestable arm red with "the writer's per-turn snapshot changed" — a
    message naming the wrong cause — so the pin is stated as the contract.
    """
    keys = set(snapshot)
    allowed = rp.TURNS_SNAPSHOT_KEYS | optional
    assert not (keys - allowed), (
        f"{context}: the writer's per-turn snapshot gained {sorted(keys - allowed)}; "
        "TURNS_SNAPSHOT_KEYS is the contract step 7 inherits, so both move together")
    assert keys >= (rp.TURNS_SNAPSHOT_KEYS - rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS), (
        f"{context}: the snapshot lost {sorted((rp.TURNS_SNAPSHOT_KEYS - rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS) - keys)} — "
        "only the unattestable pair may be absent")
    omitted = keys & rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS
    assert omitted in (set(), set(rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS)), (
        f"{context}: {sorted(omitted)} — the unattestable pair is omitted "
        "TOGETHER or not at all (a half-omitted snapshot leaves one column "
        "asserting what the other refuses to)")
    assert not (keys & rp.TURNS_SNAPSHOT_OPTIONAL_KEYS - optional), (
        f"{context}: the optional branch {sorted((keys & rp.TURNS_SNAPSHOT_OPTIONAL_KEYS) - optional)} "
        "has no business in this snapshot")


def test_the_carrier_payload_keys_are_the_ones_the_capture_writer_builds(sdk):
    """Read off a REAL capture: the carrier's `turns` entries are these dicts.

    A documented payload with no producer to check it against is the claim this
    file refuses to make — so the key sets are compared to what
    `sdk._write_capture_turns` actually journalled, in the same run. If the
    writer's snapshot gains or loses a key, this reds and step 7 cannot move the
    producer onto a stale contract.
    """
    _sdk, path = sdk
    _capture(sdk[0], TURNS)
    records = _journal(path)
    envelope = [r for r in records if r.get("type") == "PointAdded" and _is_turn(r)]
    assert len(envelope) == len(TURNS), (
        f"the capture journalled {len(envelope)} turn record(s) for "
        f"{len(TURNS)} turn(s) — the population under test is wrong")
    for record in envelope:
        snapshot = record["point"]
        _assert_snapshot_contract(snapshot, context="a fresh no-vector capture")
        assert "contains_session" not in snapshot, (
            "the session link must ride the EVENT ENVELOPE (#3947), not the "
            "point payload — a node property the live write never sets")
        assert record.get("contains_session") == SESSION


def _turn_snapshots(path: Path) -> list[dict]:
    return [r["point"] for r in _journal(path)
            if r.get("type") == "PointAdded" and _is_turn(r)]


def test_the_encoded_vector_branch_carries_its_identity_STAMPS(sdk):
    """The other half of the payload contract, and the half a no-vector capture
    cannot see: when the write ENCODES a vector, `embeddings.stamp_journal_embedding(
    creating=True)` adds `embedding_model` / `embedding_revision` /
    `embedding_text_hash` to the journalled point.

    Pinned because step 7 must carry them VERBATIM: a carrier that kept
    `embedding` and dropped the identity would replay a vector with no origin,
    and a model change would then be SILENT — the failure #5004/R1 exists to
    prevent. A pin that only ever asserted the OPTIONAL keys ABSENT would let
    exactly that happen while staying green.
    """
    from tortoise.sdk import _write_capture_turns

    client, path = sdk
    _capture(client, TURNS)          # Session + turns, no vectors
    _write_capture_turns(
        client._get_proj(), client, SESSION, TURNS, now=NOW,
        turn_embs=[[0.1, 0.2], [0.3, 0.4]], turn_offset=2)
    encoded = _turn_snapshots(path)[-len(TURNS):]
    assert len(encoded) == len(TURNS)
    expected = {"embedding_model", "embedding_revision", "embedding_text_hash"}
    for snapshot in encoded:
        _assert_snapshot_contract(
            snapshot, optional=expected, context="an encoded-vector capture")
        assert snapshot["embedding"], "the encoded vector did not ride"
        assert snapshot["embedding_text_hash"], (
            "the stamp must name the text the vector describes (#4194)")


def test_the_CARRIER_branch_stamps_its_encoded_vector_too(tmp_path, monkeypatch):
    """§7 — the same contract on the branch Step 7 CREATED.

    The stamps reach a journalled snapshot through `_emit_event`'s `point=`
    argument. The carrier rides `turns=` → `**extra`, which bypasses that arm —
    so a carrier could keep `embedding` and drop `embedding_model` /
    `embedding_revision` / `embedding_text_hash`, exactly what
    `record_projection.TURNS_SNAPSHOT_OPTIONAL_KEYS` says the carrier must carry
    VERBATIM (a vector with no origin is the silent model change #5004/R1
    forbids). The graph half of this file cannot see it: it captures with no
    embedder, so every snapshot's `embedding` is `None`.

    Non-vacuity: pre-fix the carrier snapshots carry exactly
    `TURNS_SNAPSHOT_KEYS` here, so this reddens on the code it replaced — the
    encoded-vector pin above stayed green because it reads the UNARMED lane.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", "org-10067")
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", "graph-10067")
    from tortoise.sdk import TortoiseSDK, _write_capture_turns

    path = tmp_path / "cap" / "events.jsonl"
    path.parent.mkdir(parents=True)
    sdk = TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                      event_log_path=str(path))
    try:
        _capture(sdk, TURNS)
        _write_capture_turns(
            sdk._get_proj(), sdk, SESSION, TURNS, now=NOW,
            turn_embs=[[0.1, 0.2], [0.3, 0.4]], turn_offset=2)
    finally:
        sdk.close()
    carriers = [r for r in _journal(path)
                if r.get("type") == rp.TURNS_RECORD_TYPE]
    assert len(carriers) == 2, (
        f"expected one carrier per capture, got {len(carriers)}")
    encoded = carriers[-1]["turns"]
    expected = rp.TURNS_SNAPSHOT_KEYS | {
        "embedding_model", "embedding_revision", "embedding_text_hash"}
    for snapshot in encoded:
        assert set(snapshot) == expected, (
            "a carrier snapshot that ENCODED a vector must carry its identity "
            f"stamps: got {sorted(snapshot)} — without them the replay has a "
            "vector with no origin")
        assert snapshot["embedding"], "the encoded vector did not ride"
        assert snapshot["embedding_text_hash"] == _stamped_text_hash(snapshot["content"])


def _stamped_text_hash(content: str) -> str:
    """The stamp's own text hash, so the assertion is not `non-empty`."""
    from tortoise.sdk import _content_hash

    return _content_hash(content)


def test_a_PRESERVED_vector_carries_the_flag_and_withholds_the_identity(sdk):
    """#5004 round 3: a re-capture that encodes nothing PRESERVES the node's
    prior vector — and must NOT attest the active model, because that vector may
    come from a different one.

    Both directions are asserted, because they are one decision: the flag rides,
    the three identity stamps do not. Carrying them here would record a false
    origin, which is precisely what the flag exists to avoid.
    """
    from tortoise.sdk import _write_capture_turns

    client, path = sdk
    proj = client._get_proj()
    _capture(client, TURNS)
    _write_capture_turns(proj, client, SESSION, TURNS, now=NOW,
                         turn_embs=[[0.1, 0.2], [0.3, 0.4]])
    _write_capture_turns(proj, client, SESSION, TURNS, now=NOW,
                         turn_embs=[None, None])
    preserved = _turn_snapshots(path)[-len(TURNS):]
    assert len(preserved) == len(TURNS)
    for snapshot in preserved:
        _assert_snapshot_contract(snapshot, optional={"embedding_preserved"},
                                  context="a preserved-vector capture")
        assert "embedding_preserved" in snapshot, (
            f"the preserved-vector snapshot is {sorted(snapshot)} — the flag "
            "must ride; the identity stamps are the other branch")
        assert snapshot["embedding"], "the prior vector was not carried"


# ── §3/§4 the agreement: two routes, one answer ────────────────────────────


@pytest.fixture()
def recaptured(sdk):
    """Capture 1 (two turns), then a SHORTER re-capture (one turn).

    The second capture is the stale sweep: the id space beyond the new window is
    removed, and the writer journals that removal as a `PointRetracted` (the
    event-store half) plus a hard-delete `EntityMutated` (the replay half).

    Returns the two capture GROUPS, so the journal can be re-cut per capture
    without inventing a record the producer did not write.
    """
    client, path = sdk
    _capture(client, TURNS)
    first = len(_journal(path))
    _capture(client, TURNS[:1], session_existed=True)
    records = _journal(path)
    assert first < len(records), "the second capture journalled nothing"
    return records[:first], records[first:]


def _replay_both(tmp_path, monkeypatch, records) -> list:
    """Replay *records* through BOTH engines, asserting the same graph each time."""
    directory = _write_journal(tmp_path / "replay", records)
    client, proj = _fresh_projection(tmp_path, monkeypatch)

    from tortoise.consistency import recover_from_log

    try:
        report = recover_from_log(directory, proj)
        assert report["recovered"] is True, report
        first = _assert_no_turn_state(proj)

        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild_all(directory, confirm_destructive=True)
        second = _assert_no_turn_state(proj)
    finally:
        client.close()
    assert first == second, (first, second)
    return first


def _assert_no_turn_state(proj) -> set[str]:
    ids = _node_ids(proj)
    assert SESSION in ids, (
        "the replay produced no Session node — every 'no turn node' assertion "
        f"built on it would be vacuous (ids={sorted(ids)})")
    turn_nodes = sorted(i for i in ids if i.startswith(f"{SESSION}_t"))
    assert not turn_nodes, f"a turn node reached the graph: {turn_nodes}"
    for turn in TURNS:
        assert _text_hits(proj, turn["content"]) == [], (
            "a turn's text is in the graph after a record-carried replay")
    return ids


def test_neither_replay_engine_creates_a_turn_node(
        tmp_path, monkeypatch, recaptured):
    """§3 — the journal route, on both engines, from the writer's own snapshots."""
    journal = _record_carried(list(recaptured))
    carried = [r for r in journal if r.get("type") == rp.TURNS_RECORD_TYPE]
    # Non-vacuity: the transcript must BE in the journal, or "no turn node" is
    # satisfied by a replay that carried nothing at all.
    assert carried, "the re-cut journal holds no carrier"
    carried_text = [t["content"] for c in carried for t in c["turns"]]
    assert any(turn["content"] in text for turn in TURNS for text in carried_text), (
        f"the carrier does not hold the transcript ({carried_text}) — 'no turn "
        "node' would then be satisfied by a replay that carried nothing")
    _replay_both(tmp_path, monkeypatch, journal)


def _record_rows(snaps, tier: str = "record_only") -> list[dict]:
    """The `public.point` row a carrier turn is READ as — the reader's input.

    ⚠️ This is the READER's shape, not a model of a writer. Since #10067 the rows
    are really written, from the carrier's type declaration
    (`record_projection._turns_statements`); §6 pins that statement set — the
    `cache_tier` column included — against this reader, and §4/§5 pin the
    READER's decision on such a row.

    Only what `read_record_view` reads is built: identity, `is_episodic`, the tier
    as the row's own COLUMN (#10070: it cannot ride the payload — the strip
    removes it before the insert), and the props tail. `'graph'` is the schema
    default, which is what the control row in §4 exists to exercise.
    """
    return [{"id": snap["id"], "is_episodic": snap["is_episodic"],
             "cache_tier": tier, "props": {"id": snap["id"]}} for snap in snaps]


def _record_side(records) -> dict[str, dict]:
    """The record's state as this file MODELS it (see `_record_rows`): carriers
    contribute `record_only` rows, hard deletes remove one. This is the row set
    `read_record_view` then decides on."""
    rows: dict[str, dict] = {}
    for record in records:
        if record.get("type") == rp.TURNS_RECORD_TYPE:
            for row in _record_rows(record["turns"]):
                rows[row["id"]] = row
        elif record.get("type") == "EntityMutated" and record.get("op") == "delete":
            rows.pop(record.get("id"), None)
    return rows


def test_the_record_route_agrees_that_the_turns_are_not_graph_state(
        monkeypatch, recaptured):
    """§4 — the same journal, projected the way the RECORD will see it.

    The two routes agree, and the tier is what makes that structural: the rows
    EXIST (the transcript is served from them), they carry
    `cache_tier = 'record_only'`, and the cache reader therefore plans no node.
    The graph-tier control row proves the read is not simply empty.

    ⚠️ Scope, restated because it is the honest limit of this leg: the ROW SET
    here is the model `_record_rows` builds (see its docstring), so this pins the
    reader's decision and the row contract — the row WRITE is step 7's. The
    reader control itself is the tier suite's own (`tests/test_10017_cache_tier.py`
    imports it), because the `= 'graph'` predicate it inspects is
    spelling-sensitive (`cache_projection`, the partial-index reason) and two
    copies of that control would drift into two diagnoses.
    """
    journal = _record_carried(list(recaptured))
    rows = _record_side(journal)
    assert {r["cache_tier"] for r in rows.values()} == {"record_only"}
    assert set(rows) == {f"{SESSION}_t0"}, (
        "the record's turn set is the capture's — the shorter re-capture leaves "
        f"one turn: {sorted(rows)}")
    control = {"id": "p-real", "is_episodic": False, "cache_tier": "graph",
               "props": {"id": "p-real"}}
    view, sqls = _read_view(monkeypatch, [*rows.values(), control])
    assert [node.key for node in view.nodes] == ["p-real"], (
        f"the record route planned {[n.key for n in view.nodes]} — a turn row "
        "became a graph node, so `rebuild_from_record` and the journal replay "
        "disagree")
    assert any("cache_tier = 'graph'" in sql for sql in sqls), (
        "the reader no longer filters on the tier, so this test would pass on "
        "the pre-#10070 reader")


def test_a_shorter_recapture_removes_the_turn_on_both_routes(
        tmp_path, monkeypatch, recaptured):
    """§5 — the stale sweep, from both ends.

    The journal route never had the stale turn as a node (that is §3), and the
    record route removes its ROW: the sweep's hard-delete record projects to a
    `DELETE FROM public.point`, which is the only thing that can stop a
    record-sourced rebuild from resurrecting the row the journal replay skips.
    """
    journal = _record_carried(list(recaptured))
    stale = f"{SESSION}_t1"
    deletes = [r for r in journal
               if r.get("type") == "EntityMutated" and r.get("op") == "delete"]
    assert any(r.get("id") == stale for r in deletes), (
        "the shorter re-capture journalled no hard delete for the surplus turn")
    # The record half: that record's own statements remove the row.
    statements = rp.mutations_for(deletes[0], org_id="o", graph_id="g")
    assert any(st.sql.startswith("DELETE FROM public.point") for st in statements)
    assert any(stale in str(st.params) for st in statements), (
        f"the delete does not name the stale turn: {[st.params for st in statements]}")
    # …and the row set really loses it (the carriers alone would keep it).
    rows = _record_side(journal)
    assert stale not in rows and f"{SESSION}_t0" in rows
    # The graph half: the rebuild does not resurrect it either.
    ids = _replay_both(tmp_path, monkeypatch, journal)
    assert stale not in ids


# ── §7 the PRODUCER (#10067): a real capture, both rebuild routes ──────────


def test_a_record_armed_capture_journals_the_carrier_and_replays_to_no_turn_node(
        tmp_path, monkeypatch):
    """§7 — the defect itself, measured on the writer rather than on a fixture.

    #10067: the capture skipped the graph write (`_record_holds_turns`) and still
    journalled one `PointAdded` per turn, so `rebuild()` re-created every turn the
    running graph deliberately does not hold — after #10017's purge, undoing the
    migration within a day. Three assertions, and each one alone has been true
    while the defect stood:

      1. the journal carries ONE `TurnsRecorded` per capture and NO per-turn
         `PointAdded` (the record-shape half);
      2. the record's own plan writes every turn as a `public.point` row at
         `cache_tier = 'record_only'`, so the transcript still has a home (the
         non-vacuity half — without it the switchover stores the turns nowhere);
      3. a replay of that very journal materialises NO turn node (the defect).
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", "org-10067")
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", "graph-10067")
    from tortoise.sdk import TortoiseSDK

    path = tmp_path / "cap" / "events.jsonl"
    path.parent.mkdir(parents=True)
    sdk = TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                      event_log_path=str(path))
    try:
        _capture(sdk, TURNS)
    finally:
        sdk.close()
    journal = _journal(path)

    # (1) the record SHAPE: one carrier, and not one node-creating turn record.
    carriers = [r for r in journal if r.get("type") == rp.TURNS_RECORD_TYPE]
    assert len(carriers) == 1, (
        f"the capture journalled {len(carriers)} carrier(s) for one capture")
    assert not [r for r in journal if _is_turn(r)], (
        "a per-turn `PointAdded`/`PointRetracted` record survived the switchover")
    assert [t["id"] for t in carriers[0]["turns"]] == [f"{SESSION}_t0",
                                                      f"{SESSION}_t1"]

    # (2) the record ROUTE still holds the transcript, at the record-only tier.
    rows = [st for st in rp.mutations_for(carriers[0], org_id="org-10067",
                                          graph_id="graph-10067")
            if st.sql.startswith("INSERT INTO public.point ")]
    assert [r.params[3] for r in rows] == [f"{SESSION}_t0", f"{SESSION}_t1"]
    assert all("record_only" in r.params for r in rows), (
        "a turn row landed without the tier, so `rebuild_from_record` would "
        "re-create the very node this carrier keeps out of the graph")

    # (3) the GRAPH route: replay THIS journal into an empty graph.
    client, proj = _fresh_projection(tmp_path, monkeypatch)
    from tortoise.consistency import recover_from_log

    try:
        report = recover_from_log(str(path.parent), proj)
        assert report["recovered"] is True, report
        ids = _node_ids(proj)
        assert SESSION in ids, (
            f"the replay produced no Session node, so 'no turn node' is vacuous "
            f"(ids={sorted(ids)})")
        assert not [i for i in ids if i.startswith(f"{SESSION}_t")], (
            "a rebuild of the record-armed capture resurrected turn nodes — "
            "the #10067 defect")
    finally:
        client.close()


# ── §7b the PRODUCER on a MISCONFIGURED record (#10067 review ISSUE 1) ─────


def test_a_MALFORMED_dsn_keeps_the_turns_in_the_GRAPH_instead_of_raising(
        tmp_path, monkeypatch):
    """§7b — the DSN term is a QUESTION; a raise here half-applies the capture.

    `record_dsn()` does not merely answer armed/unarmed: it RAISES
    `RecordWriteError` for a wrong-scheme value, and for a right-scheme value
    with no host (the clipped `gh secret set` paste, #9548). The SEAM catches
    exactly that and answers `_refused("malformed_dsn", seq)` — but the
    capability read in `_write_capture_turns` did not, and it runs AFTER the
    `:Session` MERGE has committed. So a misconfigured `TORTOISE_RECORD_DSN`
    made the capture RAISE out of the writer with the graph turn batch never
    issued and no journal line written: the turns in NEITHER store, and an
    exception for a half-applied capture — #10067's defect through another door.

    The correct degradation, pinned here: `_record_holds_turns` is FALSE, so the
    graph keeps the turns and the journal records the node-creating shape it
    always did, and the seam still counts its own `malformed_dsn` refusal (the
    observable signal #9548 asks for). The value is asserted to be one
    `record_dsn()` REFUSES, so this test cannot pass on an armed DSN.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    # Right scheme, NO HOST — `libpq` would fill the host from `PGHOST` or a
    # unix socket, which is why `record_dsn` refuses this shape itself (#9548).
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://user:pw@")
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", "org-10067")
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", "graph-10067")
    from tortoise import monitoring
    from tortoise.record_writer import RecordWriteError, record_dsn
    from tortoise.sdk import TortoiseSDK

    with pytest.raises(RecordWriteError):
        record_dsn()

    path = tmp_path / "cap" / "events.jsonl"
    path.parent.mkdir(parents=True)
    sdk = TortoiseSDK(db_path=str(tmp_path / "cap.db"),
                      event_log_path=str(path))
    before = monitoring.record_write_failure_counts().get("malformed_dsn", 0)
    try:
        # Pre-fix this RAISES `RecordWriteError` out of the capture.
        _capture(sdk, TURNS)
        ids = _node_ids(sdk._get_proj())
    finally:
        sdk.close()
    after = monitoring.record_write_failure_counts().get("malformed_dsn", 0)
    journal = _journal(path)

    # (1) the JOURNALLED shape follows the write that HAPPENED: no carrier —
    # the record would refuse the row it authorises — and one node-creating
    # record per turn, the shape a graph-lane capture still writes.
    assert not [r for r in journal if r.get("type") == rp.TURNS_RECORD_TYPE], (
        "a DSN the record REFUSES still produced the record-only carrier, so "
        "the turns it authorises have no reader at all")
    turn_records = [r for r in journal if _is_turn(r)]
    assert len(turn_records) == len(TURNS), (
        f"the graph-lane fallback journalled {len(turn_records)} turn "
        f"record(s) for {len(TURNS)} turns")

    # (2) the turns really are GRAPH STATE — without this, "not in the record"
    # is satisfied by the turns being nowhere, which is the defect itself.
    assert SESSION in ids, f"no Session node, so the capture did not commit: {ids}"
    assert {f"{SESSION}_t0", f"{SESSION}_t1"} <= ids, (
        f"the fallback left the turns in neither store (ids={sorted(ids)})")

    # (3) the refusal is still COUNTED at the seam (#9548's signal), so the
    # misconfiguration is observable rather than silent.
    assert after > before, (
        f"the malformed DSN moved no `malformed_dsn` count ({before} → {after})")
