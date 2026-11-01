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
§1 the CARRIER is DECLARED in every registry a journal type is declared in; it
   IS in `log.TORN_TAIL_HARMLESS_EVENT_TYPES` (step 7's settled answer to the
   placeholder #10129 left — see the test), while the `EntityMutated` hard
   delete the stale sweep uses must stay REFUSED, because a tolerated torn
   delete is a resurrection (`#10017` step 5).
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

⛔ WHAT THIS FILE DOES NOT CLAIM. `_write_capture_turns` IS the producer since
#10017's step 7 — but on the RECORD-ARMED lane only, where an unarmed capture's
per-turn `PointAdded` path is untouched (#10000's capability rule). The journal
replayed here is therefore built from a REAL unarmed capture whose turn records
are moved onto the carrier by this file, so the snapshots are the producer's and
the CARRIAGE is the thing under test; `tests/test_10017_turn_carrier_replay.py`
is the file that reads the carrier off an armed capture directly.
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
    so journal ORDER is unchanged. The stale sweep's `PointRetracted` for a turn
    goes with them (there is no turn node left to retract); its hard-delete
    `EntityMutated` survives, because that is what removes the record ROW.
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


def test_the_carrier_declares_no_NODE_but_does_write_the_ROWS():
    """`NoRecordEffect` is a decision about the NODE, and step 7 supplies the ROW.

    A "no node effect" that emitted nothing at all would lose the transcript:
    §9.1's record home for the turns is `public.point`, and the per-turn
    `PointAdded` records the carrier replaces were the write that put them
    there. So the node half stays empty and the HOME half is the carrier-keyed
    `_turn_row_statements` — one row per turn, `cache_tier = 'record_only'`, plus
    the `node_identity` row that row's `relation` FK needs (which is identity
    bookkeeping, not a graph node — see the `⚠️` below).
    """
    snap = {"id": f"{SESSION}_t0", "content": "c",
            "pointKind": "event", "speaker": "user",
            "is_episodic": True, "status": "draft",
            "createdAt": NOW, "embedding": None}
    carrier = _carrier([snap])
    plan = rp.replay_plan([carrier], org_id="o", graph_id="g")
    assert [st for st in plan if st.sql.startswith("INSERT INTO public.point")], (
        "the carrier wrote no turn row — a producer turnover without this "
        "leaves the capture's turns in no store at all")
    # ⚠️ A `node_identity` row IS written, and it is not a graph node: the
    # record's node list comes from the node TABLES (`read_record_view`), and
    # the identity row is the FK anchor the turn's `relation` row needs — the
    # same rows the per-turn `PointAdded` lane wrote. What §4 pins is that the
    # READER plans no node for the row, which is the property that matters (and
    # which `tests/test_10017_cache_tier.py`'s reader leg measures).
    #
    # ⛔ There is no `assert not any("INSERT INTO public.node" ...)` here: no
    # migration creates a `public.node` table, so that string cannot appear in
    # any plan — the assertion could not fail, and it would have passed
    # identically on the pre-fix code. A guard that cannot fire is worse than
    # none: it reads as coverage.
    assert sum("INSERT INTO public.point" in st.sql for st in plan) == 1, (
        "the carrier wrote something other than one turn row per turn")
    # …and the DECLARED effect stays `NoRecordEffect`: a `NodeSnapshot` here
    # would be a second node-creating path the graph fold does not have.
    assert isinstance(rp.RECORD_MUTATIONS[rp.TURNS_RECORD_TYPE].effect,
                      rp.NoRecordEffect)


def test_the_carrier_is_TOLERATED_as_a_torn_tail_and_the_DELETE_is_not():
    """The polarity — SETTLED by step 7, which is what #10129 left it for.

    #10129 declared this a PLACEHOLDER and named the two things that decide it:
    the WRITE ORDER, and whether the turns survive in the record when the append
    tears. Measured: `_emit_event` returns on an append failure BEFORE
    `_write_record_projection` runs, so a torn carrier means that capture's turn
    rows never reached the record either — refusing the tail recovers nothing
    for it, while `recover_from_log` then refuses the WHOLE journal
    (`recovered=False, nodes=0`). The carrier is ADDITIVE, so the revival class
    this list exists for (a torn DELETE resurrecting removed state) cannot be
    expressed by it.
    """
    assert rp.TURNS_RECORD_TYPE in tlog.TORN_TAIL_HARMLESS_EVENT_TYPES
    torn = '{"event_id": "e", "ts": "T", "type": "TurnsRecorded", "turns": [{"id"'
    assert tlog.torn_record_may_revive_state(torn) is False
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
    """The `public.point` row step 7's writer must write for one carrier turn.

    ⛔ THIS IS A MODEL OF THE ROW, and the disclosure matters: what §4/§5 below
    pin is the ROW CONTRACT and the READER's decision on it, not the row WRITE.
    The row WRITE is step 7's and is pinned where it lives —
    `record_projection._turn_row_statements`, one `public.point` row per turn at
    `cache_tier = 'record_only'`, asserted in
    `tests/test_10017_turn_carrier_replay.py`
    (`test_the_record_writes_one_ROW_per_turn_and_the_tier_goes_to_the_COLUMN`)
    and in the tier suite. Modelled here so the READER half can be exercised with
    a row this file controls — including the `'graph'` control row that proves
    the assertion is about the TIER and not about an empty view.

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


# ── §2b the ENRICHED branch is inside the declaration too (#8488) ──────────


def test_an_enriched_capture_journals_no_UNDECLARED_snapshot_key(sdk, monkeypatch):
    """#8488 (review round 2, P1): the enriched turn snapshot is DECLARED.

    When `TORTOISE_CONTEXT_ENRICH` is ON the writer puts the enriched text on
    the SAME per-turn snapshot as the vector it describes. `TURNS_SNAPSHOT_KEYS`
    and `TURNS_SNAPSHOT_OPTIONAL_KEYS` are the contract step 7 inherits, and the
    failure mode of a key outside both is silent rather than loud: it lands in
    the props JSON tail on the record-armed lane instead of a declared field.

    Nothing in the existing suite could see that: the key only appears when the
    gate is ON, and every other capture leg runs with it unset — so the drift
    was dormant, not absent. This leg reads the ON writer's own journal.
    """
    from tortoise import model_adapters
    from tortoise.context_enrich import ENRICH_ENV

    class _Rewriter:
        def complete(self, *, system, user):
            return "ENRICHED " + user.split(
                "TARGET turn (rewrite this one):\n", 1)[1].split("\n\n", 1)[0]

    from tortoise.sdk import _capture_enriched_texts

    monkeypatch.setenv(ENRICH_ENV, "1")
    monkeypatch.setattr(model_adapters, "build_extractor_model",
                        lambda *a, **kw: _Rewriter())
    # The REAL chain: the gate reads `TORTOISE_CONTEXT_ENRICH`, the model seam
    # is patched (no network), and `_write_capture_turns` is where the snapshot
    # is built — the same `enrich_fn` `capture_session` passes (`sdk.py:13349`).
    _capture(sdk[0], TURNS, enrich_fn=_capture_enriched_texts)

    snaps = _turn_snapshots(sdk[1])
    assert len(snaps) == len(TURNS), snaps
    declared = rp.TURNS_SNAPSHOT_KEYS | rp.TURNS_SNAPSHOT_OPTIONAL_KEYS
    for snap in snaps:
        # Non-vacuous: the leg is about the ENRICHED payload, so the enriched
        # key must actually be there.
        assert "embeddingText" in snap, (
            "the gate was ON but the snapshot carried no embeddingText — this "
            "leg would then assert nothing about the key it exists for")
        undeclared = sorted(set(snap) - declared)
        assert not undeclared, (
            f"the ENRICHED capture journalled {undeclared}, which is in neither "
            "TURNS_SNAPSHOT_KEYS nor TURNS_SNAPSHOT_OPTIONAL_KEYS — on the "
            "record-armed lane that key rides the props JSON tail instead of a "
            "declared field (#8488)")
        # …and the rest of the branch contract still holds for the enriched
        # snapshot (its text is attested, so the identity stamps ride with it).
        _assert_snapshot_contract(snap, optional={"embeddingText"},
                                 context="an ENRICHED capture")
