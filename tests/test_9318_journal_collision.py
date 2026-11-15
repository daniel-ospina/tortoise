"""#9318 — a position absorbed by a DIFFERENT record is refused LOUDLY.

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

    The message must identify BOTH records without quoting their CONTENT: the
    record's own failure lines withhold payload text (`_CONTENT_COLUMNS`), and a
    payload can carry the tenant's words. An ID is not content — the failure
    lines already print ids, and the id is the one thing that tells an operator
    which two records met — so the identity is named and the title is not.
    """
    conn = Absorbed(stored=("ObjectRegistered", _payload_of(RECORD_A)))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_B], conn, org_id=ORG, graph_id=GRAPH)

    message = str(excinfo.value)
    assert "position 1" in message
    assert ORG in message and GRAPH in message
    assert "sha256:" in message
    # Two DIFFERENT digests: the payloads differ, so the digests disagree.
    assert excinfo.value.payload_digest != excinfo.value.stored_payload_digest
    # …the two records are NAMED by identity, …
    assert excinfo.value.identity_state == "different"
    assert "obj-a" in message and "obj-b" in message
    # …and neither payload's CONTENT (a document title) appears.
    for record in (RECORD_A, RECORD_B):
        assert record["name"] not in message
    assert excinfo.value.seq == 1
    assert excinfo.value.event_type == "ObjectRegistered"
    assert excinfo.value.stored_event_type == "ObjectRegistered"


def test_a_re_derived_record_is_not_reportable_as_two_records() -> None:
    """The diagnosis must not assert a cause the verdict cannot know (#9318 R1).

    The compared payload is a snapshot of a DERIVATION (`_journal_row` strips
    `ENVELOPE_KEYS`), so a change to that derivation makes an old row's content
    differ from what this tree would write for the SAME record at the SAME
    position. The refusal is still right — the record may not absorb the
    difference — but the MESSAGE must not send an operator hunting a second
    journal that does not exist. It reports the shared identity instead, and
    says so in a distinguishable way.
    """
    re_derived = dict(_payload_of(RECORD_A))
    re_derived["name"] = "corpus A document (re-derived)"
    conn = Absorbed(stored=("ObjectRegistered", re_derived))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "same"
    message = str(excinfo.value)
    assert "re-derivation" in message
    assert "no record id" in message, (
        "the message must say WHY it cannot tell one record from two")
    # ⛔ The words that assert a record identity, which the row cannot carry.
    assert "SAME record" not in message
    assert "the SAME" not in message
    # The collision claim is NOT made: no "different records" diagnosis.
    assert "DIFFERENT records" not in message
    # …and, as ever, no payload content.
    assert "corpus A document" not in message


def test_two_payloads_with_no_comparable_identity_say_so() -> None:
    """`unknown` is an answer, not a guess — a difference still collides."""
    # NEITHER side carries an identity key, so `unknown` is the honest answer
    # (`one_sided` is the answer when only one of them does — asserted below).
    # `IngestStarted` is a declared `NoRecordEffect` type (audit-only run
    # marker), so its record carries no node identity at all — the one shape in
    # which NO identity is comparable on both sides.
    anonymous = {"type": "IngestStarted", "ts": "2026-11-15T00:00:00Z",
                 "run": 1}
    conn = Absorbed(stored=("IngestStarted", {"run": 2}))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([anonymous], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "unknown"
    assert "no record identity could be compared" in str(excinfo.value)
    assert "DIFFERENT records" not in str(excinfo.value), (
        "a state with no comparable identity may not assert a cause")


def test_a_shared_NODE_id_with_two_record_types_is_not_one_record() -> None:
    """A shared id is a NODE id, so two record types are two records (#9318 R2).

    Cycle 2 of review found the first cut of the identity verdict returned
    `same` on any shared id: `ObjectRegistered` vs `EntityMutated` about one
    object came back as "the SAME record", which is the cycle-1 defect mirrored
    — a cause asserted that the evidence cannot support, and every collision
    origin omitted from the sentence. The state is `same_coordinate`, and its
    text names the collision origins.
    """
    conn = Absorbed(stored=("EntityMutated", _payload_of(RECORD_A)))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "same_coordinate"
    message = str(excinfo.value)
    assert "record TYPES differ" in message
    assert "second durable journal" in message
    assert "SAME record" not in message


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
    conn = Absorbed(stored=("PointAdded", stored_nested))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([nested], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "same"
    assert "point.id=p1" in str(excinfo.value), (
        "the identity was not read from the nested snapshot — a PointAdded's id "
        "lives under `point`, and reporting 'unknown' for it tells an operator "
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
    conn = Absorbed(stored=("PointRevised", stored))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([payload], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "same"
    message = str(excinfo.value)
    # BOTH possibilities, because the row cannot distinguish them.
    assert "re-derivation of one record" in message
    assert "two records about one node" in message
    assert "SAME record" not in message
    assert "SAME NODE" not in message


def test_an_agreeing_coarse_key_cannot_suppress_a_disagreeing_one() -> None:
    """Two points of one capture session are two records (#9318 review cycle 3).

    The first cut returned on the FIRST identity key both sides carried, in
    `_IDENTITY_KEYS` order — so an agreeing `session_id` decided the verdict
    before a DISAGREEING `id` was ever read. Caller-set `session_id` rides the
    point snapshot into `PointAdded`/`PointRevised`, so a genuine two-journal
    collision between two points of one session reported "a re-derivation of one
    record" while the proof of two records sat in the same payload pair.
    """
    ours = {"type": "PointAdded",
            "point": {"id": "p-a", "session_id": "s1", "content": "a"}}
    stored = {"point": {"id": "p-b", "session_id": "s1", "content": "b"}}
    conn = Absorbed(stored=("PointAdded", stored))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([ours], conn, org_id=ORG, graph_id=GRAPH)

    assert excinfo.value.identity_state == "different"
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
    key would call them incomparable and fall back to `unknown` — losing the one
    signal the class exists to report.
    """
    state, detail = rp._identity_verdict(
        {"point": {"id": "p1"}}, {"id": "p1"}, "PointRevised", "PointRevised")

    assert state == "same"
    assert detail == "point.id=p1"


def test_a_non_scalar_identity_value_is_not_rendered_into_the_log() -> None:
    """An identity VALUE is caller data and may be a whole document (#9318 R3).

    `_identity_pairs` used to accept any non-None value and `_identity_verdict`
    rendered `str(value)`, so `{"id": {"title": "Q3 pricing memo"}}` put a
    document title on a log line the class docstring promises is content-free.
    `record_writer._record_identity` accepts `str` only; this accepts `str`/`int`
    and drops everything else.
    """
    state, detail = rp._identity_verdict(
        {"id": {"title": "Q3 pricing memo"}}, {"id": "obj-a"},
        "ObjectRegistered", "ObjectRegistered")

    assert state == "one_sided"
    assert "Q3 pricing memo" not in (detail or "")


def test_the_surviving_row_may_be_the_only_side_naming_an_identity() -> None:
    """The mirror of `one_sided`: a field REMOVED since the row was written."""
    state, detail = rp._identity_verdict(
        {"objectKind": "document"}, {"id": "obj-a"},
        "ObjectRegistered", "ObjectRegistered")

    assert state == "one_sided"
    assert detail == "only the surviving row names id=obj-a"


def test_a_number_typed_id_is_compared_by_type_not_by_stringification() -> None:
    """`{"id": 1}` and `{"id": "1"}` are different ids (#9318 review cycle 3).

    A jsonb round-trip preserves numeric ids, so comparing `str(value)` equality
    would call a numeric id and its string form the same record — the same
    over-claim as the coordinate states, one level down.
    """
    state, _detail = rp._identity_verdict(
        {"id": "1"}, {"id": 1}, "ObjectRegistered", "ObjectRegistered")

    assert state == "different"


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
    state, detail = rp._identity_verdict(
        {"id": "p1", "aboutObject": {"id": "obj-9"}}, {"id": "p1"},
        "PointRevised", "PointRevised")

    assert state == "same"
    assert detail == "id=p1"


def test_a_key_present_in_TWO_scopes_is_not_compared_across_them() -> None:
    """Two ids on one side, one on the other: nothing is comparable (#9318 R4).

    With the key present twice on this side, a key-only fallback would pick one
    of them arbitrarily and print a counterpart it never compared. The pair is
    skipped, and the same-scope agreement still decides.
    """
    state, detail = rp._identity_verdict(
        {"id": "p1", "point": {"id": "p9"}}, {"id": "p1"},
        "PointRevised", "PointRevised")

    assert state == "same"
    assert detail == "id=p1"


def test_a_BLANK_identity_is_not_an_agreeing_coordinate() -> None:
    """`""` is a defaulted field, not evidence (#9318 review cycle 4).

    The previous cut admitted the empty string, so two blanks "agreed" (`same`
    from no evidence) and a blank against a real id "disagreed" — the collision
    claim, from the re-derivation signature that `one_sided` exists to report.
    `record_writer._record_identity` requires a non-empty string for the same
    reason.
    """
    assert rp._identity_verdict({"id": ""}, {"id": ""}, "A", "A") == (
        "unknown", None)
    state, detail = rp._identity_verdict({"id": ""}, {"id": "obj-a"}, "A", "A")
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
    state, detail = rp._identity_verdict(
        {"id": huge}, {"id": "b"}, "ObjectRegistered", "ObjectRegistered")

    assert state == "different"
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


def test_a_different_event_type_alone_is_a_collision() -> None:
    """The verdict does not hang on the payload being comparable.

    A stored row of a DIFFERENT type is a collision even if the payload were
    identical, and the SQL's `event_type IS DISTINCT FROM …` arm is what makes
    that true. Asserted through the guard, so a rewrite that compared only
    payloads reds here.
    """
    conn = Absorbed(stored=("PointAdded", _payload_of(RECORD_A)))

    with pytest.raises(rp.JournalPositionCollision) as excinfo:
        rp.replay([RECORD_A], conn, org_id=ORG, graph_id=GRAPH)
    assert excinfo.value.stored_event_type == "PointAdded"
    assert excinfo.value.event_type == "ObjectRegistered"


def test_the_position_statement_carries_no_schema_ask() -> None:
    """NO SCHEMA CHANGE: the position row is written exactly as before (#9318).

    The detector reads the cursor and asks a question; it does not widen the
    key, add a column, or move the row onto a new table. Reverting to a
    `RETURNING`/`DO UPDATE`/probe-in-the-VALUES spelling reds here and in the
    `#9187` plan snapshot, so the retire path (`#12894` gives the key journal
    identity, the guard is deleted) stays a deletion rather than a migration.
    """
    statement = rp._journal_row(RECORD_A, 1, ORG, GRAPH)
    assert statement.sql.endswith(
        "ON CONFLICT (org_id, graph_id, seq) DO NOTHING")
    assert "RETURNING" not in statement.sql
    assert "DO UPDATE" not in statement.sql
    # The conflict question names only columns the migration already carries.
    assert "event_type IS DISTINCT FROM" in rp._JOURNAL_CONFLICT_CONTENT_SQL
    assert "payload IS DISTINCT FROM" in rp._JOURNAL_CONFLICT_CONTENT_SQL
    # ⛔ AND THE GUARD READS THIS PARAMETER ORDER. `_refuse_a_journal_collision`
    # unpacks `statement.params[:5]` as `(org, graph, seq, type, payload)`; a
    # reorder here would make its SELECT name a position that does not exist,
    # which is a SILENT MISS — the one failure mode a guard may not have
    # unobserved. Pinned where the order is defined, so the two cannot drift.
    assert statement.params[:4] == (ORG, GRAPH, 1, "ObjectRegistered")
    assert json.loads(statement.params[4]) == _payload_of(RECORD_A)


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
