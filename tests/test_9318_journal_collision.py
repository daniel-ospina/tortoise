"""#9318 — a position holding content this write would not produce is refused LOUDLY.

THE DEFECT THIS PINS. `public.journal_event` is keyed `(org_id, graph_id, seq)`
and the schema calls that key *"the idempotency key: applying a position twice
is a no-op"*. The ordinal, though, is a fact of ONE JOURNAL FILE
(`EventLog.append`, #9190), while the key is per `(org, graph)`. A tenant
holding two durable journals numbers each from 1, so the second journal's
position rows land on the first's — and `ON CONFLICT … DO NOTHING` absorbed them
in silence: the live write reported `applied`, the wiring gate printed `WIRED`,
no counter moved, and the surviving row kept the OTHER journal's
`event_type`/`payload` while the layer rows underneath belonged to a different
record. That is a mis-index, not merely a gap.

WHAT THE FIX IS, AND WHAT IT IS NOT.

* It is a **detector at ONE call site** — `record_projection.replay()`'s
  statement loop, which every layer-1 write already passes through (the live
  door, a full replay, the reconciler, the backfill tools). When the position
  row was absorbed, the guard asks the DATABASE whether the surviving row's
  content differs (`IS DISTINCT FROM`, in Postgres's own jsonb equality).
* It is **not** a schema change. No column, no table, no allocator: journal
  identity in the key is the REPAIR, and it is `#12894`'s decision, deliberately
  not taken here (two designs of one ordinal is the duplication this lane
  refused to add). `_journal_row`'s statement is therefore byte-identical, and
  `tests/test_record_writer_seam.py::test_the_default_position_reproduces_the_previous_plan`
  remains a true pin.
* It is **not** a violation of idempotency. A genuine replay — same position,
  same content — stays a SILENT no-op: no exception, no counter, no log line.
  That polarity is asserted in BOTH directions here, and the DB-free arms assert
  it too, because a guard that turned every replay into an error would be a
  worse defect than the one being fixed, and the DB legs skip where no Postgres
  is available.

THE FALSIFIERS. `test_two_durable_journals_at_one_ordinal_are_refused_not_absorbed`
reds on the pre-#9318 tree with `outcome=APPLIED` for the second journal and
`journal_event` holding ONE row whose payload names journal A. Its sibling
`test_a_genuine_replay_of_one_journal_stays_a_silent_no_op` reds on an
over-eager guard — one that fired on `rowcount == 0` alone.

THE DATABASE. Set `TEST_RECORD_DB_URI` to a throwaway Postgres carrying the
record migrations (the `test-record-postgres` job does it, and asserts the legs
RAN rather than skipped, #9551). Every write goes through the REAL live door
(`write_live_record`) as the migration's own non-bypassing role
(`tortoise_record_app`) — `replay()` refuses a superuser/owner/BYPASSRLS
connection by design (#9554), so an armed leg connecting as the provisioning
superuser would red on the role gate instead of testing anything.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from tortoise import monitoring
from tortoise import record_projection as rp
from tortoise import record_writer as rw
from tortoise.log import EventLog

#: The throwaway-Postgres env var the record legs already use (#9551).
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: One record the fold knows, with a payload small enough to recognise by value.
RECORD_A: dict[str, Any] = {
    "type": "ObjectRegistered", "ts": "2026-11-15T00:00:00Z", "id": "obj-a",
    "name": "corpus A document", "objectKind": "document",
}
#: The SAME type and payload SHAPE, different content — so a collision cannot be
#: told from a replay by the record type alone (a guard keyed on `event_type`
#: would read this as identical and stay silent, which is why the assertion also
#: covers the payload).
RECORD_B: dict[str, Any] = {
    "type": "ObjectRegistered", "ts": "2026-11-15T00:00:01Z", "id": "obj-b",
    "name": "corpus B document", "objectKind": "document",
}

ORG, GRAPH = "org-9318", "graph-9318"


# ════════════════════════════════════════════════════════════════════════════
# A DB-API fake that can answer the collision question — and one that cannot
# ════════════════════════════════════════════════════════════════════════════


class FakeCursor:
    """The tenant prologue's answers, plus whatever `rowcount` the test sets.

    `rowcount` is DELIBERATELY absent here: that is the shape of every recording
    fake in this suite, and it is the shape the guard must treat as "cannot
    tell" (see `rp._affected_rows`).
    """

    def __init__(self, conn: FakeConnection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((sql, None if params is None else list(params)))
        self._conn.last_sql = sql
        if sql == rp.TENANT_GUC_SQL:
            self._conn.guc_set = params[0] if params else None
        if sql == rp._JOURNAL_CONFLICT_CONTENT_SQL:
            self._conn.conflict_reads += 1

    def fetchone(self) -> Any:
        if self._conn.last_sql == rp.TENANT_SCOPE_SQL:
            return (self._conn.guc_set, False)
        if self._conn.last_sql == rp._JOURNAL_CONFLICT_CONTENT_SQL:
            return self._conn.stored
        return (self._conn.guc_set,)


class RowcountCursor(FakeCursor):
    """A cursor that CAN report the affected-row count — what a real driver does."""

    def __init__(self, conn: FakeConnection, rowcount: int) -> None:
        super().__init__(conn)
        self._rowcount = rowcount

    @property
    def rowcount(self) -> int:
        return self._rowcount


class FakeConnection:
    def __init__(self, stored: Any = None) -> None:
        self.executed: list[tuple[str, list[Any] | None]] = []
        self.last_sql: str | None = None
        self.guc_set: Any = None
        self.stored = stored
        self.conflict_reads = 0
        self.commits = 0
        self.closed = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        self.closed += 1

    def journal_statements(self) -> list[tuple[str, list[Any] | None]]:
        return [(sql, params) for sql, params in self.executed
                if sql.startswith(rp._JOURNAL_INSERT_PREFIX)]


class Blind(FakeConnection):
    """No `rowcount` at all — the recording fakes the rest of the suite uses."""


class Absorbed(FakeConnection):
    """Every position insert is absorbed (`rowcount == 0`), as on a conflict."""

    def cursor(self) -> RowcountCursor:
        return RowcountCursor(self, 0)


class Landed(FakeConnection):
    def cursor(self) -> RowcountCursor:
        return RowcountCursor(self, 1)


def _payload_of(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in rp.ENVELOPE_KEYS}


def _stored(event_type: Any, payload: Any, *, same_type: bool = True,
            same_capture: bool = False) -> tuple[Any, Any, bool, bool]:
    """The conflict query's four projected columns, as Postgres would return them.

    ⛔ THE FAKE CANNOT EVALUATE THE SQL (review P2-5), so these legs pin the
    guard's DECISION, not the predicate: the two DB acceptance legs are what
    exercise the `WHERE`. Composing the row explicitly is also what keeps the
    drift split (#12904 P0-1) visible in every test — `same_type` and
    `same_capture` are the two facts the verdict turns on, so a leg that wants a
    COLLISION has to say which of them differed, and one that wants DRIFT says
    both match.

    `same_capture=False` is the default because a second durable journal's record
    brings its OWN capture time — that is what makes the position a collision
    rather than a re-derivation of the record already there.
    """
    return (event_type, payload, same_type, same_capture)


# ════════════════════════════════════════════════════════════════════════════
# 1. THE GUARD'S ONE CALL SITE, WITH NO DATABASE
# ════════════════════════════════════════════════════════════════════════════


def test_a_cursor_that_cannot_report_rowcount_is_never_asked() -> None:
    """A fake that reports no count keeps the pre-#9318 behaviour, silently.

    This is the whole reason the guard reads `rowcount` instead of rewriting the
    INSERT with a `RETURNING`: the plan the seam, the PGlite harness and every
    DB-free test materialize is unchanged, and a cursor that cannot answer never
    produces a refusal — the guard may only fire on POSITIVE evidence.
    """
    conn = Blind()
    report = rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    assert report.events == 1
    assert len(conn.journal_statements()) == 1
    assert conn.conflict_reads == 0, (
        "a cursor with no rowcount was asked the collision question — the "
        "guard must never invent a verdict from an unreadable cursor")


def test_a_landed_position_row_does_not_ask_the_collision_question() -> None:
    """`rowcount == 1` is the ordinary live write: nothing to decide."""
    conn = Landed()
    rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)
    assert conn.conflict_reads == 0


def test_an_absorbed_position_with_the_same_content_stays_a_silent_no_op() -> None:
    """THE POLARITY THAT MATTERS: a REPLAY must not become an error (#9318).

    The stub answers the conflict query the way Postgres answers it for a row
    that already holds THIS record — content equal, so the query's
    `IS DISTINCT FROM` predicate returns no row. No exception, no counter.
    """
    conn = Absorbed(stored=None)
    before = dict(monitoring.record_write_failure_counts())
    report = rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    assert report.events == 1
    assert conn.conflict_reads == 1, (
        "the guard did not ask the collision question at all — the polarity leg "
        "would then pass by never exercising the code under test")
    assert dict(monitoring.record_write_failure_counts()) == before


def test_an_absorbed_position_with_different_content_raises_and_names_both() -> None:
    """THE COLLISION: same position, different content ⇒ refuse, naming both.

    THIS SCENARIO has two DIFFERENT records (A and B), but the message contract
    is not "two records": the class forbids asserting a cause, because the other
    reachable case is ONE record whose payload was re-derived since that row was
    written. So the message must identify BOTH SIDES without quoting their
    CONTENT: the record's own failure lines withhold payload text
    (`_CONTENT_COLUMNS`), and a payload can carry the tenant's words. An ID is
    not content — the failure lines already print ids — but an id is a
    COORDINATE the record shares with its neighbours, so it does not by itself
    tell one record from two (see the class docstring); what the message owes the
    operator is the identity it CAN name, with the title withheld.
    """
    conn = Absorbed(stored=_stored("ObjectRegistered", _payload_of(RECORD_A)))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_B], conn, org_id=ORG, graph_id=GRAPH)

    message = str(excinfo.value)
    assert "position 1" in message
    assert ORG in message and GRAPH in message
    assert "sha256:" in message
    # Two DIFFERENT digests: the payloads differ, so the digests disagree.
    assert excinfo.value.payload_digest != excinfo.value.stored_payload_digest
    # …here both sides are NAMED by identity (this scenario has two records), …
    assert excinfo.value.identity_state == "disagrees"
    assert "obj-a" in message and "obj-b" in message
    # …and neither payload's CONTENT (a document title) appears.
    for record in (RECORD_A, RECORD_B):
        assert record["name"] not in message
    assert excinfo.value.seq == 1
    assert excinfo.value.event_type == "ObjectRegistered"
    assert excinfo.value.stored_event_type == "ObjectRegistered"


def test_a_re_derived_record_replays_SILENTLY() -> None:
    """DRIFT is not a collision: the same record, READ AS re-derived (#12904 P0-1).

    ⚠️ "READ AS" — that phrase is the point of this leg, not decoration:

    The compared payload is a snapshot of a DERIVATION (`_journal_row` strips
    `ENVELOPE_KEYS`, an actively argued surface), so a change to that derivation
    makes an old row's content differ from what this tree would write for the
    SAME record at the SAME position. `recorded_at` is stamped at CAPTURE, so a
    re-derivation preserves it — and identical type + identical capture is the
    DRIFT READING, which the verdict ADMITS. It is a reading, not evidence of
    identity: it says only that two writes named one instant, and `ts` is a
    per-record value the emission path supplies, not one the position derives —
    which is why the mirror shape (two DIFFERENT records at one
    capture instant) is admitted too — that leg is
    `test_a_same_capture_instant_pair_is_ADMITTED_not_detected`, and the class
    docstring's mirror residual names the cost. The verdict here is a SILENT
    no-op: no exception, no counter, no log line.

    ⛔ THIS LEG REPLACES ONE THAT PINNED THE OPPOSITE (#12904 P0-1).
    `test_a_re_derived_record_is_not_reportable_as_two_records` asserted the
    REFUSAL, with a message that named both causes; the refusal was the defect.
    It made every position written before a payload-derivation change
    un-replayable — the record's own repair story (`replay` heals the record)
    silently version-pinned to the tree that wrote the row — which inverts the
    schema's *"applying a position twice is a no-op"* and is worse than the
    silence the guard replaced. Its companion (the message never names a single
    cause) survives on the legs that still DO refuse.
    """
    re_derived = dict(_payload_of(RECORD_A))
    re_derived["name"] = "corpus A document (re-derived)"
    # Same type, same `recorded_at`: the payload moved, the CAPTURE did not.
    conn = Absorbed(stored=_stored("ObjectRegistered", re_derived,
                                  same_capture=True))
    before = dict(monitoring.record_write_failure_counts())

    report = rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    assert report.events == 1
    assert conn.conflict_reads == 1, (
        "the guard did not ask the collision question — this leg would then pass "
        "by never exercising the code under test")
    assert dict(monitoring.record_write_failure_counts()) == before, (
        "a re-derivation was COUNTED as a refusal — the record's own repair "
        "story is then version-pinned to the tree that wrote the row")


def test_a_same_capture_instant_pair_is_ADMITTED_not_detected() -> None:
    """THE DECLARED MIRROR RESIDUAL: a different record at one capture instant.

    ⛔ THIS LEG PINS AN ADMISSION, NOT A DETECTION. Read this docstring before
    "fixing" it to expect a refusal: the drift arm (#12904 P0-1) returns silently
    on `same_type and same_capture`, and identical `recorded_at` is NOT evidence
    of identity — it says only that two writes named one instant, and `ts` is a
    per-record value the EMISSION path supplies (`_journal_row` binds
    `record.get("ts")`; `_emit_event` accepts a `recorded_ts`, minting
    `now_iso()` only when none is given), so nothing ties the instant to the
    record's identity, and TWO DIFFERENT records captured at the same instant
    are absorbed: `RECORD_A` against a probe
    differing in `id` and `name`, `same_type` and `same_capture` both true.

    `main` (post-#12904) REFUSED this pair (payload differs ⇒ collision), so this
    is the one shape the drift split widens the silent window by. It is admitted
    rather than closed HERE because closing it — refusing when
    `_identity_evidence` answers `disagrees` — is a SEMANTICS change to #12904's
    verdict, which belongs to that decision's owner; `#12894`'s journal identity
    in the key retires it outright.

    The leg is NOT vacuous: it asserts the guard HOLDS the refuting evidence and
    discards it, so a tree that starts refusing reds here as a change to a
    stated decision, and one that stops asking the collision question reds on
    `conflict_reads`.
    """
    probe = dict(RECORD_A, id="obj-b", name="corpus B document")
    assert probe != RECORD_A, "the two records must actually differ"
    # The guard HOLDS the evidence it discards — measured on the payloads the
    # absorbed row and this write would carry, so the leg cannot pass on a shape
    # where the identities merely fail to contradict.
    state, detail = rp._identity_evidence(_payload_of(probe),
                                          _payload_of(RECORD_A))
    assert state == "disagrees", (
        f"the identity verdict no longer contradicts at equal capture "
        f"({state}: {detail}) — this leg's premise moved, so re-measure the "
        f"mirror residual rather than deleting the leg")
    conn = Absorbed(stored=_stored("ObjectRegistered", _payload_of(RECORD_A),
                                  same_capture=True))
    before = dict(monitoring.record_write_failure_counts())

    report = rp.replay([probe], conn, org_id=ORG, graph_id=GRAPH)

    assert report.events == 1
    assert conn.conflict_reads == 1, (
        "the guard did not ask the collision question — this leg would then pass "
        "by never exercising the code under test")
    assert dict(monitoring.record_write_failure_counts()) == before, (
        "an equal-capture pair was COUNTED — that is the drift arm refusing, "
        "which re-opens #12904 P0-1; if the refusal is deliberate, the residual "
        "in `JournalPositionCollision` and its owner decision move FIRST")


def test_two_payloads_with_no_comparable_identity_say_so() -> None:
    """`none` is an answer, not a guess — a difference still collides."""
    # NEITHER side carries an identity key, so `none` is the honest answer
    # (`one_sided` is the answer when only one of them does — asserted below).
    # `IngestStarted` is a declared `NoRecordEffect` type (audit-only run
    # marker), so its record carries no node identity at all — the one shape in
    # which NO identity is comparable on both sides.
    anonymous = {"type": "IngestStarted", "ts": "2026-11-15T00:00:00Z",
                 "run": 1}
    conn = Absorbed(stored=_stored("IngestStarted", {"run": 2}))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([anonymous], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "none"
    assert "no identity key is comparable between them" in str(excinfo.value)


def test_a_shared_NODE_id_with_two_record_types_is_not_one_record() -> None:
    """A shared id is a NODE id, so two record types are two records (#9318 R2).

    Cycle 2 of review found the first cut of the identity verdict returned
    `same` on any shared id: `ObjectRegistered` vs `EntityMutated` about one
    object came back as "the SAME record", which is the cycle-1 defect mirrored
    — a cause asserted that the evidence cannot support. No state names a cause
    now; the message names both causes and the reading says only what the ids
    do, which here is that the write's payload carries no identity at all.
    """
    conn = Absorbed(stored=_stored("EntityMutated", _payload_of(RECORD_A),
                                   same_type=False))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    # Both sides name the SAME object id, so the ids agree — and the message
    # still names both causes, because agreement on a node coordinate is not
    # agreement on a record.
    assert excinfo.value.identity_state == "agrees"
    message = str(excinfo.value)
    assert "they share id=obj-a" in message
    assert "second durable journal" in message


def test_a_NESTED_payload_identity_is_read() -> None:
    """The dominant shape is nested, and a top-level-only read is blind to it.

    `PointAdded` carries its snapshot under `point` (`sdk._rec_point_id` reads
    `rec["point"]["id"]`, and `consistency.py` normalises `{"type": …,
    "point": {…}}` for exactly this reason). Reading the top level alone reported
    "no identity comparable" for every `PointAdded` — i.e. exactly where the
    re-derivation residual lives — while both payloads carried `point.id`.
    """
    nested = {"type": "PointAdded", "ts": "2026-11-15T00:00:00Z",
              "point": {"id": "p1", "content": "a claim"}}
    stored_nested = {"point": {"id": "p1", "content": "a claim, re-derived"}}
    conn = Absorbed(stored=_stored("PointAdded", stored_nested))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([nested], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "agrees"
    assert "point.id=p1" in str(excinfo.value), (
        "the identity was not read from the nested snapshot — a PointAdded's id "
        "lives under `point`, and reporting 'none' for it tells an operator "
        "the payloads carry no identity when both do")


def test_the_SAME_type_at_a_shared_node_id_does_not_claim_one_record() -> None:
    """A record TYPE is a class, not an identity (#9318 review cycle 3).

    Cycle 2 fixed the cross-type half — `ObjectRegistered` vs `EntityMutated` —
    but left `same` for the same-type case, and the same-type case is the COMMON
    one: two journals each holding a `PointRevised` of `p1`, or two
    `EntityMutated` of one object. A shared `id` is a NODE id (`PointRevised` is
    emitted with the point's own id), so id + type still does not identify a
    record, and the message asserted "the SAME record" while naming no collision
    origin at all — the cycle-1 defect in the opposite direction.
    """
    payload = {"type": "PointRevised", "id": "p1", "content": "v2"}
    stored = {"id": "p1", "content": "v2 (from the other journal)"}
    conn = Absorbed(stored=_stored("PointRevised", stored))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([payload], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "agrees"
    message = str(excinfo.value)
    # The reading is a fact about the ids, NOT a verdict about the records.
    assert "they share id=p1" in message
    assert "SAME record" not in message
    assert "DIFFERENT records" not in message
    assert "two records about one node" not in message


def test_an_agreeing_coarse_key_cannot_suppress_a_disagreeing_one() -> None:
    """Two points of one capture session are two records (#9318 review cycle 3).

    The first cut returned on the FIRST identity key both sides carried, in
    `_IDENTITY_KEYS` order — so an agreeing `session_id` decided the verdict
    before a DISAGREEING `id` was ever read. Caller-set `session_id` rides the
    point snapshot into `PointAdded`/`PointRevised`, so a genuine two-journal
    collision between two points of one session reported the session coordinate
    while the proof of two nodes sat in the same payload pair.
    """
    ours = {"type": "PointAdded",
            "point": {"id": "p-a", "session_id": "s1", "content": "a"}}
    stored = {"point": {"id": "p-b", "session_id": "s1", "content": "b"}}
    conn = Absorbed(stored=_stored("PointAdded", stored))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([ours], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "disagrees"
    message = str(excinfo.value)
    assert "point.id: p-a vs p-b" in message, (
        "the disagreeing node id was not the label — a coarse agreeing key "
        "decided the verdict")
    assert "session_id" not in message


def test_an_identity_read_from_a_SUBDICT_matches_a_TOP_LEVEL_one() -> None:
    """The same id at two levels is one coordinate (#9318 review cycle 3).

    `PointAdded`/`PointRevised` carry the snapshot under `point` while other
    carriers put the id at the top level, so the two sides of a real comparison
    can arrive at DIFFERENT levels. Matching on the printed label instead of the
    key would call them incomparable and fall back to `none` — losing the one
    signal the class exists to report.
    """
    state, detail = rp._identity_evidence({"point": {"id": "p1"}}, {"id": "p1"})

    assert state == "agrees"
    assert detail == "point.id=p1"


def test_a_non_scalar_identity_value_is_not_rendered_into_the_log() -> None:
    """An identity VALUE is caller data and may be a whole document (#9318 R3).

    `_identity_pairs` used to accept any non-None value and `_identity_evidence`
    rendered `str(value)`, so `{"id": {"title": "Q3 pricing memo"}}` put a
    document title on a log line the class docstring promises is content-free.
    `record_writer._record_identity` accepts `str` only; this accepts `str`/`int`
    and drops everything else.
    """
    state, detail = rp._identity_evidence({"id": {"title": "Q3 pricing memo"}}, {"id": "obj-a"})

    assert state == "one_sided"
    assert "Q3 pricing memo" not in (detail or "")


def test_the_surviving_row_may_be_the_only_side_naming_an_identity() -> None:
    """The mirror of `one_sided`: a field REMOVED since the row was written."""
    state, detail = rp._identity_evidence({"objectKind": "document"}, {"id": "obj-a"})

    assert state == "one_sided"
    assert detail == "only the surviving row names id=obj-a"


def test_a_number_typed_id_is_compared_by_type_not_by_stringification() -> None:
    """`{"id": 1}` and `{"id": "1"}` are different ids (#9318 review cycle 3).

    A jsonb round-trip preserves numeric ids, so comparing `str(value)` equality
    would call a numeric id and its string form the same record — the same
    over-claim as the coordinate states, one level down.
    """
    state, _detail = rp._identity_evidence({"id": "1"}, {"id": 1})

    assert state == "disagrees"


def test_a_WITHHELD_section_is_never_read_for_an_identity() -> None:
    """The nested walk may visit SNAPSHOTS, not every dict (#9318 review cycle 4).

    The first nested cut iterated every mapping in the payload, `props`
    included — and `props` is a withheld column. `{"props": {"id": "Q3 pricing
    memo — confidential"}}` was printed verbatim into the diagnosis, and the
    class docstring promises it "quotes no payload text". The walk is now bounded
    to the fold's own entity-snapshot sections; the pin below keeps a content
    column from being added to that set.
    """
    assert rp._identity_pairs({"props": {"id": "secret"}}) == []
    assert rp._identity_pairs({"aboutObject": {"id": "obj-9"}}) == []
    assert frozenset(rp._ENTITY_MATCH_COLUMNS) == rp._IDENTITY_SECTIONS, (
        "the snapshot sections are the fold's own table — a second list would "
        "let the reader and the projection disagree about what a snapshot is")
    # `props` is the WITHHELD tail, and the one section name that is content by
    # definition. (`object`/`subject` are also COLUMN names in `_CONTENT_COLUMNS`,
    # but as payload sections they are node snapshots the fold resolves ids
    # from — the withheld thing is a section's free-text, not its `id`.)
    assert "props" not in rp._IDENTITY_SECTIONS


def test_an_instruction_like_subdict_does_not_move_the_verdict() -> None:
    """A sibling sub-dict is not a second identity (#9318 review cycle 4).

    Pairing was by KEY alone, so `{"id": "p1", "aboutObject": {"id": "obj-9"}}`
    compared our top-level id against a nested one and reported `different` — the
    cycle-1 false certainty back again, with a counterpart the message had not
    actually compared. Only declared snapshot sections are read now, so the
    shared top-level id decides.
    """
    state, detail = rp._identity_evidence({"id": "p1", "aboutObject": {"id": "obj-9"}}, {"id": "p1"})

    assert state == "agrees"
    assert detail == "id=p1"


def test_a_key_present_in_TWO_scopes_is_not_compared_across_them() -> None:
    """Two ids on one side, one on the other: nothing is comparable (#9318 R4).

    With the key present twice on this side, a key-only fallback would pick one
    of them arbitrarily and print a counterpart it never compared. The pair is
    skipped, and the same-scope agreement still decides.
    """
    state, detail = rp._identity_evidence({"id": "p1", "point": {"id": "p9"}}, {"id": "p1"})

    assert state == "agrees"
    assert detail == "id=p1"


def test_a_BLANK_identity_is_not_an_agreeing_coordinate() -> None:
    """`""` is a defaulted field, not evidence (#9318 review cycle 4).

    The previous cut admitted the empty string, so two blanks "agreed" (`same`
    from no evidence) and a blank against a real id "disagreed" — the collision
    claim, from the re-derivation signature that `one_sided` exists to report.
    `record_writer._record_identity` requires a non-empty string for the same
    reason.
    """
    assert rp._identity_evidence({"id": ""}, {"id": ""}) == ("none", None)
    state, detail = rp._identity_evidence({"id": ""}, {"id": "obj-a"})
    assert state == "one_sided"
    assert detail == "only the surviving row names id=obj-a"


def test_the_identity_values_are_BOUNDED_like_every_other_log_value() -> None:
    """An `id` is caller-chosen, so it may not size the log line (#9318 R2).

    `add_object(..., id=…)` / a source keyed by its URL makes `id` arbitrary
    caller data. The record's own `_record_identity` bounds the same datum at
    `_ATTRIBUTION_VALUE_LIMIT`; a second half of the same log line that did not
    would be the shape the #10648 review recorded for this exact line.
    """
    huge = "x" * 300
    state, detail = rp._identity_evidence({"id": huge}, {"id": "b"})

    assert state == "disagrees"
    assert huge not in (detail or "")
    assert "chars)" in (detail or ""), (
        "the identity value was not truncated with the same bound marker the "
        "record's own identity line uses")


def test_the_identity_keys_are_ids_only_and_a_subset_of_the_writers() -> None:
    """A second list must not drift into printing content (#9318 review note).

    `_IDENTITY_KEYS` is a deliberate SUBSET of
    `record_writer._RECORD_IDENTITY_KEYS` (that module applies it to the record
    it holds; this one applies it to a durable JSON payload). Neither may name a
    content-bearing column — a source's `name` is its document title — and the
    writer's list is itself pinned as a subset of `_CONTENT_COLUMNS` by
    `test_record_searchkeys_10620`. Pinned here because a key added to THIS list
    alone would have nothing red.
    """
    assert set(rp._IDENTITY_KEYS) <= set(rw._RECORD_IDENTITY_KEYS)
    assert not set(rp._IDENTITY_KEYS) & rp._CONTENT_COLUMNS, (
        f"identity key(s) {sorted(set(rp._IDENTITY_KEYS) & rp._CONTENT_COLUMNS)} "
        f"are content columns — they would be printed verbatim into the log")
    # `event_id` is stripped by ENVELOPE_KEYS before the durable payload is
    # built, so it can never appear on either side of the comparison.
    assert "event_id" not in rp._IDENTITY_KEYS
    assert "event_id" in rp.ENVELOPE_KEYS


def test_a_TS_only_difference_is_now_REFUSED() -> None:
    """P0-2 (#12904): the shape this class used to absorb SILENTLY.

    `_journal_row` strips `ENVELOPE_KEYS`, so two records that differ ONLY in
    `ts`/`event_id` bind identical `event_type`/`payload` — the reachable #9318
    shape, two durable journals ingesting the SAME document apart only in CAPTURE
    TIME. Until the `recorded_at` arm existed, the predicate matched no row and
    the second journal was absorbed as though it were a replay.

    The two assertions below ARE the boundary: the durable content is
    byte-identical (so no content comparison can see the difference) and the
    capture time is the only thing left that distinguishes them. `event_id` is
    still unrecoverable — that is the restated residual, pinned by the sibling
    leg.
    """
    first = dict(RECORD_A, ts="2026-11-15T00:00:00Z", event_id="evt-first")
    second = dict(RECORD_A, ts="2026-11-16T09:30:00Z", event_id="evt-second")
    assert first != second, (
        "the two records must actually differ, or this leg is vacuous")

    row_first = rp._journal_row(first, 1, ORG, GRAPH)
    row_second = rp._journal_row(second, 1, ORG, GRAPH)
    assert row_first.params[:5] == row_second.params[:5], (
        "the durable content of two ts-only-different records is no longer "
        "identical, so this leg's premise has moved: it exists to prove the "
        "CAPTURE arm is what catches the pair")
    assert row_first.params[5] != row_second.params[5], (
        "`ts` is the only field left once the envelope is stripped, so it is "
        "what must carry the difference")

    # The surviving row is journal A's, captured at A's time. The stub hands the
    # guard the row Postgres WOULD return: `same_type` true, `same_capture` false.
    conn = Absorbed(stored=_stored("ObjectRegistered", _payload_of(first),
                                  same_capture=False))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([second], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.capture_state == "differs"
    message = str(excinfo.value)
    assert "CAPTURED at different times" in message, (
        "the refusal does not say WHICH discriminator fired — an operator is "
        "left to guess between a second journal and a moved capture time")
    # …and the diagnosis still names both possibilities rather than picking one.
    assert "a DIFFERENT record claimed this position" in message
    assert "no record id" in message
    # ⛔ AND IT DOES NOT CLAIM A CONTENT DIFFERENCE THIS PAIR DOES NOT HAVE
    # (review cycle 7). The diagnosis is content-shaped, so an earlier cut said
    # "the surviving row's content is not what this write would produce" on the
    # very line that prints two IDENTICAL payload digests. Pin the true clause:
    assert "IDENTICAL to what this write would produce" in message, (
        "the refusal claims a CONTENT difference for a pair whose payload "
        "digests are byte-identical — the capture arm is the one shape where "
        "only `recorded_at` differs")
    assert "not what this write would produce" not in message.replace(
        "IDENTICAL to what this write would produce", ""), (
        "the refusal still carries the content-difference sentence")
    # …and, as ever, no payload content.
    assert RECORD_A["name"] not in message


def test_an_event_id_only_difference_is_still_invisible() -> None:
    """RESIDUAL, restated (#12904 P0-2): only `event_id` is unrecoverable.

    Two records differing ONLY in `event_id` bind identical content AND
    identical capture, so no comparison of the durable row can separate them —
    `event_id` is stripped by `ENVELOPE_KEYS` and the row has no column for it.
    This is the residual that is LEFT after the `recorded_at` arm; `ts` is not in
    it, because `ts` IS carried (`recorded_at`) and is now compared.

    ⛔ THE TWO REDS THIS LEG CAN PRODUCE, and WHICH assertion produces each
    (review P2-6, second round: the first cut attached them to the wrong ones):
      * the ARITY assertion reds on a bind that was ADDED OR REMOVED — that is
        either #12894's journal identity (the pin is retired WITH the guard) or
        some other reshape of the bind list (re-measure);
      * the EQUALITY assertion reds on a PAYLOAD DERIVATION MOVE — `ENVELOPE_KEYS`
        or a record type's field set changed, so the boundary must be re-measured.
    A key change cannot red the equality assertion (both records derive the same
    bind) and a payload change cannot red the arity one (#9284: `_journal_row`
    still binds 6), so each message names only its own red.
    """
    first = dict(RECORD_A, ts="2026-11-15T00:00:00Z", event_id="evt-first")
    second = dict(RECORD_A, ts="2026-11-15T00:00:00Z", event_id="evt-second")
    assert first != second, (
        "the two records must actually differ, or this leg is vacuous")

    row_first = rp._journal_row(first, 1, ORG, GRAPH)
    row_second = rp._journal_row(second, 1, ORG, GRAPH)
    # ⛔ THE BIND SHAPE IS PINNED BY ARITY, NOT BY INDEX (#12904 review). A
    # `journal_id` APPENDED to the key leaves `params[:5]` byte-identical, so the
    # COUNT is the thing that sees the key change.
    assert len(row_first.params) == 6, (
        f"`_journal_row` now binds {len(row_first.params)} parameters "
        f"({row_first.params}) where this boundary was measured at 6 "
        f"(org, graph, seq, event_type, payload, ts). A COUNT can only move when "
        f"a bind was ADDED or REMOVED: if the 7th is journal identity then the "
        f"KEY GAINED JOURNAL IDENTITY (#12894) and this pin is DELETED with the "
        f"detector, not kept green; if instead a bind was appended or dropped "
        f"for any other reason, the guard's POSITIONAL bind reading must be "
        f"re-measured. (A payload-derivation move cannot reach this assertion — "
        f"it moves the content below, not the count.)")
    assert row_first.params == row_second.params, (
        "the durable row content of two event_id-only-different records is no "
        "longer identical — the PAYLOAD DERIVATION MOVED (`ENVELOPE_KEYS` or a "
        "record type's field set changed), so this boundary must be "
        "re-measured; `event_id` alone cannot cause it, and a KEY change cannot "
        "either (both records derive the same bind, so the arity assertion "
        "above is what reds for #12894)")

    # Same bound parameters ⇒ `IS DISTINCT FROM` matches no row ⇒ the stub
    # returns no stored row ⇒ silent no-op, no counter, no exception.
    conn = Absorbed(stored=None)
    before = dict(monitoring.record_write_failure_counts())
    report = rp.replay([second], conn, org_id=ORG, graph_id=GRAPH)
    assert report.events == 1
    assert conn.conflict_reads == 1, (
        "the guard did not ask the collision question — this leg would then "
        "pass by never exercising the code under test")
    assert dict(monitoring.record_write_failure_counts()) == before


def test_the_capture_arm_cannot_fire_when_this_write_carries_no_ts() -> None:
    """The `recorded_at` arm MUST be guarded (the review's false-red).

    `ts` reaches the durable row (`COALESCE(%s::timestamptz, now())`), so a
    comparison of it is how the `ts`-only difference became visible — but bound
    UNCONDITIONALLY it is a false-positive machine: a write with no `ts` leaves
    `recorded_at` to `now()`, so every replay of a `ts`-less record would find a
    different `recorded_at` and be refused. Measured in a real database:
    `now() IS DISTINCT FROM NULL::timestamptz` is TRUE.

    So the arm is CONDITIONAL on THIS write having a capture time, and this leg
    pins both halves: the clause is guarded in the SQL, and the decision is
    unreachable for a NULL bind. Pinned on the SQL as well as on behaviour
    because a behavioural leg can only reach a `ts`-less replay through the whole
    door — `test_a_ts_less_replay_is_silent_on_a_real_database` is that door.
    """
    sql = rp._JOURNAL_CONFLICT_CONTENT_SQL
    assert "recorded_at IS DISTINCT FROM" in sql, (
        "the capture-time arm is GONE — the ts-only difference is invisible to "
        "the verdict again, which is #12904 P0-2 re-opened")
    assert "%s::timestamptz IS NOT NULL" in sql, (
        "the capture-time arm is UNGUARDED — a `ts`-less replay falls back to "
        "`now()` there, so two replays of ONE record would be refused as "
        "collisions")
    # …and the guard CONJOINS the arm rather than sitting beside it: an `OR`
    # here keeps the `IS NOT NULL` text while restoring exactly the false red it
    # was added for, and only the DB leg caught that, so the CONJUNCTION is
    # pinned too. (There is no WORKTREE-REPORT.md in this tree — it is
    # gitignored, so nothing may point at it; the red measurement lives in the
    # PR body and in the DB leg's own guard.)
    assert "AND recorded_at IS DISTINCT FROM" in sql, (
        "the capture arm is OR-ed in, not conjoined — `IS NOT NULL` is present "
        "but no longer guards the comparison")
    # A NULL bind can never read as the same capture: `recorded_at` is NOT NULL.
    conn = Absorbed(stored=None)
    ts_less = {k: v for k, v in RECORD_A.items() if k != "ts"}
    assert rp._journal_row(ts_less, 1, ORG, GRAPH).params[5] is None
    report = rp.replay([ts_less], conn, org_id=ORG, graph_id=GRAPH)
    assert report.events == 1
    assert conn.conflict_reads == 1


def test_a_different_event_type_alone_is_a_collision() -> None:
    """The verdict does not hang on the payload being comparable.

    A stored row of a DIFFERENT type is a collision even if the payload were
    identical, and the SQL's `event_type IS DISTINCT FROM …` arm is what makes
    that true. Asserted through the guard, so a rewrite that compared only
    payloads reds here.
    """
    conn = Absorbed(stored=_stored("PointAdded", _payload_of(RECORD_A),
                                   same_type=False))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)
    assert excinfo.value.stored_event_type == "PointAdded"
    assert excinfo.value.event_type == "ObjectRegistered"


def test_a_different_event_type_at_the_SAME_capture_instant_is_still_a_collision() -> None:
    """DRIFT REQUIRES THE SAME TYPE: `same_type` is a conjunct, not a courtesy.

    A row of a DIFFERENT type at the SAME capture instant is a COLLISION. The
    drift arm reads `same_type and same_capture and ts is not None`, and this leg
    is what pins the FIRST conjunct — before it, no leg did: the other two
    type-difference legs (`test_a_different_event_type_alone_is_a_collision`,
    `test_a_shared_NODE_id_with_two_record_types_is_not_one_record`) pass
    `same_capture=False` and take the collision path regardless, and every
    same-capture leg compares same-type records. Drop `same_type` and a record
    whose TYPE changed is silently absorbed — a wider silent window than the
    declared mirror residual, which at least requires the same type.
    """
    conn = Absorbed(stored=_stored("PointAdded", _payload_of(RECORD_A),
                                  same_type=False, same_capture=True))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)
    assert excinfo.value.stored_event_type == "PointAdded"
    assert excinfo.value.event_type == "ObjectRegistered"
    # ⛔ AND THE MESSAGE NAMES A DIFFERENCE THIS PAIR HAS (review cycle 8):
    # `ENVELOPE_KEYS` strips `type`, so both sides carry the SAME payload — a
    # clause saying "only the CAPTURE TIME differs" would be false here.
    message = str(excinfo.value)
    assert "the record TYPE" in message, (
        "the refusal named the capture time as the only difference for a "
        "TYPE-only difference")
    assert "only the CAPTURE TIME differs" not in message


def test_the_position_statement_carries_no_schema_ask() -> None:
    """NO SCHEMA CHANGE: the position row is written exactly as before (#9318).

    The detector reads the cursor and asks a question; it does not widen the
    key, add a column, or move the row onto a new table. Reverting to a
    `RETURNING`/`DO UPDATE`/probe-in-the-VALUES spelling reds here and in the
    `#9187` plan snapshot.

    ⛔ RETIREMENT IS A DELETION, ATOMIC WITH THE KEY CHANGE (#12904 P1-4):
    **delete this guard with the key change; leaving it in place converts
    legitimate old-journal replays into refusals.** An earlier wording said the
    guard would "simply never fire" after #12894 — backwards, because after the
    key change a conflict IS same-journal-same-seq, so a payload difference
    there is this record's own drift, which is exactly what this guard reports.
    ⛔ AND THIS PIN CARRIES THE TWO READINGS TOO, not #12894 alone (review P2):
    a red here means EITHER that the key gained journal identity (#12894, so the
    pin is DELETED with the guard) OR that the STATEMENT WAS RESHAPED — which is
    the only other way this assertion can move, because `_journal_row` returns
    `_JOURNAL_POSITION_INSERT_SQL` VERBATIM. A payload-derivation change moves the
    PARAM assertions below, never this one; naming it here sent the next reader
    hunting a cause this red cannot have.
    """
    statement = rp._journal_row(RECORD_A, 1, ORG, GRAPH)
    assert statement.sql == rp._JOURNAL_POSITION_INSERT_SQL, (
        "`_journal_row` no longer emits the statement the collision guard knows "
        "how to read. TWO readings, and this pin does not choose between them: "
        "if the KEY GAINED JOURNAL IDENTITY (#12894), the guard is DELETED with "
        "it — do not re-point this pin at the new shape; if instead the "
        "STATEMENT WAS RESHAPED (`RETURNING`, `DO UPDATE`, or a probe folded "
        "into the `VALUES` list), the guard's POSITIONAL bind reading must be "
        "RE-MEASURED against the new spelling")
    assert statement.sql.endswith(
        "ON CONFLICT (org_id, graph_id, seq) DO NOTHING")
    assert "RETURNING" not in statement.sql
    assert "DO UPDATE" not in statement.sql
    # The conflict question names only columns the migration already carries.
    assert "event_type IS DISTINCT FROM" in rp._JOURNAL_CONFLICT_CONTENT_SQL
    assert "payload IS DISTINCT FROM" in rp._JOURNAL_CONFLICT_CONTENT_SQL
    # ⛔ AND THE GUARD READS THIS PARAMETER ORDER. `_refuse_a_journal_collision`
    # unpacks the six binds as `(org, graph, seq, type, payload, ts)`; a reorder
    # would make its SELECT name a position that does not exist, which is a
    # SILENT MISS — the one failure mode a guard may not have unobserved. Pinned
    # where the order is defined, so the two cannot drift.
    assert statement.params[:4] == (ORG, GRAPH, 1, "ObjectRegistered")
    assert json.loads(statement.params[4]) == _payload_of(RECORD_A)
    assert statement.params[5] == RECORD_A["ts"]
    assert len(statement.params) == rp._JOURNAL_POSITION_PARAM_COUNT


def test_the_guard_bails_when_the_position_statement_is_reshaped() -> None:
    """P1-3 (#12904): a reshaped statement must BAIL, never be re-bound.

    The guard reads the position statement's binds POSITIONALLY. When #12894
    adds journal identity to the key, the same code would read `journal_id` as
    `seq` and `seq` as `event_type` — silently — and then fail on `%s::jsonb`
    for EVERY ABSORBED position, i.e. it would turn the idempotent replay this
    guard exists to protect into an error on every record.

    Reproduced with the shape #12894 will produce: `journal_id` inserted as the
    third bind. The assertion is that the refusal names the RESHAPE and its
    remedy, before any unpacking — not a driver error from a mis-bound SELECT.
    """
    genuine = rp._journal_row(RECORD_A, 1, ORG, GRAPH)
    # TWO shapes, because the guard has TWO ways to be fooled and each lands on a
    # different half of the check:
    #   * the statement RENAMED — the #12894 shape, `journal_id` in the key;
    #   * the statement UNCHANGED but a bind APPENDED — the review's exact
    #     scenario, where `params[:5]` stays byte-identical and only the ARITY
    #     moves.
    reshaped_sql = rp.Statement(
        "INSERT INTO public.journal_event "
        "(org_id, graph_id, journal_id, seq, event_type, payload, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, COALESCE(%s::timestamptz, now())) "
        "ON CONFLICT (org_id, graph_id, journal_id, seq) DO NOTHING",
        (ORG, GRAPH, "journal-1", 1, "ObjectRegistered",
         json.dumps(_payload_of(RECORD_A)), RECORD_A["ts"]),
    )
    appended_bind = rp.Statement(genuine.sql, (*genuine.params, "journal-1"))

    for label, statement in (("renamed", reshaped_sql),
                             ("bind appended", appended_bind)):
        conn = Absorbed(stored=_stored("PointAdded", _payload_of(RECORD_A),
                                       same_type=False))
        with pytest.raises(rp.RecordStatementError) as excinfo:
            rp._refuse_a_journal_collision(conn.cursor(), statement, 1, 1)

        message = str(excinfo.value)
        assert "RESHAPED" in message, (
            f"a {label} position statement did not bail — it was unpacked "
            f"positionally, which is the silent re-bind #12904 P1-3 names: "
            f"{message}")
        assert "DELETE it with the key change" in message, (
            "the bail does not name its remedy, so the next lane reads a "
            "driver-shaped failure and patches around it — and the budget is "
            "200 chars for this half, so the remedy must come BEFORE anything "
            "that can be cut")
        # …and it never reached the query: a bail that still asked would be a
        # mis-bound SELECT, which is the failure this leg exists to prevent.
        assert conn.conflict_reads == 0, label


# ════════════════════════════════════════════════════════════════════════════
# 2. THE DOOR'S REPORT — the SAME channel every refusal already uses
# ════════════════════════════════════════════════════════════════════════════


def _arm(monkeypatch: pytest.MonkeyPatch, conn: FakeConnection) -> None:
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://record/db")
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", ORG)
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", GRAPH)
    monkeypatch.setattr(rw, "_default_connect", lambda dsn: conn)


def test_the_live_door_reports_a_collision_as_its_own_refusal(
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture) -> None:
    """`journal_collision` on the EXISTING counter + WARNING + `RecordWrite`.

    The collision arrives at the door WRAPPED (`RecordWriter.write` re-raises
    every projection failure as `RecordWriteError from exc`), so this leg is
    also the pin on `_journal_collision_cause`: a door that classified by
    exception type alone would report the generic `write_failed` and an operator
    would never learn that two records claimed one position.
    """
    conn = Landed()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise rp.JournalPositionCollision(
            org_id=ORG, graph_id=GRAPH, seq=1,
            event_type="ObjectRegistered", payload_digest="sha256:aaaaaaaaaaaa",
            payload_bytes=65, stored_event_type="PointAdded",
            stored_payload_digest="sha256:bbbbbbbbbbbb", stored_payload_bytes=70)

    monkeypatch.setattr(rp, "replay", _boom)
    _arm(monkeypatch, conn)

    with caplog.at_level(logging.WARNING):
        write = rw.write_live_record(RECORD_A, seq=1)

    assert write is not None
    assert write.applied is False
    assert write.refusal == "journal_collision"
    # The SAME counter every other refusal counts on — no new channel.
    assert monitoring.record_write_failure_counts().get("journal_collision") == 1
    assert write.first_seq == 1 and write.last_seq == 0, (
        "a refused position must not be reported as consumed (the `last_seq + 1` "
        "retry rule would otherwise rewind to position 1)")
    assert "journal_collision" in caplog.text
    assert "PointAdded" in caplog.text
    # The breaker is a CONNECT fact; a collision must not trip it.
    assert rw._breaker_is_open() is False


def test_a_write_that_failed_for_another_reason_is_still_write_failed(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """The collision branch must not swallow the ordinary failure vocabulary."""
    conn = Landed()

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise rp.RecordStatementError("journal_event insert failed (something)")

    monkeypatch.setattr(rp, "replay", _boom)
    _arm(monkeypatch, conn)

    write = rw.write_live_record(RECORD_A, seq=1)
    assert write is not None and write.refusal == "write_failed"


# ════════════════════════════════════════════════════════════════════════════
# 3. THE ACCEPTANCE LEG — two DURABLE journals, one tenant, one ordinal
# ════════════════════════════════════════════════════════════════════════════


def _db_helpers():
    """The DB helpers the other record legs already own — imported, not copied.

    `_ensure_record_schema` applies the migrations the migrations registry
    names, `_new_tenant` mints a tenant no earlier run has used, and
    `_app_role_dsn` connects as the non-bypassing role `replay()` demands. A
    second copy of any of them is how two legs come to disagree about what
    "migrated" or "armed" means.
    """
    from tests.test_9078_record_written_by_production_path import (
        _app_role_dsn,
        _ensure_record_schema,
        _new_tenant,
        _record_db,
        _seed_tenant,
    )

    return (_app_role_dsn, _ensure_record_schema, _new_tenant, _record_db,
            _seed_tenant)


@pytest.fixture()
def record_tenant(monkeypatch: pytest.MonkeyPatch):
    """A tenant this test alone has used, armed onto the live door.

    `journal_event` is append-only by trigger, so a shared pair would carry a
    previous run's positions and this leg's second write would collide with ITS
    OWN history instead of with the second journal (#9307 review F2/F4). The
    tenant is minted per test and the pair is armed exactly the way
    `.env.example` documents (the declaration included — the pair alone is
    refused, #9192).
    """
    (_app_role_dsn, _ensure_record_schema, _new_tenant, _record_db,
     _seed_tenant) = _db_helpers()
    admin = _record_db()
    _ensure_record_schema(admin)
    tenant = _new_tenant()
    _seed_tenant(admin, tenant)
    org, graph = tenant

    monkeypatch.setenv("TORTOISE_RECORD_DSN",
                       _app_role_dsn(os.environ[RECORD_DB_ENV]))
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    monkeypatch.setenv("TORTOISE_RECORD_ORG_ID", org)
    monkeypatch.setenv("TORTOISE_RECORD_GRAPH_ID", graph)
    try:
        yield admin, tenant, org, graph
    finally:
        admin.close()


def _journal_rows(admin: Any, tenant: tuple[str, str]) -> list[tuple[int, str, dict]]:
    org, graph = tenant
    with admin.cursor() as cur:
        cur.execute(
            "SELECT seq, event_type, payload FROM public.journal_event "
            "WHERE org_id = %s AND graph_id = %s ORDER BY seq", (org, graph))
        return [(seq, event_type, payload) for seq, event_type, payload in cur.fetchall()]


def _layer_keys(admin: Any, table: str, tenant: tuple[str, str]) -> list[str]:
    org, graph = tenant
    with admin.cursor() as cur:
        cur.execute(
            f"SELECT node_key FROM public.{table} "
            f"WHERE org_id = %s AND graph_id = %s ORDER BY node_key", (org, graph))
        return [row[0] for row in cur.fetchall()]


def test_two_durable_journals_at_one_ordinal_are_refused_not_absorbed(
        record_tenant, tmp_path: Path,
        caplog: pytest.LogCaptureFixture) -> None:
    """THE ACCEPTANCE LEG (#9318) — the falsifier, on a real Postgres.

    Two DISTINCT durable `EventLog` files under ONE record tenant, one record
    appended from each: both journals number their first record `1`, which is
    the collision. On the pre-#9318 tree this leg reds — the second write
    returns `APPLIED`, `journal_event` holds ONE row naming journal A's record,
    and the second record's layer rows are written UNDER that foreign position.
    After the guard, the second write is REFUSED, the layer rows are not
    written, and the record still names what it actually holds.
    """
    admin, tenant, _, _ = record_tenant

    journal_a = EventLog(tmp_path / "corpus-a" / ".events.jsonl")
    journal_b = EventLog(tmp_path / "corpus-b" / ".events.jsonl")
    seq_a = journal_a.append(RECORD_A)
    seq_b = journal_b.append(RECORD_B)
    assert (seq_a, seq_b) == (1, 1), (
        "the reproduction needs both journals numbered from 1 — that is the "
        "collision; if this changed, the ordinal's source moved (#9190)")

    write_a = rw.write_live_record(RECORD_A, seq=seq_a)
    assert write_a is not None and write_a.applied, write_a
    with caplog.at_level(logging.WARNING):
        write_b = rw.write_live_record(RECORD_B, seq=seq_b)

    # ── the loud failure ───────────────────────────────────────────────────
    assert write_b is not None
    assert write_b.applied is False, (
        "the second journal's write was reported APPLIED — that is the silent "
        "absorption #9318 exists to make loud")
    assert write_b.refusal == "journal_collision"
    assert monitoring.record_write_failure_counts().get("journal_collision") == 1
    assert "journal_collision" in caplog.text

    # ── the data, unchanged and honestly indexed ──────────────────────────
    rows = _journal_rows(admin, tenant)
    assert len(rows) == 1, (
        f"journal_event holds {len(rows)} position row(s) for two journals: "
        f"{rows!r} — a second row here would mean the second journal's position "
        f"was written, which the key cannot allow")
    seq, event_type, payload = rows[0]
    assert seq == 1 and event_type == "ObjectRegistered"
    assert payload["id"] == "obj-a", (
        f"the surviving position row names {payload['id']!r}, not journal A's "
        f"'obj-a' — the row was rewritten, and `journal_event` is append-only "
        f"by trigger (#9318)")
    # The refused record's layer rows are NOT written under a foreign position.
    assert _layer_keys(admin, "object", tenant) == ["obj-a"], (
        "the refused record's layer rows landed under another record's "
        "position row — that is the mis-index, not a gap (#9318)")


def test_a_genuine_replay_of_one_journal_stays_a_silent_no_op(
        record_tenant, tmp_path: Path) -> None:
    """THE OTHER FALSIFIER (#9318): a replay must stay a SILENT no-op.

    The same record, from the same journal, applied twice. The position row is
    absorbed by ITSELF — same key, same content — which the schema calls
    idempotency and which reads `APPLIED` with nothing counted. An over-eager
    guard (one that fired on `rowcount == 0` alone) reds every assertion below,
    and it would make the record's own repair story (`replay` heals the record)
    unusable.
    """
    admin, tenant, _, _ = record_tenant
    journal = EventLog(tmp_path / "corpus" / ".events.jsonl")
    seq = journal.append(RECORD_A)

    first = rw.write_live_record(RECORD_A, seq=seq)
    assert first is not None and first.applied, first
    second = rw.write_live_record(RECORD_A, seq=seq)

    assert second is not None
    assert second.applied is True, (
        f"the genuine replay was refused as {second.refusal!r} — a guard that "
        f"turns every replay into an error is a WORSE defect than the one "
        f"#9318 fixes (the schema: applying a position twice is a no-op)")
    assert second.refusal is None
    assert monitoring.record_write_failure_counts().get("journal_collision") is None
    rows = _journal_rows(admin, tenant)
    assert [(seq_, payload["id"]) for seq_, _, payload in rows] == [(1, "obj-a")]


def test_a_ts_less_replay_is_silent_on_a_real_database(
        record_tenant, tmp_path: Path) -> None:
    """THE GUARD ON THE `recorded_at` ARM, measured against Postgres (#12904).

    The stub-based legs cannot discriminate the predicate — they answer with the
    row the query WOULD return (review P2-5) — so the one case that decides
    whether the new arm is guarded has to run on a real database. It is the case
    the review's false-red warning names: a record carrying NO `ts`, written
    twice.

    `_journal_row` binds `COALESCE(%s::timestamptz, now())`, so the FIRST write
    stamps a real `recorded_at`, while the second write binds NULL there. With an
    UNGUARDED arm (`recorded_at IS DISTINCT FROM %s::timestamptz`) that NULL is
    compared against the stamped value, `now() IS DISTINCT FROM NULL` is TRUE
    (measured in PG), the predicate matches the row and the healthy replay is
    refused as a collision. The `%s::timestamptz IS NOT NULL` guard is what makes
    the arm conditional on there being a capture time to compare, and this leg is
    what reds when it is removed.
    """
    _, _tenant, _, _ = record_tenant
    journal = EventLog(tmp_path / "corpus" / ".events.jsonl")
    ts_less = {k: v for k, v in RECORD_B.items() if k != "ts"}
    assert "ts" not in ts_less
    seq = journal.append(ts_less)

    first = rw.write_live_record(ts_less, seq=seq)
    assert first is not None and first.applied, first
    second = rw.write_live_record(ts_less, seq=seq)

    assert second is not None
    assert second.applied is True, (
        f"a `ts`-less replay was refused as {second.refusal!r}: the capture-time "
        f"arm compared the row's stamped `recorded_at` against the INSERT's "
        f"`COALESCE(NULL, now())` — the false red #12904 P0-2 warned about")
    assert second.refusal is None
    assert monitoring.record_write_failure_counts().get("journal_collision") is None


def test_a_re_derived_payload_is_silent_on_a_real_database(
        record_tenant, tmp_path: Path) -> None:
    """DRIFT, measured against Postgres: same record, same capture, new payload.

    The stub legs can only say what the guard does with a row they hand it. This
    one makes POSTGRES return that row: the surviving position row is written
    from one payload, then the SAME record — same type, same `ts`, so the same
    `recorded_at` — is written again with a payload this tree now derives
    differently. That is precisely the re-derivation case that used to be
    refused, so this leg is the DB-side proof that #12904 P0-1's polarity holds
    where it matters.

    ⚠️ The layer rows are NOT discarded with the absorbed position: the drift arm
    `return`s WITHOUT raising, so this write reaches its own `commit()` and every
    layer statement it re-derived LANDS — asserted here only for the position
    row (append-only, unchanged), which is why the layer effect is stated rather
    than pinned. For the same-type drift case that is the repair (the row moves to
    the new derivation); for the mirror residual it is the cost: the second
    record's LAYER rows are applied and committed while its position row is
    dropped — the mis-index shape #9318 exists to stop.
    """
    admin, tenant, _, _ = record_tenant
    journal = EventLog(tmp_path / "corpus" / ".events.jsonl")
    seq = journal.append(RECORD_A)

    first = rw.write_live_record(RECORD_A, seq=seq)
    assert first is not None and first.applied, first

    # The SAME record (`type`, `ts` and therefore `recorded_at` all unchanged),
    # with one field's value changed — a derivation that now emits something
    # other than what that row holds.
    re_derived = dict(RECORD_A, name="corpus A document (re-derived)")
    second = rw.write_live_record(re_derived, seq=seq)

    assert second is not None
    assert second.applied is True, (
        f"a re-derived record was refused as {second.refusal!r} — the record's "
        f"own repair story is then version-pinned to the tree that wrote the row "
        f"(#12904 P0-1)")
    assert second.refusal is None
    assert monitoring.record_write_failure_counts().get("journal_collision") is None
    rows = _journal_rows(admin, tenant)
    assert len(rows) == 1 and rows[0][2]["name"] == RECORD_A["name"], (
        "the surviving position row must still hold the FIRST derivation — a "
        "drift replay may not rewrite an append-only row")


def test_two_journals_ingesting_one_document_differing_only_in_capture_time_are_refused(
        record_tenant, tmp_path: Path) -> None:
    """P0-2's acceptance leg on a real database: the ts-only collision (#9318).

    Two durable journals, the SAME record appended to each — same type, same
    payload, so nothing in the durable CONTENT differs — apart only in capture
    time (`ts`, which lands in `recorded_at`). That is the reachable shape #9318
    names, and before the `recorded_at` arm it was absorbed SILENTLY: the
    predicate matched no row and the second journal's document was filed under
    the first's position.

    ⛔ On the pre-fix tree this leg reds with `applied=True` and one position row.
    """
    admin, tenant, _, _ = record_tenant
    journal_a = EventLog(tmp_path / "corpus-a" / ".events.jsonl")
    journal_b = EventLog(tmp_path / "corpus-b" / ".events.jsonl")
    first = dict(RECORD_A, ts="2026-11-15T00:00:00Z", event_id="evt-first")
    second = dict(RECORD_A, ts="2026-11-16T09:30:00Z", event_id="evt-second")
    seq_a = journal_a.append(first)
    seq_b = journal_b.append(second)
    assert (seq_a, seq_b) == (1, 1)
    # The premise: the durable CONTENT of the two is identical, so only the
    # capture time can separate them.
    assert _payload_of(first) == _payload_of(second)

    write_a = rw.write_live_record(first, seq=seq_a)
    assert write_a is not None and write_a.applied, write_a
    write_b = rw.write_live_record(second, seq=seq_b)

    assert write_b is not None
    assert write_b.applied is False, (
        "two journals ingesting one document, apart only in capture time, were "
        "reported APPLIED — that is the silent absorption #9318 exists to make "
        "loud, and it is exactly what the `recorded_at` arm closes")
    assert write_b.refusal == "journal_collision"
    rows = _journal_rows(admin, tenant)
    assert len(rows) == 1
