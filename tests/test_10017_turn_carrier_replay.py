"""#10017 R1 + R2 — a REBUILD must not resurrect the turns a purge removed.

Two recorded rulings from #10017 (owner, 2026-10-26T15:48:12Z), implemented
together because they are two routes to ONE resurrection:

**R2 — the record-carrier event.** On a record-armed deployment the writer
already refuses to mint turn nodes (#10000) but still journalled each turn as
``PointAdded``, a NODE-CREATING record. So a journal rebuild produced a graph
the running deployment did not have, and it re-created every turn #10017's purge
had removed. The carrier is ``TurnsRecorded`` — ONE record per capture holding the
writer's per-turn snapshots — with NO graph fold and a RECORD effect of one
``public.point`` row per turn at ``cache_tier = 'record_only'``, so
``rebuild_from_record`` skips them too. Two rebuild routes, one graph.

⚠️ ``TurnsRecorded``'s declaration (and its payload pin) is main's, from #10129;
this file pins STEP 7, the producer turnover, on top of it: the journal carries
the carrier, the record carries the rows, the graph carries no turn, and the
change feed carries neither the creation nor the stale-sweep deletion.

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

import sys

import pytest

import tortoise.log as log_mod
import tortoise.projection as projection_mod
import tortoise.record_projection as rp
import tortoise.record_read as record_read
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
#: agrees with any key set, including a wrong one. The two declarations the
#: writer is held to are cross-checked against `record_projection` in
#: `test_the_payload_expectation_here_IS_the_declared_contract` below, so this
#: local copy cannot drift from the contract in either direction.
TURN_SNAPSHOT_KEYS = frozenset({
    "id", "content", "pointKind", "speaker", "is_episodic", "status",
    "createdAt", "embedding",
})

#: The pair a capture may OMIT from the carrier, and only together (#10021 P1).
#: `record_projection._upsert_statement` writes `status`/`createdAt` from
#: EXCLUDED ABSOLUTELY, so a capture that could not READ the record's prior row
#: omits both rather than asserting its own `now`/`draft` over a stored turn —
#: that is a re-stamp and a demotion on the store of record. Kept here as the
#: same kind of longhand copy as `TURN_SNAPSHOT_KEYS`, and pinned to
#: `rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS` in the leg below.
TURN_SNAPSHOT_UNATTESTABLE_KEYS = frozenset({"status", "createdAt"})

#: The keys that ride on ONE branch only (#5004): `embedding_preserved` when the
#: write PRESERVED a prior vector and so withholds the origin, or the three
#: identity stamps when it ENCODED one. ⛔ Deliberately NOT part of an exact-set
#: expectation: WHICH branch is taken depends on whether the embedder is
#: available, so an exact set makes this leg environment-dependent (a dev box
#: with `--extra embeddings` loads it, CI does not — the #2573/#2772 class
#: `tests/conftest.py::force_sparse_tfidf` exists for) AND it would forbid the
#: identity stamps the carrier is REQUIRED to carry (`record_projection.
#: TURNS_SNAPSHOT_OPTIONAL_KEYS`: *"⛔ Step 7 must carry them VERBATIM"*).
#: The property this leg defends is "no `Point`-shaped key the writer never
#: emits, and no required key dropped" — inclusion, on both lanes.
TURN_SNAPSHOT_BRANCH_KEYS = frozenset({
    "embedding_preserved",
    "embedding_model", "embedding_revision", "embedding_text_hash",
})

#: The three stamps that cannot coexist with `embedding_preserved`.
TURN_SNAPSHOT_IDENTITY_KEYS = frozenset({
    "embedding_model", "embedding_revision", "embedding_text_hash",
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


def _drive_turn_writer(sdk, turns=None) -> None:
    """Drive the SURVIVING turn writer — `sdk.capture_session` is gone (#4282).

    `#4282` (2026-10-27) DELETED the public `TortoiseSDK.capture_session` wrapper as part
    of the authorised SDK-surface reduction. Every leg below is about the TURN CARRIER —
    that a turn is a record row and not a graph node, that the change-feed does not
    announce it on the armed lane, and that a journaled replay reproduces it — and that
    behaviour lives in the module-level `_write_session_and_turns`, which
    `test_the_carrier_carries_the_ENCODED_identity_stamps` in this same file has always
    driven directly.

    ⚠️ WHAT WAS LOST WITH THE DOOR, stated because it is not nothing: the wrapper did more
    than wrap — it resolved the extractor, minted the `sessionCaptured` Event, ran
    `_materialize_session_source` and ran the extraction — `test_9078`'s restored legs drive
    the mint, the materialization and the extraction, plus the shared writer, to reach the
    record's event stream. It also returned a
    receipt, and it was read at TWO call sites this change replaces: the shared
    `_capture_with_record_armed` helper (behind the armed legs that use it), and
    `test_the_carrier_survives_an_unimportable_embeddings_seam` — both `assert
    res["ok"] is True`. Those gates are gone with the wrapper and nothing here
    re-establishes them. What remains is what this file is about: the carrier's own
    survival and replay, asserted through the journal, the store, the planned projection
    SQL and the module's own pins.
    """
    sdk_mod._write_session_and_turns(
        sdk._get_proj(), sdk, SESSION_ID, CONV if turns is None else turns,
        now="2026-10-25T00:00:00+00:00")



# ════════════════════════════════════════════════════════════════════════════
# R2 — the carrier: a turn is a record row and NO graph node
# ════════════════════════════════════════════════════════════════════════════


def _applied_seam(*_a, **_k):
    """A `write_live_record` stand-in that reports the record TOOK the write.

    ⛔ NOT `lambda *a, **k: None` (#10000/#10021). `None` is that contract's
    spelling for *not armed — nothing attempted*, and the writer's store decision
    reads it as "the record did not take these turns" and RESCUES them into the
    GRAPH. The legs below assert the graph stayed EMPTY, which is a property of a
    record write that APPLIED; a refused one is *supposed* to reach the graph, and
    that path has its own pin in `tests/test_10000_turns_record_only.py`
    (`test_a_partially_refused_capture_is_served_from_both_stores`).
    """
    from tortoise.record_writer import RecordWrite, RecordWriteOutcome

    return RecordWrite(outcome=RecordWriteOutcome.APPLIED, events=1,
                       statements=1, first_seq=1, last_seq=1)


def _capture_with_record_armed(sdk, monkeypatch, turns=CONV) -> None:
    """Capture on the record-held lane, with Postgres stubbed out entirely.

    ⛔ THE TENANT IS ARMED HERE TOO, not only on the `armed_sdk` fixture: a leg
    that builds its OWN SDK (the MIXED-generation leg does, because it needs a
    feed it can inspect across two captures) would otherwise run un-tenanted,
    and an un-tenanted capture is CORRECTLY the graph lane (`no_tenant` ⇒ the
    record cannot hold the write) — so the leg would assert the carrier never
    arrives and report the store decision as a defect. `_bind_record_tenant` is
    idempotent, so re-arming on the fixture's SDK costs nothing.

    ⛔ AND THE PREDICATE IS ASSERTED, not assumed (#10076 review P1). The whole
    file was previously green-on-the-wrong-lane because the DSN alone does NOT
    make the record capable: `record_holds_turns` resolves the tenant from the
    SDK and answers `no_tenant` without one, so every leg took the GRAPH lane
    and the ones that assert a carrier record could not pass. Re-deriving the
    same question here means a future fixture that forgets a half of the
    capability fails at the SETUP, naming the missing half, instead of
    surfacing as a carrier leg that mysteriously emits `PointAdded`.

    ⛔ AND THE RECORD'S RESIDUE READ IS MODELLED. `_record_turn_residue` asks
    POSTGRES for the turn rows at/above this window (`turn_keys_at_or_above`);
    the DSN here is synthetic and no driver is installed, so the read raises and
    degrades to `None` — which is right in production but leaves the SHORTER
    re-capture legs with no population to sweep, because on the RECORD-HELD lane
    the graph cannot name the surplus either (the turns are deliberately not
    graph nodes). So the ids the record HOLDS are carried in
    ``sdk._stub_record_turn_rows`` and mutated at the one place the real writer
    mutates the record (after a capture lands), while the sweep consumes them
    through the SAME seam its production caller uses. The read itself is pinned
    against a connection fake in `tests/test_10000_turns_record_only.py`; this
    file is not a second test of it.
    """
    sdk._bind_record_tenant("org-1", "g-1")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _applied_seam, raising=False)

    from tortoise.record_writer import record_holds_turns

    assert record_holds_turns(
        journal_configured=sdk._get_event_log() is not None,
        tenant=getattr(sdk, "_record_tenant", None)) is True, (
        "this capture cannot be put on the record-held lane, so the leg below "
        "would assert about the GRAPH lane and pass for the wrong reason — "
        "the record needs an armed DSN, a journal AND a tenant")

    held = getattr(sdk, "_stub_record_turn_rows", {})
    monkeypatch.setattr(
        sdk_mod, "_record_turn_residue",
        lambda _sdk, sid, first, _held=held: sorted(
            tid for tid in _held.get(sid, ())
            if int(tid.rpartition("_t")[2]) >= first))

    _drive_turn_writer(sdk, turns)
    held[SESSION_ID] = [f"{SESSION_ID}_t{i}" for i in range(len(turns))]
    sdk._stub_record_turn_rows = held


@pytest.fixture()
def armed_sdk(tmp_path, monkeypatch):
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=_journal(tmp_path))
    # ⛔ A TENANT IS PART OF "THE RECORD CAN HOLD THIS WRITE" (#10000/#10021).
    # The carrier is gated on `record_writer.record_holds_turns` — the WRITE
    # door's own predicate (journal + DSN + a tenant + a closed breaker), which
    # is the same declaration that decided to SKIP the graph write. A DSN alone
    # is not the capability: without a tenant the record refuses the write
    # `no_tenant`, so skipping the graph would lose the turn, and the graph lane
    # is then the correct answer. `tests/test_10000_turns_record_only.py` asks
    # the same question the same way (`_bind_tenant`).
    sdk._bind_record_tenant("org-1", "g-1")
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
    assert rp.TURNS_RECORD_TYPE in types, types
    carriers = [r for r in records if r["type"] == rp.TURNS_RECORD_TYPE]
    assert len(carriers) == 1, (
        "the carrier is ONE record per capture (#10129's declaration): "
        f"{len(carriers)} found for one capture")
    assert [s["id"] for s in carriers[0]["turns"]] == EXPECTED_TURN_IDS, (
        "the carrier's turn set is not the writer's")
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


def test_the_payload_expectation_here_IS_the_declared_contract():
    """The local expectation is a COPY of the contract, never a second one.

    `TURN_SNAPSHOT_KEYS` is written out longhand above so the assertion cannot
    agree with any key set the writer happens to emit; this leg is what keeps
    that honest by pinning the copy to `record_projection`'s declaration. A key
    added to (or dropped from) the contract then reds HERE, deliberately, in
    the same commit that changes it — instead of the payload leg going green on
    a set it silently inherited.
    """
    assert TURN_SNAPSHOT_KEYS == rp.TURNS_SNAPSHOT_KEYS
    assert TURN_SNAPSHOT_BRANCH_KEYS == rp.TURNS_SNAPSHOT_OPTIONAL_KEYS
    assert TURN_SNAPSHOT_UNATTESTABLE_KEYS == rp.TURNS_SNAPSHOT_UNATTESTABLE_KEYS


def test_the_carrier_carries_the_writers_own_keys(armed_sdk, monkeypatch):
    """The payload is the WRITER'S, verbatim — the contract step 7 inherits.

    Pinned because it is silent when wrong: a `Point`-shaped key the writer never
    emits becomes a phantom node property on replay, and a key it emits but the
    record drops loses data. `cache_tier` is deliberately NOT here — it cannot
    ride a payload (`_RECORD_ONLY_KEYS` strips it) and the ROW states it as its
    own column (see `test_the_record_writes_one_ROW_per_turn_...` below).

    Enforced as a two-sided INCLUSION, not an exact set: the base keys are
    REQUIRED (a dropped one is data loss), the branch keys are PERMITTED (which
    branch the write takes depends on the embedder, and both are legitimate —
    see `TURN_SNAPSHOT_BRANCH_KEYS`), and anything else is a phantom.
    """
    _capture_with_record_armed(armed_sdk, monkeypatch)
    carrier = next(r for r in _records(_journal_of(armed_sdk))
                   if r["type"] == rp.TURNS_RECORD_TYPE)
    assert len(carrier["turns"]) == len(CONV)
    allowed = TURN_SNAPSHOT_KEYS | TURN_SNAPSHOT_BRANCH_KEYS
    for snapshot in carrier["turns"]:
        keys = frozenset(snapshot)
        assert keys >= (TURN_SNAPSHOT_KEYS - TURN_SNAPSHOT_UNATTESTABLE_KEYS), (
            "the carrier's snapshot drifted from the writer's payload — "
            f"missing={sorted((TURN_SNAPSHOT_KEYS - TURN_SNAPSHOT_UNATTESTABLE_KEYS) - keys)}")
        # #10021 P1: the un-attestable pair is omitted TOGETHER or not at all.
        # A capture whose prior-row read raised cannot claim the stored turn is
        # `now`/`draft`, and half the pair is the shape no producer emits.
        assert (TURN_SNAPSHOT_UNATTESTABLE_KEYS & keys) in (
            frozenset(), TURN_SNAPSHOT_UNATTESTABLE_KEYS), (
            "the un-attestable pair must be omitted together or carried "
            f"together: saw {sorted(keys)}")
        assert keys <= allowed, (
            "the carrier carries a key the writer's payload contract does not "
            f"declare — extra={sorted(keys - allowed)}")
        assert "cache_tier" not in snapshot
        assert not ({"embedding_preserved"} & keys
                    and TURN_SNAPSHOT_IDENTITY_KEYS & keys), (
            "the preserved-vector branch must WITHHOLD the origin stamps "
            f"(#5004 round 3): saw {sorted(keys)}")
        assert snapshot["is_episodic"] is True
    # The session link rides the ENVELOPE, exactly as it does for `PointAdded`
    # (#3947) — never a per-turn key the live write does not set.
    assert carrier["contains_session"] == SESSION_ID


def test_the_carrier_carries_the_prior_rows_status_and_created_at(
        armed_sdk, monkeypatch):
    """The OTHER arm of #10021's omission rule, on the carrier lane.

    A capture that CAN read the record's prior row journals that row's
    `status`/`createdAt` — the values a replay must leave alone. Without this
    leg the omission arm above is the only pinned behaviour, and a writer that
    simply dropped the pair everywhere would satisfy it.
    """
    monkeypatch.setattr(
        sdk_mod, "_record_prior_vectors",
        lambda *a, **k: {
            f"{SESSION_ID}_t0": record_read.PriorTurn(
                content="[user] hi", embedding=None,
                created_at="2026-01-01T00:00:00+00:00", status="established"),
        })
    _capture_with_record_armed(armed_sdk, monkeypatch)
    carrier = next(r for r in _records(_journal_of(armed_sdk))
                   if r["type"] == rp.TURNS_RECORD_TYPE)
    snapshot = carrier["turns"][0]
    assert snapshot["createdAt"] == "2026-01-01T00:00:00+00:00", (
        "a re-capture re-stamped the record's created_at on the carrier "
        "instead of journalling the value it read")
    assert snapshot["status"] == "established", (
        "a re-capture demoted a promoted turn to draft on the store of record "
        "(#10000 review P1, on the carrier lane)")


def test_the_carrier_carries_the_ENCODED_identity_stamps(armed_sdk, monkeypatch):
    """#5004's attestation on the CARRIER lane — the highest-volume producer.

    `_emit_event` gates the attestation on the record TYPE (`creating=type_ in
    ("PointAdded", "OperatorAdded")`), and the carrier is neither — so
    `_write_capture_turns` stamps it explicitly on each snapshot it puts in the
    carrier. Without that, an armed capture would journal a vector with NO
    origin while the graph lane attests one: the same capture would yield
    different record content depending on whether `TORTOISE_RECORD_DSN` is set,
    and a model change would go SILENT for turns — the failure #5004/R1 ("the
    payload carries the vector VERBATIM plus the identity it was computed
    under") exists to expose.

    Driven through `embed_fn` with an INJECTED vector batch, so the encoded
    branch is taken deterministically: no ambient embedder, no model download,
    no network — the branch that made the sibling leg environment-dependent is
    produced here on purpose instead.
    """
    from tortoise.embeddings import embedding_identity

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _applied_seam, raising=False)
    proj = armed_sdk._get_proj()
    sdk_mod._write_session_and_turns(
        proj, armed_sdk, SESSION_ID, CONV, now="2026-10-25T00:00:00+00:00",
        embed_fn=lambda texts: [[0.1, 0.2, 0.3, 0.4] for _ in texts])
    carrier = next(r for r in _records(_journal_of(armed_sdk))
                   if r["type"] == rp.TURNS_RECORD_TYPE)
    model, revision = embedding_identity()
    for snapshot in carrier["turns"]:
        keys = frozenset(snapshot)
        assert snapshot["embedding"], (
            "the injected vector did not ride the carrier — the identity "
            "assertion below would then be vacuous (no vector, no origin)")
        assert keys >= TURN_SNAPSHOT_IDENTITY_KEYS, (
            "the CARRIER journalled a vector with no origin — `stamp_journal_"
            "embedding(creating=True)` did not run on this lane, so a model "
            f"change is silent for turns (saw {sorted(keys)})")
        assert snapshot["embedding_model"] == model
        assert snapshot["embedding_revision"] == revision
        assert snapshot["embedding_text_hash"], (
            "the stamp must name the text the vector describes (#4194)")
        assert "embedding_preserved" not in keys


def test_the_record_writes_one_ROW_per_turn_and_the_tier_goes_to_the_COLUMN():
    """R2's record half: THE ROWS EXIST, and each declares its placement.

    The per-turn `PointAdded` records this carrier replaces ARE the record's
    turn-row write (the writer issues no row statement of its own), so a
    producer turnover without this statement would leave a capture's turns in NO
    store — measured, not inferred: `mutations_for` on the carrier is what
    `write_live_record` and `replay_plan` both run.
    """
    carrier = {
        "type": rp.TURNS_RECORD_TYPE, "sessionId": SESSION_ID,
        "contains_session": SESSION_ID,
        "turns": [{"id": f"{SESSION_ID}_t{i}", "content": f"turn {i}",
                   "pointKind": "event", "speaker": "user",
                   "is_episodic": True, "status": "draft",
                   "createdAt": "2026-10-25T00:00:00Z", "embedding": None}
                  for i in range(3)],
    }
    statements = rp.mutations_for(carrier, org_id="o", graph_id="g")
    inserts = [s for s in statements
               if s.sql.startswith("INSERT INTO public.point")]
    assert len(inserts) == 3, [s.sql[:60] for s in statements]
    for insert in inserts:
        assert "cache_tier" in insert.sql, insert.sql
        assert rp.RECORD_ONLY_TIER in insert.params, insert.params
        # …on the COLUMN, never through the props tail (the passthrough a
        # public write could reach).
        assert "cache_tier" not in str(insert.params[-1]), insert.params[-1]
    # One CONTAINS relation per turn — the same `relation` rows the per-turn
    # `PointAdded` lane wrote, so the record's edge graph is unchanged.
    contains = [s for s in statements
                if s.sql.startswith("INSERT INTO public.relation")]
    assert len(contains) == 3, [s.sql[:60] for s in statements]
    # The `node_identity` rows are PRESENT, deliberately: they are the record's
    # identity registry (the composite FK `relation.src_key`/`tgt_key` point at),
    # not a node table — `cache_projection.read_record_view` builds nodes from
    # the node TABLES and reads `node_identity` only for the key contract. The
    # per-turn `PointAdded` lane wrote them too, so keeping them is what makes
    # the record's shape unchanged by the producer turnover; what changed is the
    # TIER on the row, which is what makes the reader skip it.
    assert any("node_identity" in s.sql for s in statements), (
        "no `node_identity` row — the CONTAINS relation rows below cannot "
        "satisfy their composite FK")


def test_a_carrier_with_no_turns_is_REFUSED():
    """A carrier that carries no turn reports a capture as filed while the
    record holds none of its transcript — refuse rather than emit nothing."""
    with pytest.raises(rp.RecordProjectionError, match="turns"):
        rp.mutations_for({"type": rp.TURNS_RECORD_TYPE,
                          "turns": []}, org_id="o", graph_id="g")


def test_an_armed_capture_writes_NO_graph_event_for_its_turns(
        armed_sdk, monkeypatch):
    """THE CHANGE-FEED DECISION, pinned (#10017 step 7; required by the issue's
    own instruction of 2026-10-27T03:21:55Z).

    On this lane the writer deliberately stores no turn NODE, so a `:GraphEvent`
    about a turn would advertise an entity no graph read can return. Turns
    therefore LEAVE `poll_events` — at BOTH ends: no creation row (the carrier is
    not in `_GRAPH_EVENT_TYPES`) and no stale-sweep `PointRetracted` row (gated on
    the same capability), which a previous head emitted for a turn the feed never
    saw created.

    The second capture is SHORTER, so it runs the stale sweep over turn `_t2` —
    that is the leg a creation-only assertion would miss.
    """
    _capture_with_record_armed(armed_sdk, monkeypatch)
    _capture_with_record_armed(armed_sdk, monkeypatch, turns=CONV[:1])
    proj = armed_sdk._get_proj()
    rows = proj.g.query(
        "MATCH (e:GraphEvent) WHERE e.payload CONTAINS $sid "
        "RETURN e.type, e.payload", params={"sid": SESSION_ID}).result_set
    turn_payloads = [row for row in rows
                     if any(tid in str(row[1]) for tid in EXPECTED_TURN_IDS)]
    assert turn_payloads == [], (
        "the change feed gained an event about a turn the graph does not hold: "
        f"{turn_payloads}")
    # `poll_events` is the subscriber surface the drop is visible on.
    served = armed_sdk.poll_events()
    assert not [e for e in served["events"]
                if any(tid in str(e) for tid in EXPECTED_TURN_IDS)], (
        "poll_events still serves a turn event on the record-held lane")
    # ⛔ AND NOTHING ELSE ANNOUNCES THE CAPTURE ON THE FEED EITHER. The rationale
    # this leg makes falsifiable is `record_projection.TURNS_RECORD_TYPE`'s
    # docstring: `SessionRecorded` is NOT a `:GraphEvent` type, so it writes no
    # feed row — the armed lane's change-feed visibility ends with the extraction
    # lane's claim Points. A comment claiming otherwise (as an earlier head did)
    # is the contract lying about the code, and this is what reds it.
    assert "SessionRecorded" not in sdk_mod._GRAPH_EVENT_TYPES, (
        "SessionRecorded became a `:GraphEvent` type — the carrier's docstring "
        "now understates what the feed serves; re-derive it, do not delete this pin")
    assert not [row for row in rows if row[0] == "SessionRecorded"], (
        f"the capture IS announced on the change feed: {rows}")
    assert "SessionRecorded" not in str(served["events"]), (
        "poll_events announced the capture on the record-held lane — the "
        "rationale in record_projection.TURNS_RECORD_TYPE says it does not")


def test_the_carrier_survives_an_unimportable_embeddings_seam(
        armed_sdk, monkeypatch):
    """#5148: a WRITE must not depend on an import — the armed lane included.

    `_emit_event` guards this identical import and states the rule; the carrier
    branch did not, and it runs LATE — the stale-turn removals are already
    journaled by then — so an ImportError ended a capture that had already
    mutated the record and reached NEITHER the carrier nor the trailing
    `SessionRecorded`. Pre-fix this reds with the ImportError; post-fix the
    capture completes, the carrier is journalled, and only the #5004
    attestation is absent (the fail-soft shape `_capture_turn_embeddings`
    already uses earlier on the same call path).
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://unused/record")
    monkeypatch.setattr("tortoise.record_writer.write_live_record",
                        _applied_seam, raising=False)
    # ⛔ BUILD THE STORE BEFORE POISONING THE SEAM. `TortoiseSDK._get_proj()` is
    # LAZY, and on a non-embedded lane `FalkorProjection.__init__` reads
    # `EMBEDDING_DIM` from this very module
    # (`projection/__init__.py::_ensure_vector_index_api`, inside the
    # vector-index block the embedded lane short-circuits). Poisoning first
    # therefore reds this leg in `_ensure_indexes` — the STORE's construction,
    # not the carrier under test — which is exactly what CI's docker lane did
    # while the embedded carve-out passed: a lane-dependent failure of the
    # TEST, reported as a failure of the guard.
    armed_sdk._get_proj()
    # An import-HOOK failure, not a missing dep: the module exists, the import
    # raises. `None` in `sys.modules` is exactly that shape. Set from here on,
    # it reaches the CARRIER's stamp import and nothing on the construction
    # path.
    monkeypatch.setitem(sys.modules, "tortoise.embeddings", None)
    _drive_turn_writer(armed_sdk)
    records = _records(_journal_of(armed_sdk))
    assert [r for r in records if r["type"] == rp.TURNS_RECORD_TYPE], (
        "the carrier never reached the journal — the capture ended on the "
        "import, after the record had already been mutated")


def test_the_GRAPH_held_lane_still_announces_its_turns(tmp_path, monkeypatch):
    """The NEGATIVE CONTROL for the drop above: with no record armed the graph
    holds the turns, so the per-turn `PointAdded` — and its `:GraphEvent` — must
    still exist. Without this leg the "no turn event" assertion would pass on a
    lane that emits no events at all."""
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=_journal(tmp_path))
    try:
        _drive_turn_writer(sdk)
        rows = sdk._get_proj().g.query(
            "MATCH (e:GraphEvent {type:'PointAdded'}) RETURN e.payload"
        ).result_set
        served = " ".join(str(r[0]) for r in rows)
        assert SESSION_ID in served, (
            "the graph-held lane emitted no PointAdded event for the capture — "
            "the negative control for the change-feed drop is vacuous")
    finally:
        sdk.close()


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
    assert rp.TURNS_RECORD_TYPE in projection_mod._NO_PROJECTION_FOLD
    assert rp.TURNS_RECORD_TYPE in projection_mod._NO_POINT_FOLD


def test_a_torn_tail_carrying_the_carrier_does_not_block_recovery():
    """R2, third home: `log.py`'s torn-tail allow-list.

    Its polarity is deliberate — a NEW type defaults to REFUSED — so omitting
    the carrier would make `rebuild`/`recover_from_log`/`backup.restore` refuse
    an intact journal whose last append was a capture cut off mid-line. #10129
    left this classification as a PLACEHOLDER for step 7; it is settled here:
    `_emit_event` returns on an append failure BEFORE the record write, so a torn
    carrier means that capture reached no store either way — refusing would make
    the whole journal unrecoverable for a loss it cannot recover. The R1
    REMOVAL record must stay OUT of it: a tolerated torn delete is a
    resurrection.
    """
    assert rp.TURNS_RECORD_TYPE in log_mod.TORN_TAIL_HARMLESS_EVENT_TYPES
    # …while the R1 removal record stays refused, so the asymmetry is pinned in
    # the same leg that pins the tolerance.
    assert "EntityMutated" not in log_mod.TORN_TAIL_HARMLESS_EVENT_TYPES


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
        _drive_turn_writer(sdk)   # graph-held lane
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
        _drive_turn_writer(sdk)
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


def test_the_stale_sweep_journals_a_TIERLESS_delete_not_a_demote(
        tmp_path, monkeypatch):
    """The SHORTER-RE-CAPTURE sweep removes the record ROW — deliberately.

    There are TWO removals on the record-held lane and they mean different
    things (#3300), so this leg pins which one the sweep is, because a comment
    alone already got it wrong once (`sdk.py`'s `_write_capture_turns` said the
    sweep's record half was a TIER DEMOTE, while the emission beside it was
    tier-less).

    * the PURGE's removal (`TortoiseSDK._journal_turn_removal`) is a DEMOTE —
      the record is the turn's home and the transcript must survive;
    * this SWEEP's removal is a real row DELETE — a turn the shorter re-capture
      dropped is no longer in the session, and `record_read._turns` carries NO
      tier predicate, so demoting instead of deleting would keep SERVING it.

    Mutation that reddens it: pass `cache_tier=rp.RECORD_ONLY_TIER` to the
    sweep's `_journal_entity_mutation` call (i.e. follow the old comment). The
    record then carries the tier and `mutations_for` returns the `UPDATE`
    demote instead of the `DELETE` pair — both assertions below fail.
    """
    log_path = _journal(tmp_path)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        _capture_with_record_armed(sdk, monkeypatch, turns=CONV)
        # A SHORTER re-capture: ids at/beyond the new window are stale.
        _capture_with_record_armed(sdk, monkeypatch, turns=CONV[:1])

        removals = [r for r in _records(log_path)
                    if r.get("type") == "EntityMutated"
                    and r.get("op") == "delete"
                    and r.get("id") in EXPECTED_TURN_IDS]
        assert [r["id"] for r in removals] == EXPECTED_TURN_IDS[1:], (
            "the sweep journalled the wrong population (or nothing) — a "
            "shorter re-capture must record exactly the turns it dropped")
        for record in removals:
            assert "cache_tier" not in record, (
                "the stale-turn sweep carries a tier, which makes its record "
                "effect the PURGE's demote — that keeps serving a turn the "
                "session no longer holds")
            statements = rp.mutations_for(record, org_id="o", graph_id="g")
            assert [s.sql.split()[0] for s in statements] == ["DELETE", "DELETE"], (
                f"the sweep's removal must delete the row, not demote it: "
                f"{[s.sql for s in statements]}")
    finally:
        sdk.close()


def test_a_MIXED_generation_session_keeps_a_LEGACY_creation_with_NO_retraction(
        tmp_path, monkeypatch):
    """The asymmetry `sdk._write_capture_turns` ACCEPTS, pinned instead of
    described — the MIXED-generation case neither neighbour covers.

    One session can hold turns captured by two generations of this code, and the
    change feed is the surface that remembers which:

    * a turn captured **after** this change journals the CARRIER, which is NOT in
      `_GRAPH_EVENT_TYPES`, so it is announced nowhere (pinned by
      `test_an_armed_capture_writes_NO_graph_event_for_its_turns`);
    * a turn captured **before** it journalled its own `PointAdded`, which IS in
      `_GRAPH_EVENT_TYPES` — the turn WAS announced on this lane, and its removal
      (the stale sweep, gated on `not _record_holds_turns`) is now silent.

    So a legacy turn keeps a creation in the feed with no matching retraction,
    and a subscriber keeps a point the sweep dropped. That is the ACCEPTED
    direction — ⛔ re-announcing turns on this lane to "repair" it would re-
    advertise entities the lane deliberately refuses to store, and would redden
    the post-change leg — and this leg is what reds if a future lane instead
    re-adds the `PointRetracted` emission without re-deriving that trade.

    Non-vacuity: flip `sdk._write_capture_turns`'s gate to `if True:` (i.e.
    restore the pre-change emission on this lane) and the `PointRetracted`
    assertion below reds. The LEGACY emission is the pre-fix producer's own
    record, verbatim (`{"id", "kind", "content_hash"}` + `point=` +
    `contains_session=`), so the feed this leg starts from is the one the
    pre-fix code left behind.
    """
    log_path = _journal(tmp_path)
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        _capture_with_record_armed(sdk, monkeypatch)  # the post-change capture
        proj = sdk._get_proj()

        def _turn_events() -> list[tuple]:
            rows = proj.g.query(
                "MATCH (e:GraphEvent) WHERE e.payload CONTAINS $sid "
                "RETURN e.type, e.payload", params={"sid": SESSION_ID}
            ).result_set
            return [row for row in rows
                    if any(tid in str(row[1]) for tid in EXPECTED_TURN_IDS)]

        # The armed capture announced nothing — re-checked here because the
        # legacy emission below is only meaningful against an otherwise empty
        # feed, and because it makes THIS leg's start state independent of the
        # sibling leg continuing to hold that property.
        assert _turn_events() == [], _turn_events()

        # ── the PRE-CHANGE producer, replayed verbatim ──────────────────────
        # `_t2` is chosen because the shorter re-capture below SWEEPS it: the
        # sweep is the removal whose retraction the gate no longer emits.
        legacy_id = EXPECTED_TURN_IDS[2]
        sdk._emit_event(
            "PointAdded",
            {"id": legacy_id, "kind": "event", "content_hash": "legacy"},
            point={"id": legacy_id, "content": CONV[2]["content"],
                   "pointKind": "event"},
            contains_session=SESSION_ID,
        )
        created = _turn_events()
        assert [row[0] for row in created] == ["PointAdded"], created
        assert legacy_id in str(created[0][1])

        # ── a SHORTER re-capture: ids at/beyond the new window are stale ────
        _capture_with_record_armed(sdk, monkeypatch, turns=CONV[:1])

        # THE PIN — the feed is UNCHANGED: the sweep retracted nothing, so the
        # legacy creation still stands and the swept turn keeps no removal row.
        assert _turn_events() == created, (
            "the record-held lane emitted a change-feed event about a turn: the "
            "sweep's `PointRetracted` is gated on `not _record_holds_turns`, and "
            f"this lane stores no turn node to retract. Got {_turn_events()}")
        assert not [row for row in _turn_events() if row[0] == "PointRetracted"]

        # `poll_events` is what a SUBSCRIBER sees, so the asymmetry is stated
        # where it bites: the creation is still served for a turn the session no
        # longer holds, and no removal is. Pinned, not repaired.
        served = [e for e in sdk.poll_events()["events"]
                  if any(tid in str(e) for tid in EXPECTED_TURN_IDS)]
        assert [e["type"] for e in served] == ["PointAdded"], (
            f"poll_events serves something other than the legacy creation: {served}")
        assert legacy_id in str(served[0])

        # ── while the REMOVAL itself is journalled and replayable ───────────
        # The asymmetry is about the FEED only: the sweep's record half is a
        # real row DELETE (tier-less — a demote would keep serving a turn the
        # session no longer holds), so a rebuild cannot resurrect `_t2` even
        # though the feed still advertises it.
        removed = [r for r in _records(log_path)
                   if r.get("type") == "EntityMutated"
                   and r.get("op") == "delete"
                   and r.get("id") in EXPECTED_TURN_IDS]
        assert sorted(r["id"] for r in removed) == EXPECTED_TURN_IDS[1:], (
            f"the sweep journalled the wrong population: {removed}")
        assert all("cache_tier" not in r for r in removed), removed
    finally:
        sdk.close()


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
