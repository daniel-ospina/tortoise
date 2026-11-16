"""#12894 stage C — the allocator, EXECUTED against a real Postgres.

WHY THIS FILE EXISTS. Round 3 of the plan review asked one question of every fix —
*"what does this do when it runs?"* — and found three P0s that had been applied as
SENTENCES rather than as mechanism. A document cannot answer that question; a
statement executed against a real server can. Every test here runs the SQL this
change ships, on a Postgres carrying the record migrations, and asserts the fact
the plan could only describe.

THE FINDINGS → TESTS (each was marked WRONG or UNPROVEN in review round 2):

================================= ==============================================
`GREATEST(0,0)=0` with `seq > 0`  `test_an_empty_graph_seeds_and_its_first_write_is_one`
  ⇒ an empty graph cannot seed      (23514 absent; the first position is 1)
journal + zero position rows      `test_a_journal_with_no_position_rows_seeds_past_it`
  ⇒ seeded 0, next write 1          (N records ⇒ next write N+1)
case 2 (record ahead of journal)   `test_case_two_writes_no_counter_row`
  ⇒ must not be representable       (raw SQL inserts nothing + SeedRefused)
a seed with no `app.current_org`  `test_a_seed_without_the_guc_is_a_refusal_not_a_divergence`
  ⇒ 42501, mis-read as divergence   (raw SQL raises 42501; `seed()` does not)
`generate_series` N copies        `test_a_batch_writes_distinct_payloads_in_one_statement`
  ⇒ one payload, N times            (3 distinct payloads on 3 consecutive positions)
allocated number lost             `test_the_allocated_number_reaches_the_watermark_and_the_byte_meter`
  ⇒ meter charges 0 silently        (first/last_seq = 6/7, rows 2, bytes > 0)
`ON CONFLICT DO NOTHING` absorbs   `test_a_duplicate_position_fails_the_transaction`
  ⇒ counter advances, payload lost  (23505; the layer rows roll back)
no isolation precondition (M5)    `test_a_serializable_transaction_is_refused_at_arm`
  ⇒ 40001 with no retry             (IsolationRefused before the first statement)
`57014` unhandled (M5)            `test_a_cancelled_allocation_rolls_the_counter_back`
                                    (real statement timeout; counter unchanged)
restore restarts the allocator    `test_a_restored_counter_continues_past_the_rows`
  (B3)                              (the seed reads the rows, not a sequence)

THE DSN GATE. `TEST_RECORD_DB_URI` is the record's own Postgres (the CI job
`test-record-postgres` provisions it, and `provision.sh` makes it a SUPERUSER).
Writes go through `_app_role_dsn`, because `scope_connection` REFUSES a
superuser/BYPASSRLS/owner connection — the same gate production passes through.
A skip is visible on that job (its leg gate reds any skip), and locally the
`TEST_RECORD_DB_URI` arm is what makes this file run at all.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

from tortoise import record_allocator as ra
from tortoise import record_projection as rp
from tortoise import record_writer as rw

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_RECORD_DB_URI"),
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record migrations",
)

#: One record that expands to node/identity mutations AND a position row, so a
#: write is never vacuous. `PointAdded` is the epistemic layer's cheapest shape.
RECORD = {
    "type": "PointAdded",
    "ts": "2026-11-16T00:00:00Z",
    "id": "p-12894",
    "point": {"id": "p-12894", "content": "the record owns the ordinal",
              "pointKind": "claim"},
}


def _records(n: int) -> list[dict]:
    out = []
    for i in range(n):
        point_id = f"p-12894-{i}-{uuid.uuid4().hex[:6]}"
        out.append({
            "type": "PointAdded",
            "ts": f"2026-11-16T00:00:{i:02d}Z",
            "id": point_id,
            "point": {"id": point_id, "content": f"claim {i}",
                      "pointKind": "claim"},
        })
    return out


@pytest.fixture(scope="module")
def schema():
    """The record schema (this tree's migrations) + the byte meter, once.

    ⛔ THE THREE CALLS ARE THE **FILENAME ORDER** OF THE FILES THEY APPLY, and the
    order is LOAD-BEARING — not tidiness. `20261116000001_record_cursor_allocator.sql`
    grants the byte meter's EXECUTE to `tortoise_record_app` **guarded on the
    meter's presence** (`to_regprocedure(...) IS NOT NULL`), because the record
    schema and the metering schema are applied by different production steps. Its
    function is created by the METERING migration `20261114000001`, which is
    EARLIER by filename but is NOT in `RECORD_MIGRATIONS`. So on a fresh database
    a single record-then-metering pass applies the cursor migration while the
    meter does not exist yet, the guard fires its SKIP, `tortoise_record_app` has
    no EXECUTE, and the meter raises `InsufficientPrivilege` -> returns `None`
    (TOTAL by contract) -> the org is charged 0 bytes.

    MEASURED on a fresh `pgvector/pgvector:pg16` (the exact CI shape), with the
    single-pass pair: `test_the_allocated_number_reaches_the_watermark_and_the_`
    `byte_meter` reported `written_rows=0` for a 2-record write, and
    `has_function_privilege('tortoise_record_app', …) = f` with the function and
    the role both present. It passed only when another file in the job had
    migrated the container FIRST — i.e. this file's headline assertion depended
    on the ORDER OF FILES IN THE JOB, which is exactly the accidental green a
    falsifier must not have.

    Metering-first is not an alternative: its files `CREATE POLICY` on
    `public.metering_records`, which the RECORD schema creates, so it SKIPS with
    `UndefinedTable` (measured). Re-applying the record step after the metering
    step is what production does by filename (`…09…` -> `…14…` -> `…16…`), so the
    fixture does the same thing instead of a two-call sequence that only works on
    a reused container.

    The grant is then ASSERTED, so a future ordering regression is a RED here
    instead of a zero-byte meter reading three assertions later.
    """
    from tests.test_9078_record_written_by_production_path import (
        _ensure_metering_schema,
        _ensure_record_schema,
        _record_db,
    )

    conn = _record_db()
    _ensure_record_schema(conn)      # the base shape the metering files depend on
    _ensure_metering_schema(conn)    # …14… — the meter's function comes into being
    _ensure_record_schema(conn)      # …16… — re-applied, so its guard can fire
    from tortoise import postgres_bytes

    with conn.cursor() as cur:
        # `to_regclass` answers for RELATIONS; the meter is a FUNCTION, so it is
        # probed by signature (`to_regclass` on a function name returns NULL and
        # the assertion below would then read a present meter as absent).
        cur.execute(
            "SELECT to_regclass('public.record_cursor'), "
            f"to_regprocedure('public.{postgres_bytes.ROWS_RPC}(text,text,bigint[])'), "
            f"has_function_privilege('tortoise_record_app', "
            f"'public.{postgres_bytes.ROWS_RPC}(text,text,bigint[])', 'EXECUTE')")
        row = cur.fetchone()
    assert row[0] is not None, "record_cursor was not created by the migrations"
    assert row[1] is not None, "the byte meter is absent — its assertions would lie"
    assert row[2] is True, (
        "the write role has NO EXECUTE on the byte meter, so the meter returns "
        "None and every armed write is charged 0 bytes — this is the state "
        "`20261116000001`'s guarded GRANT exists to remove, and it is skipped "
        "whenever the record migrations are applied before the metering one")
    return conn


@pytest.fixture()
def tenant(schema):
    """A tenant pair no other test in this PROCESS has used.

    Per test, because `journal_event` is APPEND-ONLY by trigger and the counter is
    per graph: a shared pair makes the second test's seed read the first test's
    rows. Teardown deletes the ORG (the FK cascade takes the counter and the
    journal rows).
    """
    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-12894-{token}", f"graph-12894-{token}"
    with schema.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s)",
                    (graph, org, graph))
    yield org, graph
    # ⛔ NO TEARDOWN DELETE. `graphs` → record tables IS `ON DELETE CASCADE`, but
    # the cascade meets `journal_event_is_append_only`, which refuses DELETE
    # unless `app.record_journal_purge='on'` (measured: the cascade raises). The
    # pair is unique per test and per process, which is the same practice the
    # sibling record suites use (`_new_tenant`): the rows are append-only by
    # design, and a suite that erased them would be testing a path production
    # does not have.


@pytest.fixture()
def app():
    """An app-role connection with an OPEN transaction (the allocator's shape)."""
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone()[0] == "tortoise_record_app", (
            "the write path must connect as the record's app role — "
            "`scope_connection` refuses a superuser/owner")
    try:
        yield conn
    finally:
        if not conn.closed:
            conn.rollback()
            conn.close()


def _seed_and_arm(app, org, graph, floor):
    result = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=floor)
    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    return result


def _journal_rows(schema, org, graph):
    with schema.cursor() as cur:
        cur.execute(
            "SELECT seq, event_type, payload FROM public.journal_event "
            "WHERE org_id = %s AND graph_id = %s ORDER BY seq", (org, graph))
        return cur.fetchall()


def _counter(schema, org, graph):
    with schema.cursor() as cur:
        cur.execute("SELECT seq, mode FROM public.record_cursor "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
        return cur.fetchone()


def _append_positions(schema, org, graph, seqs):
    """Put position rows in the record WITHOUT the allocator.

    This is the shape a restore produces (`pg_restore` bulk-loads
    `journal_event`), and the shape the `None`-position tail produces — the record
    holds positions a replay of the journal would also enumerate.
    """
    with schema.cursor() as cur:
        for seq in seqs:
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, json.dumps({"restored": seq})))


# ── the seed, for every case the reviews named ──────────────────────────────

def test_an_empty_graph_seeds_and_its_first_write_is_one(schema, tenant, app):
    """FINDING (round 2, blocking): `GREATEST(0,0) = 0` with `CHECK (seq > 0)`
    raises `23514` — the EASIEST cutover could not happen at all.

    The cursor therefore accepts 0 (last allocated = none), and the first
    allocated position is 1 — NOT 2, which a "seed to 1" workaround would make it
    while leaving position 1 unallocated forever.
    """
    org, graph = tenant
    result = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    assert (result.seq, result.mode) == (0, ra.MODE_SEEDED), result
    app.commit()
    assert _counter(schema, org, graph) == (0, "seeded")

    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    write = rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
        [RECORD], org_id=org, graph_id=graph, allocate=True, conn=app)
    app.commit()
    assert (write.first_seq, write.last_seq) == (1, 1), (
        f"an empty graph's first allocated position must be 1, got "
        f"{write.first_seq}..{write.last_seq}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1]
    assert _counter(schema, org, graph) == (1, "active")


def test_a_journal_with_no_position_rows_seeds_past_it(schema, tenant, app):
    """FINDING (round 1, B1): a graph with a large journal and ZERO
    `journal_event` rows (`None` positions write no row) seeded to 0, so its next
    write took seq 1 while the journal's replay index said N+1.

    The seed's floor is the caller's own replay enumeration, so this case — which
    IS decidable — seeds to N and the next write takes N+1. The tail is REPORTED
    (`healed_tail`), never silently repaired here.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [])          # nothing: the table is empty
    # A journal of 3 records with NO position rows: emulate by seeding with the
    # floor a replay of that journal enumerates.
    result = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert (result.seq, result.healed_tail) == (3, True), result
    app.commit()

    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    write = rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
        [RECORD], org_id=org, graph_id=graph, allocate=True, conn=app)
    app.commit()
    assert write.first_seq == 4, (
        f"after a 3-record journal with no position rows the next position is 4 "
        f"(N+1), got {write.first_seq}")
    assert ra.metrics()["seed_healed_tail"] >= 1


def test_case_two_writes_no_counter_row(schema, tenant, app):
    """FINDING (round 2 B1 / round 3 P0): CASE 2 — the record holds a position the
    journal cannot reproduce (a replaced or shortened journal).

    ⛔ THE CASE IS UNREPRESENTABLE IN THE SQL, which is what this test proves: the
    SAME statement that seeds every other graph writes ZERO rows here, so no
    counter row can exist for it. Python only classifies the absence into the
    refusal the operator needs — including both numbers, because "the seed did
    not run" and "the record is ahead of the journal" have opposite remedies.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [7])        # the record is at 7 …
    # ⛔ SCOPE FIRST, AND THAT IS PART OF THE FINDING. The seed reads
    # `journal_event` through the SAME tenant policy, so on an unscoped
    # connection the journal looks EMPTY (`row_max` reads 0), the guard passes,
    # and the insert is then refused `42501` — a mis-scope presenting as a
    # refusal (see the sibling test). With the statement scoped, the ONLY thing
    # that can suppress the row is the case-2 guard.
    rp.scope_connection(app, org)
    with app.cursor() as cur:
        # … and the journal enumerates only 3 records. Run the RAW statement, so
        # the guard under test is the SQL's, not Python's.
        cur.execute(ra.SEED_SQL, (org, graph, 3, org, graph))
        assert cur.fetchall() == [], "the seed's SQL wrote a row for case 2"
    app.rollback()
    assert _counter(schema, org, graph) is None, (
        "a counter row exists for a graph whose record is ahead of its journal")

    with pytest.raises(ra.SeedRefused) as caught:
        ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert caught.value.reason == "divergence"
    assert (caught.value.row_max, caught.value.enumerated_floor) == (7, 3)
    app.rollback()
    assert _counter(schema, org, graph) is None
    # The graph keeps working: no counter row IS "not cut over".
    assert ra.metrics()["seed_refused"] >= 1


def test_a_seed_is_re_run_safe_and_monotone(schema, tenant, app):
    """The plan's own recovery step RE-RUNS the seed, so a seed must be
    idempotent; and it must never move the counter BACKWARDS (a burn or a heal is
    not a reason to reissue a number). An ALLOCATING graph is refused rather than
    silently re-based."""
    org, graph = tenant
    first = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    second = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    assert (first.seq, second.seq) == (0, 0)
    app.commit()

    _append_positions(schema, org, graph, [1, 2, 3])
    third = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert third.seq == 3, "the re-seed did not heal to the rows' maximum"
    fourth = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert fourth.seq == 3, "a re-seed of an unchanged graph moved its counter"
    app.commit()

    # ⛔ AND IT IS MONOTONE. A BURN (a purge, or an allocation whose rows were
    # removed) leaves the counter AHEAD of the rows; re-seeding must not reissue
    # the positions it already handed out. `GREATEST(cursor, floor)` — not
    # `floor` — is what this asserts, and the case-2 guard still passes because
    # the record is not ahead of the journal.
    with schema.cursor() as cur:
        cur.execute("UPDATE public.record_cursor SET seq = 9 "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
    fifth = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert fifth.seq == 9, (
        f"the re-seed moved the counter BACKWARDS to {fifth.seq} — positions "
        f"1..9 would be reissued")

    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    with pytest.raises(ra.SeedRefused) as caught:
        ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    assert caught.value.reason == "armed", (
        "re-seeding a graph that is already allocating must be refused")
    app.rollback()
    assert _counter(schema, org, graph) == (9, "active"), (
        "the refused re-seed changed the counter")


def test_a_seed_without_the_guc_is_a_refusal_not_a_divergence(schema, tenant, app):
    """FINDING (round 2, blocking): a seed under RLS with no
    `app.current_org_id` raises `42501` — and the plan's read-back then reported
    it as a DIVERGENCE, sending the operator to the wrong remedy.

    Both halves are asserted: the RAW statement really is refused by the policy
    (so the failure is real), and `seed()` — which sets and verifies the tenant
    itself — succeeds on the same connection with no GUC supplied by the caller.
    """
    org, graph = tenant
    psycopg2 = pytest.importorskip("psycopg2")
    with app.cursor() as cur, pytest.raises(psycopg2.errors.InsufficientPrivilege):
        cur.execute(ra.SEED_SQL, (org, graph, 0, org, graph))
    app.rollback()

    result = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    app.commit()
    assert result.mode == ra.MODE_SEEDED, (
        "the documented seed path must set the tenant itself — an unset GUC is a "
        "refusal, never a divergence")


# ── the allocation ──────────────────────────────────────────────────────────

def test_a_batch_writes_distinct_payloads_in_one_statement(schema, tenant, app):
    """FINDING (round 2): `generate_series` without `unnest(...) WITH ORDINALITY`
    emits THE SAME payload N times, so the "one statement" claim did not carry N
    distinct records.

    Three records must produce three DISTINCT payloads on three consecutive
    positions, in journal order.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    records = _records(3)
    write = rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
        records, org_id=org, graph_id=graph, allocate=True, conn=app)
    app.commit()
    assert (write.first_seq, write.last_seq) == (1, 3)

    rows = _journal_rows(schema, org, graph)
    assert [row[0] for row in rows] == [1, 2, 3]
    payload_ids = [row[2]["id"] for row in rows]
    assert payload_ids == [record["id"] for record in records], (
        f"the payloads did not land in the records' order: {payload_ids}")
    assert len(set(payload_ids)) == 3, (
        "the batch wrote ONE payload three times — the ordinality pairing is gone")


def test_the_allocated_number_reaches_the_watermark_and_the_byte_meter(
        schema, tenant, app):
    """FINDING (round 2, blocking): `replay()` DISCARDS each statement's result,
    and both `last_seq` and the byte meter used the CALLER's `first_seq`, so an
    allocated number never reached them — the meter matched ZERO rows and charged
    0, silently, under a contract that says it never fails a write.

    The counter starts at 5 (the record already holds 1..5), so a caller-supplied
    `first_seq` of 1 would measure the WRONG rows: the meter is non-zero only if
    the number the allocator issued is the number it measured.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3, 4, 5])
    _seed_and_arm(app, org, graph, 5)
    write = rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
        _records(2), org_id=org, graph_id=graph, allocate=True, conn=app)
    app.commit()

    assert (write.first_seq, write.last_seq) == (6, 7), (
        f"the watermark is not the allocated block: "
        f"{write.first_seq}..{write.last_seq}")
    assert write.written_rows == 2, (
        f"the byte meter matched {write.written_rows} rows for a 2-record write "
        f"at 6..7 — it is measuring the caller's positions, not the allocated ones")
    assert write.written_bytes > 0, (
        "the byte meter charged ZERO for two committed journal rows")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3, 4, 5, 6, 7]


def test_a_cut_over_DOOR_still_writes_when_the_journal_has_a_position(
        schema, tenant, app, monkeypatch):
    """THE DOOR SHAPE (#12894 review cycle 1, P1). Every production door
    (`api.py`, `mining.py`, `sdk.py`) calls the live door WITH the ordinal
    `log.append` reported, so a cut-over deployment is
    `write_live_record(record, seq=<n>, allocate=True)` — not
    `seq=None`. The door used to forward that ordinal as `first_seq`, and
    `replay_plan` REFUSES `allocate=True` with a caller watermark
    (`start_seq must be left at 1`), so every cut-over write would have been
    refused; the wiring gate blessed the body that did it.

    Driven through the DOOR rather than `RecordWriter` because that is where the
    forwarding lived — a `RecordWriter(allocate=True)` test cannot see it.
    """
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    # The door opens its OWN connection from the arming env; that is the
    # shape production has, and the shape whose forwarding was wrong.
    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    write = rw.write_live_record(RECORD, seq=7, allocate=True,
                                 tenant=(org, graph))

    assert write is not None and write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"a cut-over door refused a write that carried a journal position: "
        f"{write!r}")
    # The ALLOCATED number is reported, not the journal's 7: allocation is what
    # `allocate=True` is FOR, and the ordinal the append reported has no meaning
    # on this arm.
    assert (write.first_seq, write.last_seq) == (1, 1), (
        f"the door reported {write.first_seq}..{write.last_seq}, not the "
        f"allocated block")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1]


def test_a_duplicate_position_fails_the_transaction(schema, tenant, app):
    """FINDING (round 1, B5): the position insert's `ON CONFLICT … DO NOTHING`
    absorbed a duplicate WITHOUT an error while the counter had already advanced:
    the layer rows landed with no idempotency key and the payload was dropped
    permanently.

    The allocated insert has no conflict clause. A counter behind the table
    (a restore that missed it, a purge) is a `23505` that FAILS the transaction,
    and the increment rolls back with it.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])
    _seed_and_arm(app, org, graph, 2)
    with schema.cursor() as cur:
        # Force the counter BEHIND the table: the next allocation hands out 3,
        # which the table already holds. (A privileged setup write: the app role
        # is RLS-bound and cannot move a counter it does not know the tenant of.)
        cur.execute("UPDATE public.record_cursor SET seq = 2 "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
        cur.execute("INSERT INTO public.journal_event "
                    "(org_id, graph_id, seq, event_type, payload) "
                    "VALUES (%s, %s, 3, 'PointAdded', '{}')", (org, graph))

    with pytest.raises(rw.RecordWriteError) as caught:
        rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
            _records(1), org_id=org, graph_id=graph, allocate=True, conn=app)
    assert "PositionCollision" in str(caught.value) or "23505" in str(caught.value), (
        f"the duplicate was not named as a collision: {caught.value}")
    assert ra.metrics()["duplicate_position"] >= 1
    app.rollback()
    assert _counter(schema, org, graph) == (2, "active"), (
        "the counter increment did NOT roll back with the failed transaction")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3], (
        "the failed transaction left a row behind")


def test_a_restored_counter_continues_past_the_rows(schema, tenant, app):
    """FINDING (round 1, B3): `record_cursor` was absent from `THE_RECORD_TABLES`,
    so a restore returned a populated `journal_event` with the allocator at 0 and
    every new position collided — and was swallowed.

    The seed reads the ROWS, so a restored graph continues at `max(seq)+1` with no
    sequence state to lose. (The dump itself lists the table because it is in
    `THE_RECORD_TABLES`; that half is pinned DB-free in
    `tests/test_12894_record_cursor.py`.)
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3, 4, 5, 6, 7, 8, 9])
    result = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=9)
    assert result.seq == 9
    app.commit()
    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    write = rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
        [RECORD], org_id=org, graph_id=graph, allocate=True, conn=app)
    app.commit()
    assert write.first_seq == 10, (
        f"a restored graph restarted its allocator at {write.first_seq} instead "
        f"of continuing past the rows it holds")


def test_an_unarmed_cursor_allocates_nothing(schema, tenant, app):
    """`seeded` means "not cut over" and `halted` means "stopped after a defect".
    Neither is a number to invent, so an allocation under either is a REFUSAL —
    and it writes neither a position nor a counter increment."""
    org, graph = tenant
    ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    app.commit()                        # seeded, NOT armed
    with pytest.raises(rw.RecordWriteError) as caught:
        rw.RecordWriter(dsn=os.environ["TEST_RECORD_DB_URI"]).write(
            [RECORD], org_id=org, graph_id=graph, allocate=True, conn=app)
    assert "CursorNotActive" in str(caught.value), caught.value
    app.rollback()
    assert _counter(schema, org, graph) == (0, "seeded")
    assert _journal_rows(schema, org, graph) == []
    assert ra.metrics()["cursor_not_active"] >= 1


# ── the isolation guard and the retry contract ──────────────────────────────

def test_a_serializable_transaction_is_refused_at_arm(schema, tenant, app):
    """FINDING (M5): under REPEATABLE READ/SERIALIZABLE the counter's blocked
    UPDATE raises `40001`, and nothing on this path retries it — so a DSN carrying
    `-c default_transaction_isolation=serializable` would turn every contended
    write into an error.

    The guard reads the TRANSACTION-scoped setting and refuses BEFORE the first
    statement: no position is issued under an isolation level that breaks
    "allocation order = commit order".
    """
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    psycopg2 = pytest.importorskip("psycopg2")
    org, graph = tenant
    # ⛔ THE DSN SHAPE THE FINDING NAMES —
    # `-c default_transaction_isolation=serializable` — not a `SET TRANSACTION`
    # issued mid-transaction (which Postgres refuses: it must come before any
    # query, so a fixture that has already queried cannot express it).
    serializable = psycopg2.connect(
        _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]),
        options="-c default_transaction_isolation=serializable")
    serializable.autocommit = False
    try:
        with pytest.raises(ra.IsolationRefused):
            ra.seed(serializable, org_id=org, graph_id=graph, enumerated_floor=0)
    finally:
        serializable.rollback()
        serializable.close()
    # 40001 is prevented, never retried: the guard's own counter is the tripwire.
    assert ra.metrics()["isolation_refused"] >= 1
    assert ra.metrics()["serialization_failure"] == 0, (
        "a 40001 was counted — the isolation guard did not refuse at arm")
    app.rollback()
    assert _counter(schema, org, graph) is None


def test_a_cancelled_allocation_rolls_the_counter_back(schema, tenant, app):
    """FINDING (M5): `57014` (a cancelled statement — a local timeout, an operator
    cancel) was not handled at all.

    This is a REAL cancellation: a 1 ms statement timeout on an allocation of
    3,000 records. The class is NAMED, COUNTED, and raised retryable — and the
    increment is gone with the aborted transaction, so a retry starts from the
    same number.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    rp.scope_connection(app, org)
    # The ALLOCATION statement alone, with a batch big enough to exceed 1 ms:
    # the class under test is the allocator's, so the timeout must land inside
    # ITS statement (a 3,000-record `RecordWriter.write` cancels on statement 2
    # of 6,001 — a layer mutation — which is a different path).
    statement = ra.allocation_statement(
        org_id=org, graph_id=graph, records=_records(20_000))
    with app.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '1ms'")
    with pytest.raises(ra.RetryableAllocationFailure) as caught:
        ra.execute_allocation(app, statement, org_id=org, graph_id=graph)
    assert caught.value.sqlstate == "57014", caught.value
    assert ra.metrics()["query_cancelled"] >= 1
    app.rollback()
    assert _counter(schema, org, graph) == (0, "active"), (
        f"a cancelled allocation burned positions: {_counter(schema, org, graph)}")
    assert _journal_rows(schema, org, graph) == []


def test_the_replay_path_keeps_its_idempotent_position_write(schema, tenant, app):
    """The `DO NOTHING` removal is scoped to the ALLOCATED insert.

    A replay numbers records by ENUMERATION and must stay idempotent (#5285:
    replay twice and compare the tables), so replaying the same segment twice is
    a no-op on the position rows — the asymmetry is deliberate, and this is what
    keeps a lane from "unifying" the two statements.
    """
    org, graph = tenant
    with app.cursor() as cur:
        cur.execute(rp.TENANT_GUC_SQL, (org,))
        cur.execute("SELECT set_config('app.current_org_id', %s, true)", (org,))
    rp.replay([RECORD], app, org_id=org, graph_id=graph, start_seq=1)
    rp.replay([RECORD], app, org_id=org, graph_id=graph, start_seq=1)
    app.commit()
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1]


def test_the_allocated_position_cannot_be_absorbed_at_all(schema, tenant, app):
    """THE ALLOCATED PATH'S ADVANTAGE OVER THE DERIVED ONE, asserted rather than
    claimed — and the reason this lane deletes its own suppression counter.

    The derived path's insert carries `ON CONFLICT … DO NOTHING`, so a position
    held by a different record was ABSORBED in silence until `#9318` (PR #12904)
    made it a typed `JournalPositionCollision` — a verdict, one tenant-scoped
    SELECT, only when the insert was absorbed. The ALLOCATED insert carries no
    `DO NOTHING` at all: a duplicate raises `23505`, the transaction fails, and
    the counter's increment rolls back with it (`test_a_duplicate_position_*`
    asserts that half). So there is nothing here to detect, count or report.

    This test pins the asymmetry from the outside: after a collision, the derived
    path still holds ONE row and raises; the allocated path cannot reach that
    state in the first place.
    """
    org, graph = tenant
    # The counter is put BEHIND the table on purpose — the shape a restore
    # produces when `journal_event` is bulk-loaded and the counter comes from an
    # older backup. `seed()` refuses to create this (its guard is `row_max <=
    # floor`), so it is staged directly, which is also what an operator's manual
    # re-base or a partial restore leaves behind.
    _seed_and_arm(app, org, graph, 0)
    _append_positions(schema, org, graph, [1])
    with schema.cursor() as cur:
        cur.execute("UPDATE public.record_cursor SET seq = 0, mode = 'active' "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
    with pytest.raises(ra.PositionCollision):
        ra.execute_allocation(
            app, ra.allocation_statement(org_id=org, graph_id=graph,
                                         records=_records(1)),
            org_id=org, graph_id=graph)
    app.rollback()
    # The counter must be untouched by the failed attempt: a 23505 that left the
    # increment behind would burn position 2 forever.
    assert _counter(schema, org, graph) == (0, ra.MODE_ACTIVE), (
        "the failed allocation advanced the counter — position 1 is burned")


def test_a_seed_on_an_armed_graph_refuses_and_names_the_condition(
        schema, tenant, app):
    """FINDING (review P2): `seed()` on an `active` cursor was reported with the
    same reason as a divergence ("divergence"), sending the operator to look for
    missing rows when the actual state is "already armed".

    A halted cursor is NOT armed: halting is the operator's brake, and re-seeding
    it is the documented way to resume (#12894's mode transitions), so that half
    re-seeds and becomes `seeded` again — asserted here so a lane cannot make
    `halt` a one-way door without failing this.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)

    with pytest.raises(ra.SeedRefused) as armed:
        ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    app.rollback()
    assert armed.value.reason == "armed", (
        f"an already-armed cursor must name `armed`, got {armed.value.reason!r}")

    ra.halt(app, org_id=org, graph_id=graph)
    app.commit()
    # ⛔ A HALTED CURSOR IS NOT `armed`, so the seed RE-RUNS (monotone, no error) —
    # but it must NOT flip the mode. `halted` is the operator's brake; a recovery
    # seed that silently lifted it would resume allocation nobody asked for. The
    # documented resume is `reopen` → `seeded` → `arm`.
    resumed = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    app.commit()
    assert (resumed.seq, resumed.mode) == (3, ra.MODE_HALTED), (
        f"a seed must not lift a halt, got {resumed.seq} {resumed.mode}")

    assert ra.reopen(app, org_id=org, graph_id=graph) == 3
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_SEEDED), (
        "`reopen` is the documented resume from `halted`")


def test_the_seed_separates_a_tail_from_a_burn(schema, tenant, app):
    """FINDING (review P2): `healed_tail` meant `seq > row_max`, which is TRUE for
    a counter ahead of everything (positions issued and their rows GONE) — the
    opposite condition, reported under the benign name.

    A tail (`floor > row_max`) is a replay that has not caught up; a burn
    (`seq > max(row_max, floor)`) is missing rows. Both are asserted, in that
    order, on the same graph.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])
    tail = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=5)
    app.commit()
    assert (tail.seq, tail.healed_tail, tail.burned) == (5, True, False), (
        f"the tail case: {tail}")

    ra.arm(app, org_id=org, graph_id=graph)
    alloc = ra.execute_allocation(
        app, ra.allocation_statement(org_id=org, graph_id=graph,
                                     records=_records(2)),
        org_id=org, graph_id=graph)
    app.commit()
    assert alloc.first_seq == 6

    # The rows for 6..7 are GONE but the counter says 7: a burn, not a tail.
    with schema.cursor() as cur:
        # `journal_event_is_append_only` refuses a DELETE unless the purge flag
        # is on; staging a BURN means staging rows that are gone.
        cur.execute("SET app.record_journal_purge = 'on'")
        cur.execute("DELETE FROM public.journal_event "
                    "WHERE org_id = %s AND graph_id = %s AND seq > 5", (org, graph))
    app.commit()
    ra.halt(app, org_id=org, graph_id=graph)
    app.commit()
    # `enumerated_floor=2` == `row_max`, so NO tail applies and the burn is
    # isolated: `floor > row_max` is false, `seq > max(row_max, floor)` is true.
    burned = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=2)
    app.commit()
    assert (burned.seq, burned.healed_tail, burned.burned) == (7, False, True), (
        f"a counter ahead of every row must report `burned`, not the benign "
        f"tail: {burned}")
