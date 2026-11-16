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
enumerated replay after cutover   `test_a_post_cutover_replay_cannot_add_a_second_position_`
  ⇒ same record at a 2nd position  `for_one_record` (refusal named; rows unchanged)
`seeded` / reopened graphs        `test_a_seeded_graph_refuses_the_replay_and_the_refusal_names_ARM`
  ⇒ mode is the REMEDY, the row   + `test_a_reopened_graph_keeps_its_consumed_numbers_`
     is the FACT                  `and_still_refuses` (`reopen` keeps `seq`)

THE DSN GATE. `TEST_RECORD_DB_URI` is the record's own Postgres (the CI job
`test-record-postgres` provisions it, and `provision.sh` makes it a SUPERUSER).
Writes go through `_app_role_dsn`, because `scope_connection` REFUSES a
superuser/BYPASSRLS/owner connection — the same gate production passes through.
A skip is visible on that job (its leg gate reds any skip), and locally the
`TEST_RECORD_DB_URI` arm is what makes this file run at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import threading
import time
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


@pytest.fixture(autouse=True)
def _release_pooled_handles():
    """Hand back every idle record connection this suite's writes opened.

    MEASURED, and it cost a flake in the job that runs these files together: the
    live door opens its connection through `_default_connect`, which POOLS it,
    and `close()` returns the handle to that module-level idle set rather than to
    the kernel. A handle left in the pool can still be holding the allocator's
    row lock, and the NEXT record suite's `_ensure_record_schema` re-applies the
    unguarded migrations (`DROP`/`CREATE POLICY`, `ALTER`) — an
    AccessExclusiveLock on `public.record_cursor`. The two deadlock:

        process A waits for AccessExclusiveLock on relation …record_cursor;
        blocked by process B, which waits for ShareLock on transaction …

    `record_writer._release_pooled_connections()` is the module's own documented
    seam for exactly this ("the TEST suite's isolation seam today"), and
    `tests/test_9558_record_write_cost.py` uses it for the same reason.
    """
    yield
    from tortoise import record_writer as _rw

    _rw._release_pooled_connections()


@pytest.fixture(scope="module")
def schema():
    """The record schema (this tree's migrations) + the byte meter, once.

    ⛔ THE THREE CALLS ARE THE **FILENAME ORDER** OF THE FILES THEY APPLY, and the
    order is LOAD-BEARING — not tidiness. The byte meter's EXECUTE grant for
    `tortoise_record_app` lives in `20261116000002_record_cursor_meter_grant.sql`,
    GUARDED on the meter's presence (`to_regprocedure(...) IS NOT NULL`), because
    the record schema and the metering schema are applied by different production
    steps. The meter's function is created by the METERING migration
    `20261114000001`, which is EARLIER by filename but is NOT in
    `RECORD_MIGRATIONS`. So on a fresh database a single record-then-metering pass
    applies `…02…` while the meter does not exist yet, the guard fires its SKIP,
    `tortoise_record_app` has no EXECUTE, and the meter raises
    `InsufficientPrivilege` -> returns `None` (TOTAL by contract) -> the org is
    charged 0 bytes.

    ⛔ THAT GRANT IS A STEP OF ITS OWN (`20261116000002`), AND THE THIRD CALL IS
    WHAT REACHES IT. It used to be the TAIL of
    `20261116000001_record_cursor_allocator.sql`, where it was unreachable: that
    file is `creates`-conditional in `RECORD_REAPPLY_STEPS` (its `CREATE POLICY`s
    take an ACCESS EXCLUSIVE lock on `public.record_cursor`, the #12399
    deadlock), so it is applied only where the table is ABSENT — skipped forever
    once the FIRST pass created it, which is before the metering migration exists.
    The third call below therefore reached nothing and the assertion at the end of
    this fixture reddened on the required `test-record-postgres` leg (run
    43810200175). `…02…` is listed with `creates=None`: it creates no relation, it
    locks only the FUNCTION, and it is re-applied on EVERY pass — which is exactly
    what makes the third call grant a meter the second call created.

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
    _ensure_record_schema(conn)      # …16…+`…02…` — the GRANT step, always re-applied
    from tortoise import postgres_bytes

    with conn.cursor() as cur:
        # `to_regclass` answers for RELATIONS; the meter is a FUNCTION, so it is
        # probed by signature (`to_regclass` on a function name returns NULL and
        # the assertion below would then read a present meter as absent).
        cur.execute(
            "SELECT to_regclass('public.record_cursor'), "
            f"to_regprocedure('public.{postgres_bytes.ROWS_RPC}(text,text,bigint[])')")
        row = cur.fetchone()
    assert row[0] is not None, "record_cursor was not created by the migrations"
    assert row[1] is not None, "the byte meter is absent — its assertions would lie"
    # ⛔ THE GRANT IS ASKED SEPARATELY, AND ONLY NOW. `has_function_privilege` on a
    # signature that does not RESOLVE raises `undefined_function` (measured:
    # `function "public.nope_fn(text,text,bigint[])" does not exist`), so folding
    # it into the SELECT above made the absent-meter case die with a raw driver
    # error — on exactly the container the assertion above exists to diagnose.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT has_function_privilege('tortoise_record_app', "
            f"'public.{postgres_bytes.ROWS_RPC}(text,text,bigint[])', 'EXECUTE')")
        granted = cur.fetchone()[0]
    assert granted is True, (
        "the write role has NO EXECUTE on the byte meter, so the meter returns "
        "None and every armed write is charged 0 bytes — this is the state "
        "`20261116000002_record_cursor_meter_grant.sql`'s guarded GRANT exists "
        "to remove, and it is skipped whenever that always-applied step runs "
        "before the metering migration has created the function")
    try:
        yield conn
    finally:
        # ⛔ CLOSE AT MODULE TEARDOWN, WHICH IS BEFORE THE NEXT FILE'S FIXTURES.
        # pytest finalizes a module's fixtures before another module's run, so
        # this closes the last window in which THIS suite could be the session
        # blocking the next suite's `_ensure_record_schema` re-apply. It is not a
        # substitute for `_release_pooled_handles` above (that one covers the
        # per-test handle the live door pools); together they mean no connection
        # this suite opened outlives it.
        with contextlib.suppress(Exception):
            conn.close()


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
    # ⛔ THE COUNTER IS SEEDED TO 6 AND THE JOURNAL'S ORDINAL IS 7 (#12894): while
    # both allocators exist they must AGREE, so the door's `seq` is the position
    # the journal really holds — not an arbitrary number. The discriminating power
    # of this test does not depend on the two disagreeing: `first_seq=7` forwarded
    # into `replay_plan` would still be refused (`start_seq must be left at 1`),
    # which is the regression this pin exists for.
    _seed_and_arm(app, org, graph, 6)
    # The door opens its OWN connection from the arming env; that is the
    # shape production has, and the shape whose forwarding was wrong.
    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    write = rw.write_live_record(RECORD, seq=7, allocate=True,
                                 tenant=(org, graph))

    assert write is not None and write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"a cut-over door refused a write that carried a journal position: "
        f"{write!r}")
    # The ALLOCATED number is reported, not the caller's `first_seq=1`: allocation
    # is what `allocate=True` is FOR. (It equals `seq` here because the two
    # allocators must agree — `test_a_divergent_journal_position_is_refused_not_`
    # `renumbered` is the leg that holds them together.)
    assert (write.first_seq, write.last_seq) == (7, 7), (
        f"the door reported {write.first_seq}..{write.last_seq}, not the "
        f"allocated block")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [7]


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

    ⛔ THE CANCELLATION IS FORCED BY A REAL LOCK WAIT, NOT BY A TINY TIMEOUT, and
    the first version of this test is why. It set `statement_timeout = 1ms` on a
    20 000-record allocation and was RIGHT HALF THE TIME — measured, 4 runs:
    fail, pass, fail, pass. Twenty thousand rows is fast enough that the two
    racers are comparable, so the test measured the machine's mood, not the
    allocator. Holding the counter's row lock from a SECOND session makes the
    wait unbounded, so a 200 ms timeout can only be reached by waiting:

    the admin session takes the row (`FOR UPDATE`, uncommitted) → this session's
    allocation BLOCKS on it → the timeout fires → `57014`.

    The class is NAMED, COUNTED and retryable, and the increment is gone with the
    aborted transaction, so a retry starts from the same number.

    ⛔ AND IT RUNS THROUGH `execute_allocation`, THE REAL ENTRY POINT. An earlier
    cut had to execute the statement directly because a 1 ms budget also covered
    `execute_allocation`'s own scope + isolation reads, so a slow scope statement
    raised a bare `QueryCanceled` and reddened the leg for the wrong reason. The
    lock-forced wait REMOVES that constraint rather than working around it: the
    timeout is no longer the thing racing, so 200 ms comfortably covers the two
    prologue statements and the cancellation can only land inside the allocation.
    """
    import psycopg2

    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    rp.scope_connection(app, org)
    blocker = psycopg2.connect(os.environ["TEST_RECORD_DB_URI"])
    blocker.autocommit = False
    try:
        with blocker.cursor() as cur:
            # The superuser session: no RLS predicate to satisfy, and the lock is
            # held until this transaction ends, which is what makes the wait real.
            cur.execute("SELECT seq FROM public.record_cursor "
                        "WHERE org_id = %s AND graph_id = %s FOR UPDATE",
                        (org, graph))
            assert cur.fetchone() is not None
        statement = ra.allocation_statement(
            org_id=org, graph_id=graph, records=_records(3))
        with app.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '200ms'")
        with pytest.raises(ra.RetryableAllocationFailure) as caught:
            ra.execute_allocation(app, statement, org_id=org, graph_id=graph)
        assert caught.value.sqlstate == "57014", caught.value
        assert ra.metrics()["query_cancelled"] >= 1
    finally:
        app.rollback()
        blocker.rollback()
        blocker.close()
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


def test_a_post_cutover_replay_cannot_add_a_second_position_for_one_record(
        schema, tenant, app, monkeypatch):
    """FINDING (review P1): the record's ordinal is ALLOCATED once a graph is cut
    over, so an ENUMERATED replay is an answer to a question already answered.

    `#9190` was reopened by owner ruling (2026-11-15, comment 6902871297): *"the
    record allocates the ordinal and the journal record CARRIES it; replay READS
    the number instead of computing it."* Before this pin, `replay()` still
    enumerated from `start_seq`, so replaying one journal event on a cut-over
    graph stored the SAME record at a SECOND position — silently:
    `ON CONFLICT (org_id, graph_id, seq) DO NOTHING` keys on `seq` and the
    enumerated `seq` was free, and the `#9318` collision guard only runs on an
    insert that was ABSORBED (this one affects a row). No error, no counter, one
    record at two positions — and the door's own refusal text recommended that
    replay as the remedy.

    EXECUTED, on the shape the finding names: the door writes the record at the
    ALLOCATED number (4, from a counter at 3), then the same record is replayed
    enumerated at 9. The replay must be REFUSED by name, and the position rows must
    still be exactly the ones the door wrote — which is the half that fails if the
    guard is removed (a row at 9 would appear here with no exception at all).

    ⚠️ The door is given `seq=4`, not 9: since #12894's cutover invariant the
    door's journal ordinal and the allocated number must AGREE, and a divergent one
    is refused at the door (`test_a_divergent_journal_position_is_refused_not_`
    `renumbered`). The replay below still uses 9 — a position the record does not
    hold — which is exactly the second-position shape this pin is about.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    app.commit()
    # The live door opens its OWN connection, so it goes through the role gate
    # (`scope_connection` refuses a BYPASSRLS DSN); see the sibling test above.
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    write = rw.write_live_record(
        RECORD, durable=True, seq=4, tenant=(org, graph), allocate=True)
    assert write is not None and write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"the allocated door write did not apply: {write}")
    assert (write.first_seq, write.last_seq) == (4, 4), (
        f"the door did not take the allocated number: {write.first_seq}")
    written = [row[0] for row in _journal_rows(schema, org, graph)]
    assert written == [1, 2, 3, 4], f"the door wrote {written}"

    # THE DERIVED REPLAY OF THE SAME RECORD AT A POSITION IT DOES NOT HOLD (9):
    # refused, by name, before a single position statement.
    with pytest.raises(rp.RecordProjectionError) as refused:
        rp.replay([RECORD], app, org_id=org, graph_id=graph, start_seq=9)
    app.rollback()
    message = str(refused.value)
    for needle in ("ENUMERATED replay is REFUSED", "ISSUED", "record_cursor",
                   "#9190", "allocate=True"):
        assert needle in message, (
            f"the refusal does not name {needle!r}: {message}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == written, (
        "a post-cutover replay added a position row for a record the allocator "
        f"had already numbered: {[row[0] for row in _journal_rows(schema, org, graph)]} "
        f"vs the door's {written}")


def test_a_seeded_graph_refuses_the_replay_and_the_refusal_names_ARM(
        schema, tenant, app):
    """FINDING (cycle-1 P2, answered WITHOUT the exemption cycle-2 measured wrong):
    a `seeded` graph's enumerated replay is refused, and the refusal must name a
    verb that RUNS from that state.

    ⛔ THE EXEMPTION WAS TRIED AND IS WRONG. It was argued from §A7.5.5 step
    (0)'s *"a row in mode 'seeded' is NOT [cut over]"* — a sentence about
    RE-SEEDABILITY — and §A7.7's `halted → seeded` ("abandoning the counter") is
    `REOPEN_SQL`: `SET mode='seeded'` KEEPING `seq`. So the mode cannot decide
    this guard; see
    `test_a_reopened_graph_keeps_its_consumed_numbers_and_still_refuses`.
    What cycle 1 DID establish is narrower and is what this pins: the refusal
    named `allocate=True` and a seed, and BOTH refuse on a `seeded` graph
    (`ALLOCATION_SQL` requires `mode = 'active'`). A `seeded` graph is not a dead
    end — `arm` is its writer, and it is the cutover's own 6.3 step — so the
    message must name `arm`.

    Non-vacuity: the pre-fix message carries the generic sentence ("go through
    the allocator (allocate=True) or a seed"), which the second assertion
    forbids, and it never names `arm` for this mode.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])
    seeded = ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=2)
    app.commit()
    assert (seeded.seq, seeded.mode) == (2, ra.MODE_SEEDED), (
        f"the seed did not leave the graph SEEDED at its floor: {seeded}")

    with pytest.raises(rp.RecordProjectionError) as refused:
        rp.replay([RECORD], app, org_id=org, graph_id=graph, start_seq=3)
    app.rollback()
    message = str(refused.value)
    for needle in ("ENUMERATED replay is REFUSED", "SEEDED", "ARM it",
                   "seeded → active"):
        assert needle in message, (
            f"the `seeded` refusal does not name {needle!r}, so the operator is "
            f"sent to a verb that refuses rather than to the arming step: {message}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2], (
        "a `seeded` graph's replay wrote a position row: "
        f"{[r[0] for r in _journal_rows(schema, org, graph)]}")
    assert _counter(schema, org, graph) == (2, ra.MODE_SEEDED)

    # AND `arm` IS the writable path, so the state is a gate and not a dead end:
    # after arming, the allocator issues the next position and the door applies.
    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    assert _counter(schema, org, graph) == (2, ra.MODE_ACTIVE)
    allocated = rp.replay([RECORD], app, org_id=org, graph_id=graph,
                          allocate=True)
    app.commit()
    assert allocated.first_seq == 3, (
        f"the armed graph did not allocate past its floor: {allocated}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3]


def test_a_reopened_graph_keeps_its_consumed_numbers_and_still_refuses(
        schema, tenant, app, monkeypatch):
    """⛔ CYCLE-2 P1, MEASURED: `reopen()` moves `halted → seeded` and KEEPS
    `seq`, so a reopened graph's ordinals were issued and consumed even though its
    mode reads `seeded`. A `seeded` exemption in the enumerated guard let the same
    payload land at a SECOND position, silently.

    The shape, end to end: the door allocates and writes the record at 4; the
    counter is halted and reopened to `(4, 'seeded')`; an enumerated replay at a
    position the record does NOT hold (9) must be REFUSED, and position 9 must NOT
    exist. Pre-fix this replay SUCCEEDED and `journal_event` held the record at 4
    AND 9.

    ⚠️ The door is given `seq=4` (not 9): since #12894's cutover invariant the
    journal ordinal and the allocated number must agree at the door, and a
    divergent one is refused there rather than numbered here.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    app.commit()
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    write = rw.write_live_record(
        RECORD, durable=True, seq=4, tenant=(org, graph), allocate=True)
    assert write is not None and write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"the allocated door write did not apply: {write}")
    assert (write.first_seq, write.last_seq) == (4, 4), (
        f"the door did not take the allocated number: {write.first_seq}")

    ra.halt(app, org_id=org, graph_id=graph)
    ra.reopen(app, org_id=org, graph_id=graph)
    app.commit()
    assert _counter(schema, org, graph) == (4, ra.MODE_SEEDED), (
        "`reopen` must keep the consumed `seq` — that is the whole hazard: "
        f"{_counter(schema, org, graph)}")

    with pytest.raises(rp.RecordProjectionError) as refused:
        rp.replay([RECORD], app, org_id=org, graph_id=graph, start_seq=9)
    app.rollback()
    assert "ENUMERATED replay is REFUSED" in str(refused.value)
    written = [row[0] for row in _journal_rows(schema, org, graph)]
    assert written == [1, 2, 3, 4], (
        "a REOPENED graph — `seeded` with consumed numbers — stored the same "
        f"record at a second position: {written}")


def test_the_printed_counter_repair_chain_terminates_allocatable(
        schema, tenant, app):
    """⛔ FRESH-CONTEXT REVIEW, P2: every refusal PRINTS a repair chain, and until
    this test each chain was pinned only by needle PRESENCE — so a chain that
    named real verbs in an order that cannot run was green. Three of them were
    measured wrong on the same day: `halt → re-seed → arm` (stops at `halted`),
    "`allocate=True` … and both REFUSE from here" (`ALLOCATION_SQL` is scoped
    `AND mode='active'`, so it runs), and "`… and reopen`" as the last step
    (leaves `seeded`, where every allocation raises `CursorNotActive`).

    This test EXECUTES the chain instead of reading it. It is the falsifier the
    needle tests cannot be: it fails for any remedy that does not reach an
    allocating counter, whatever words it chooses.

    The chain under test is the one `PositionCollision` and
    `_ALLOCATOR_REMEDY['duplicate_position']` print for a counter that is BEHIND
    the table: `halt → re-seed → reopen → arm`.

    Non-vacuity: dropping `reopen` (the pre-fix wording) reds at the `arm` call
    with `RecordAllocatorError`, and dropping `arm` (the other pre-fix wording)
    reds at the allocation with `CursorNotActive`.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_ACTIVE)

    # ── step 1: halt (`active → halted`) ── the only transition out of `active`
    ra.halt(app, org_id=org, graph_id=graph)
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_HALTED)

    # ── step 2: re-seed (monotone GREATEST) — and it must NOT move the mode
    ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=3)
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_HALTED), (
        "`seed` is not a transition: it sets `seq`/`updated_at` only, so the "
        f"mode must still read `halted`: {_counter(schema, org, graph)}")

    # ── ⛔ AND `arm` REFUSES HERE — this is why `reopen` is in the chain.
    with pytest.raises(ra.RecordAllocatorError):
        ra.arm(app, org_id=org, graph_id=graph)
    app.rollback()
    assert _counter(schema, org, graph) == (3, ra.MODE_HALTED), (
        "a refused `arm` moved the counter, which would make the refusal a "
        f"partial transition: {_counter(schema, org, graph)}")

    # ── step 3: reopen (`halted → seeded`), keeping the consumed `seq`
    ra.reopen(app, org_id=org, graph_id=graph)
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_SEEDED)

    # ── step 4: arm (`seeded → active`) — the ONLY writer back
    ra.arm(app, org_id=org, graph_id=graph)
    app.commit()
    assert _counter(schema, org, graph) == (3, ra.MODE_ACTIVE)

    # ── the chain TERMINATES ALLOCATABLE: the counter is `active` and its `seq`
    #    covers the journal, so the next door write takes 4 rather than colliding.
    assert _counter(schema, org, graph)[0] >= max(
        row[0] for row in _journal_rows(schema, org, graph)), (
        "the chain left the counter BEHIND the table — the first allocation "
        f"after it would be a `23505`: counter={_counter(schema, org, graph)}, "
        f"journal={[r[0] for r in _journal_rows(schema, org, graph)]}")

    allocated = rp.replay([RECORD], app, org_id=org, graph_id=graph,
                          allocate=True)
    app.commit()
    assert allocated.first_seq == 4, (
        f"the repaired counter did not issue the next position: {allocated}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3, 4]


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


def test_the_live_door_takes_the_allocated_number_over_the_journals(
        schema, tenant, app, monkeypatch):
    """FINDING (review P1): `write_live_record(seq=…)` forwarded the JOURNAL's
    ordinal as the plan's `first_seq`, and `replay_plan` refuses
    `allocate and start_seq != 1` — so at the documented cutover every armed write
    after position 1 came back `REFUSED refusal="write_failed"`, attributed to a
    remedy ("a replay heals the record") that cannot apply, while
    `tools/record_wiring_check.py` CERTIFIED the same call shape.

    All three production doors pass `seq=` (that is what `EventLog.append` gives
    them, #9190), so the wiring, not the caller, was wrong: with
    ``allocate=True`` the record's counter issues the number and the journal's own
    ordinal stays in the journal FILE, whose coordinate it is.

    The falsifier drives the REAL live door with the journal ordinal the record
    really holds — 4, on a graph whose counter is at 3. The write must APPLY at 4
    and the position row must BE 4; a forwarded `first_seq` reds this test twice
    over (a refusal, and a position of 9 if the refusal were ever relaxed).

    ⛔ AND THE DIVERGENT SHAPE IS NOW A REFUSAL, deliberately (#12894's cutover
    invariant): a journal ordinal of 9 against a counter at 3 means the journal and
    the counter disagree about how long the journal is, and writing at 4 would put
    the line at 9 and the row at 4. That half lives in
    `tests/test_12894_record_cursor_live.py::
    test_a_divergent_journal_position_is_refused_not_renumbered` (the same file —
    an earlier cut of this pointer named a
    `tests/test_12894_live_allocator_armed.py` that does not exist); it was
    previously
    asserted HERE as an APPLIED write at 4, which pinned the divergence this change
    removes.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    app.commit()
    # The live door resolves its DSN from the env (that is the arming seam the
    # production doors use); the tenant comes from the explicit argument, so no
    # single-tenant declaration is needed.
    # ⛔ THE APP ROLE, NOT THE ADMIN DSN. The live door opens its OWN connection,
    # so it goes through the same role gate as every other caller: a superuser
    # DSN is refused by `scope_connection` (BYPASSRLS), and the refusal surfaces
    # as `write_failed` — which would make this test fail for a reason that has
    # nothing to do with the ordinal.
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))

    write = rw.write_live_record(
        RECORD, durable=True, seq=4, tenant=(org, graph), allocate=True)
    assert write is not None, "the live door returned nothing (an unarmed read?)"
    assert write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"a cutover write with the journal's real ordinal refused: outcome="
        f"{write.outcome} refusal={write.refusal!r} — `first_seq` is still "
        f"carrying the journal's number into the plan")
    assert (write.first_seq, write.last_seq) == (4, 4), (
        f"the watermark is not the allocated position: "
        f"{write.first_seq}..{write.last_seq}")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3, 4], (
        "the position row is not the allocated number")


def test_a_mis_armed_counter_is_refused_with_a_remedy_that_applies(
        schema, tenant, app, monkeypatch, caplog):
    """FINDING (review cycle 3, P2): the live door reported every allocator
    refusal as ``refusal="write_failed"`` — the token a genuine record failure
    carries — and its WARNING said *"a replay heals the record"*, which is false
    for both of them: a replay neither arms a cursor nor gets past `#9318`'s
    collision refusal. The docstring claimed ``refusal`` distinguished them; it
    does not, and now says so.

    Both halves asserted: the token stays in the closed vocabulary (`write_failed`
    — a new token would be a surface change), and the LOG names the state and the
    remedy that applies. A "heals the record" line here reds this test.
    """
    org, graph = tenant
    # `seeded`, NOT armed: the counter exists and refuses to issue.
    ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
    app.commit()
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    before = ra.metrics()["cursor_not_active"]
    caplog.clear()
    write = rw.write_live_record(
        RECORD, durable=True, seq=2, tenant=(org, graph), allocate=True)
    assert write is not None and write.outcome is rw.RecordWriteOutcome.REFUSED
    assert write.refusal == "write_failed", (
        f"the refusal token moved out of the closed vocabulary: {write.refusal!r}")
    assert ra.metrics()["cursor_not_active"] == before + 1
    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "ORDINAL ALLOCATOR refused" in text, (
        f"the log does not name the allocator: {text[-400:]!r}")
    assert "seed" in text and "arm" in text, (
        f"the remedy for a `seeded` cursor is seed-then-arm, not replay: "
        f"{text[-400:]!r}")
    assert "a replay heals the record" not in text, (
        "the generic lost-write remedy was emitted for a mis-armed counter — a "
        "replay cannot arm a cursor")


# ══════════════════════════════════════════════════════════════════════════════
# #12894 — THE WIRE: the live door ALLOCATES (flag `TORTOISE_RECORD_ALLOCATE`)
# ══════════════════════════════════════════════════════════════════════════════
#
# WHY THIS SECTION EXISTS. `docs/evidence/12894-landed-verify/README.md` V2 proved,
# by a call-site census, that the allocator chain that landed is SCAFFOLDING: every
# production door passes a `seq` it did not issue and none passes `allocate=`, so
# `allocate=True` was exercised by tests only and `write_live_record`'s allocated
# arm was inert. A green suite that never enters that arm is not evidence about the
# live path — which is the whole finding. Every test below therefore drives **the
# live door** (`write_live_record`) or **the seed verb**, not `RecordWriter` and not
# the plan builder alone, except where the fact being asserted is the allocator's
# own (`record_allocator` is the subject and the door is the caller).
#
# THE SIX FALSIFIERS (the brief's list, in its order):
#
#   (3a) concurrent writers, order = commit order   `test_two_writers_allocate_in_commit_order`
#   (3b) a rollback burns nothing, reuses nothing   `test_a_rolled_back_allocation_is_reissued_exactly_once`
#   (3c) replay READS the carried ordinal, refuses  `test_replay_reads_a_carried_ordinal` +
#        on `carried != start_seq + i`              `test_replay_refuses_a_carried_ordinal_that_disagrees`
#   (3d) a journal line and its row carry the same  `test_the_armed_row_carries_the_journals_position` +
#        number                                     `test_a_divergent_journal_position_is_refused_not_renumbered`
#   (3e) a truncated journal does not re-mint       `test_a_shrunken_journal_cannot_mint_a_position`
#   (3f) `TORTOISE_JOURNAL_SEGMENTS=1` after a seal `test_a_sealed_journal_is_gated_on_the_armed_arm`
#
# ⛔ THEY LIVE IN THIS FILE, NOT A NEW ONE, on purpose: the schema/tenant/app harness
# (and the pooled-handle teardown that keeps this suite from deadlocking the NEXT
# suite's migration re-apply) is defined here, and re-exporting it into a second
# module is both an F811 lint failure and a second copy of a harness that must not
# drift. A new test FILE would also owe a `config/ci-surfaces.yml` durations row.


# ── harness for the wire section ────────────────────────────────────────────

def _door_dsn() -> str:
    from tests.test_9078_record_written_by_production_path import _app_role_dsn as dsn

    return dsn(os.environ["TEST_RECORD_DB_URI"])


def _app_conn():
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_door_dsn())
    conn.autocommit = False
    return conn


def _arm_door(monkeypatch, *, flag: bool) -> None:
    """Put the live door in the shape production has: its own DSN, from the env.

    The DSN is ALWAYS set — the flag is the only variable between the two arms,
    so a test cannot pass because the door was unarmed (`write_live_record`
    returns `None` there, which would make every `is not None` assertion fail
    loudly rather than quietly).
    """
    monkeypatch.setenv(rw.RECORD_DSN_ENV, _door_dsn())
    if flag:
        monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    else:
        monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)


def _journal_file(tmp_path, n: int):
    """A REAL journal file, written by `EventLog` itself (the reader the seed uses)."""
    from tortoise.log import EventLog

    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    for i in range(n):
        log.append({**RECORD, "id": f"p-journal-{i}",
                    "point": {"id": f"p-journal-{i}", "content": f"c{i}",
                              "pointKind": "claim"}})
    return path


# ── (1) the seed is REACHABLE, and correct on both populations ──────────────

def test_the_seed_verb_seeds_a_graph_from_its_own_journal(schema, tenant, tmp_path):
    """V5 item 2: `seed()` had no operator door — a code path with no door.

    The floor is the JOURNAL's own enumeration (`EventLog.read_all`, the read the
    replay engines use), not a number this tool counted off newlines: the number
    becomes a `seq` live writes key on, so a second reader is a second answer to
    "how long is the journal".
    """
    from tools import record_allocator_cli as cli

    org, graph = tenant
    path = _journal_file(tmp_path, 3)

    code = cli.main(["seed", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(), "--journal", str(path),
                     "--json"])
    assert code == 0, "the seed verb refused a graph it should have seeded"
    assert _counter(schema, org, graph) == (3, "seeded"), (
        "a journal with 3 records and no position rows must seed the counter to "
        "3 — the next write is 4")


def test_a_seed_from_a_journal_with_no_rows_gives_the_first_write_n_plus_1(
        schema, tenant, app, monkeypatch):
    """THE ROUND-2 P0, driven through the DOOR (plan case 2a / `§A7.5.5`).

    A journal with N records and ZERO `journal_event` rows is the population the
    seed exists for: `row_max = 0`, `floor = N`, so `seq = GREATEST(0, N) = N` and
    the FIRST live write is `N + 1`. Under a `COALESCE(row_max, 0)` seed this
    graph would start at 1 and every write would collide with an existing row.

    Driven through the FLAG rather than `allocate=True`, because the flag is what a
    deployment sets: seed → arm → the next journal append's position is N+1 and the
    door's allocated number is the same N+1.
    """
    org, graph = tenant
    seeded = _seed_and_arm(app, org, graph, 3)
    assert (seeded.seq, seeded.row_max, seeded.healed_tail) == (3, 0, True), (
        f"the seed did not lift a zero-row graph to its journal's floor: {seeded}")

    _arm_door(monkeypatch, flag=True)
    write = rw.write_live_record(RECORD, durable=True, seq=4,
                                 tenant=(org, graph))
    assert write is not None and write.applied, f"the door refused: {write!r}"
    assert (write.first_seq, write.last_seq) == (4, 4)
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [4], (
        "the first new position is not N+1")
    # ⛔ THE DISCRIMINATOR, as on its two sibling legs. Counter 3 + `seq=4` and the
    # enumeration's own 4 produce the SAME row and watermark, so without this line
    # the leg stays green with the flag wiring removed entirely (#12894 review
    # cycles 4 and 5, P2). Seed 3 → allocation issues 4, and only the allocated
    # arm moves the counter.
    assert _counter(schema, org, graph) == (4, "active"), (
        "this leg did not allocate — it would pass on the enumerated arm too")


def test_status_position_divergence_is_the_doors_condition(
        schema, tenant, app, tmp_path, capsys):
    """⛔ THE DOOR'S PAIR, NOT THE SEED'S — and both directions (review cycle 5, P1).

    `divergence` answers the SEED's question (`row_max > floor`) and can read
    False while the door refuses every write. `position_divergence` must be the
    DOOR's own test. The door issues `cursor + 1` and compares it with the
    position the append reported (`floor + 1`), so it ACCEPTS iff `cursor ==
    floor` — the `+ 1` cancels.

    ⛔ AN OFF-BY-ONE HERE IS WORSE THAN NO FIELD AT ALL: `cursor + 1 != floor`
    reads True on a healthy armed graph and False on the wedged one, while the
    door's WARNING names this field as authoritative. Both directions are pinned
    below, healthy first — a one-sided leg would have passed the inverted version.
    """
    from tools import record_allocator_cli as cli

    org, graph = tenant
    _seed_and_arm(app, org, graph, 3)          # counter 3; journal count 3: AGREE

    healthy_dir = tmp_path / "healthy"
    healthy_dir.mkdir()
    assert cli.main(["status", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(),
                     "--journal", str(_journal_file(healthy_dir, 3)),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seq"] == 3 and payload["mode"] == "active", (
        f"the fixture did not arm a counter at 3: {payload}")
    assert payload["position_divergence"] is False, (
        f"a healthy armed graph was reported as divergent — the door's condition "
        f"is inverted here, and the door's WARNING names this field: {payload}")

    # ONE refused write: the journal has a 4th line, the counter is still 3. This
    # is the permanent-wedge state the field exists to expose.
    wedged_dir = tmp_path / "wedged"
    wedged_dir.mkdir()
    assert cli.main(["status", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(),
                     "--journal", str(_journal_file(wedged_dir, 4)),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["position_divergence"] is True, (
        f"the wedged state read as healthy — an operator following the door's "
        f"WARNING would be told there is nothing to diagnose: {payload}")


def test_the_seed_verb_refuses_a_divergence_and_names_it(schema, tenant, tmp_path):
    """`SeedRefused(reason='divergence')`, reached through the VERB.

    The record holds position 3; the journal enumerates 2. Lifting to the
    enumerated floor would claim positions the journal cannot reproduce, so the
    seed writes NO row — and the reason has to survive to the operator, because
    `divergence` and `armed` have opposite remedies.
    """
    from tools import record_allocator_cli as cli

    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    path = _journal_file(tmp_path, 2)

    code = cli.main(["seed", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(), "--journal", str(path)])
    assert code == 1, "the seed verb accepted a graph whose record is ahead of its journal"
    assert _counter(schema, org, graph) is None, (
        "a refused seed must leave NO counter row: absence IS 'not cut over'")


def test_status_answers_the_divergence_remedy_beside_the_record(
        schema, tenant, tmp_path, capsys):
    """THE REMEDY MUST BE ABLE TO ANSWER ITS OWN QUESTION (fresh-context review, P2).

    The door's divergence WARNING tells an operator to check the journal's
    enumeration, so `status` must report the journal's count and the two derived
    booleans that name which seed outcome they imply.

    ⚠️ PROVENANCE CORRECTED (#12894 review cycle 5, P2). This docstring
    originally claimed `status` "used to accept `--journal` and ignore it". That
    was FALSE: `main()` has resolved `--journal` through `_journal_floor()` into
    `args.floor` since the verb was created, and `_cmd_status` already emitted
    `journal_enumerated_floor`/`divergence`/`healed_tail`. So this leg is a
    REGRESSION PIN of behaviour that already worked — worth keeping, but it
    proves nothing about the cycle-4 finding, which was that `divergence` answers
    the SEED's pair and can read `false` while the door refuses. That condition is
    `position_divergence`, pinned separately by
    `test_status_position_divergence_is_the_doors_condition`.
    """
    from tools import record_allocator_cli as cli

    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])

    # 3 journal records against 2 position rows: the `None`-position TAIL.
    tail = _journal_file(tmp_path, 3)
    assert cli.main(["status", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(), "--journal", str(tail),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal_row_max"] == 2
    assert payload["journal_enumerated_floor"] == 3
    assert payload["healed_tail"] is True and payload["divergence"] is False, (
        f"a tail was not named as a tail: {payload}")

    # 2 journal records against 3 position rows: the DIVERGENCE the door refuses
    # on. Same command, opposite verdict — which is the point of printing both.
    _append_positions(schema, org, graph, [3])
    short = _journal_file(tmp_path / "short", 2)
    assert cli.main(["status", "--org-id", org, "--graph-id", graph,
                     "--dsn", _door_dsn(), "--journal", str(short),
                     "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal_row_max"] == 3
    assert payload["divergence"] is True and payload["healed_tail"] is False, (
        f"a divergence was not named as a divergence: {payload}")


def test_the_seed_verb_refuses_to_guess_the_floor(schema, tenant):
    """`0` is a real answer for an empty graph, so it cannot be a DEFAULT.

    A defaulted floor would seed a populated graph to 0 and the next allocation
    would collide with row 1 — the failure mode this refusal exists to prevent.
    """
    from tools import record_allocator_cli as cli

    org, graph = tenant
    with pytest.raises(SystemExit) as raised:
        cli.main(["seed", "--org-id", org, "--graph-id", graph,
                  "--dsn", _door_dsn()])
    assert raised.value.code == 2, "a seed with no floor must be a USAGE refusal"


# ── (2) the flag: BOTH arms, and the meter ──────────────────────────────────

def test_the_flag_arms_the_door_and_the_row_carries_the_allocated_number(
        schema, tenant, app, monkeypatch):
    """The switch, turned on. Before this, `allocate` was False on every lane and
    the row's position came from `EventLog.append` (V2 item 3-4)."""
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=1,
                                 tenant=(org, graph))
    assert write is not None and write.applied, (
        f"the flag-armed door refused a write on an ARMED counter: {write!r}")
    assert (write.first_seq, write.last_seq) == (1, 1), (
        "the reported watermark is not the allocated position")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1]
    # ⛔ THE ASSERTION THAT MAKES THIS LEG DISCRIMINATE THE SWITCH. The two arms
    # produce the SAME row and the SAME watermark here (counter at 0 + `seq=1`
    # versus the enumeration's own 1), so without this line the leg stays green
    # with the flag wiring removed entirely (#12894 review cycle 4, P2). The
    # counter is the one value only the allocated arm moves — the flag-OFF leg
    # below asserts it is untouched, and this is the mirror of that assertion.
    assert _counter(schema, org, graph) == (1, "active"), (
        "the flag-armed door did not issue the position from the counter — "
        "the row and watermark alone cannot tell the two arms apart")


def test_the_flag_off_keeps_the_enumerated_arm(schema, tenant, monkeypatch):
    """The DEFAULT is unchanged: no flag ⇒ the door writes the journal's ordinal.

    On a graph with no counter row the enumerated arm is the only one that can
    apply — and that is exactly what must keep happening for every deployment
    that has not been cut over.
    """
    org, graph = tenant
    _arm_door(monkeypatch, flag=False)

    write = rw.write_live_record(RECORD, durable=True, seq=4,
                                 tenant=(org, graph))
    assert write is not None and write.applied, f"the default arm refused: {write!r}"
    assert (write.first_seq, write.last_seq) == (4, 4)
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [4]
    assert _counter(schema, org, graph) is None, (
        "the unarmed-flag arm allocated a position — the switch is not off")


def test_the_armed_door_charges_real_bytes(schema, tenant, app, monkeypatch):
    """The review's P0, driven through the DOOR: the byte meter charged 0 because
    the watermark came from the caller's `first_seq` and matched no row.

    ⛔ A ZERO HERE IS THE DEFECT, not a quiet pass: `written_bytes == 0` is what
    the pre-fix code reported for every allocated write.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=1,
                                 tenant=(org, graph))
    assert write is not None and write.applied
    assert write.written_rows >= 1, (
        f"the meter matched no rows for an allocated write: rows="
        f"{write.written_rows} bytes={write.written_bytes}")
    assert write.written_bytes > 0, (
        "the byte meter charged 0 for an allocated write — the watermark is not "
        "the allocated position")
    # The same discriminator as the flag leg: the row and the byte count are
    # identical on the enumerated arm, so the counter is what proves the
    # allocation actually ran (#12894 review cycle 4, P2).
    assert _counter(schema, org, graph) == (1, "active"), (
        "the byte-meter leg did not move the counter — it would pass on the "
        "enumerated arm too")


def test_the_armed_door_refuses_a_record_whose_position_is_unproven(
        schema, tenant, app, monkeypatch):
    """`seq is None` is the `#9307` "append succeeded, position not proven" state.

    On the enumerated arm it writes the layer rows and NO position row. On the
    ALLOCATED arm there is nothing to verify the issued number against, so the
    write is REFUSED rather than allocating a position the journal cannot
    corroborate. ⛔ AND DO NOT CALL IT "one record, healed by a replay" — on the
    ARMED arm a replay is not the repair: the journal consumed a position the
    counter did not, so every later armed write refuses too, and an enumerated
    replay is REFUSED on a cut-over graph
    (`record_projection._refuse_an_enumerated_replay`). The only repair is the
    allocator's operator path; the door's WARNING names it.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=None,
                                 tenant=(org, graph))
    assert write is not None and not write.applied, (
        "the armed door allocated a position for a record whose journal "
        "position was never proven")
    assert _journal_rows(schema, org, graph) == []


# ── (3a) concurrency: allocation order IS commit order ─────────────────────

def test_two_writers_allocate_in_commit_order(schema, tenant, app):
    """The property a bare `nextval()` cannot give (module docstring: a sequence is
    not transactional and its order is not the commit order).

    Writer A allocates and HOLDS. Writer B allocates and must BLOCK on the
    counter's row lock until A commits — then receive the NEXT number. The
    assertion is both halves: distinct positions, and B's number is A's + 1 with
    B finishing after A's commit.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    statement = ra.allocation_statement(org_id=org, graph_id=graph,
                                        records=[RECORD])

    first = ra.execute_allocation(app, statement, org_id=org, graph_id=graph)

    result: queue.Queue = queue.Queue()

    def writer_b() -> None:
        conn = _app_conn()
        try:
            block = ra.execute_allocation(conn, statement, org_id=org,
                                          graph_id=graph)
            result.put(("ok", block.first_seq, time.monotonic()))
            conn.commit()
        except Exception as exc:
            result.put(("error", exc, time.monotonic()))
        finally:
            conn.close()

    thread = threading.Thread(target=writer_b, daemon=True)
    thread.start()
    # B must be BLOCKED here: it cannot take the counter's row lock until A
    # commits. A fixed sleep is a measurement of "B has had time to block".
    time.sleep(1.0)
    assert result.empty(), (
        "the second writer allocated while the first still held the counter — "
        "allocation order is not commit order")
    committed_at = time.monotonic()
    app.commit()
    thread.join(timeout=30)
    assert not thread.is_alive(), "the second writer never completed"

    status, value, finished_at = result.get_nowait()
    assert status == "ok", f"the second writer failed: {value}"
    assert value == first.last_seq + 1, (
        f"the second writer received {value}, not {first.last_seq + 1} — the "
        f"numbers are not contiguous in commit order")
    assert finished_at >= committed_at


# ── (3b) a rollback burns nothing ──────────────────────────────────────────

def test_a_rolled_back_allocation_is_reissued_exactly_once(schema, tenant, app):
    """A position is issued inside the caller's transaction, so a rollback returns
    it. Not burned (no gap), and not reused-after-being-seen (the first caller
    never committed it)."""
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)

    statement = ra.allocation_statement(org_id=org, graph_id=graph,
                                        records=[RECORD])
    first = ra.execute_allocation(app, statement, org_id=org, graph_id=graph)
    app.rollback()

    second_conn = _app_conn()
    try:
        again = ra.execute_allocation(second_conn, statement, org_id=org,
                                      graph_id=graph)
        second_conn.commit()
    finally:
        second_conn.close()

    assert first.first_seq == again.first_seq, (
        f"a rolled-back allocation BURNED a position: {first.first_seq} then "
        f"{again.first_seq}")
    assert _counter(schema, org, graph) == (again.first_seq, "active"), (
        "the counter did not end at the number that committed")


# ── (3c) replay READS the carried ordinal ──────────────────────────────────

def _position_plan(records, *, start_seq: int):
    """The journal-position statements of a plan, with the `seq` each binds."""
    plan = rp.replay_plan(records, org_id="o", graph_id="g",
                          start_seq=start_seq)
    out = []
    for statement in plan:
        if statement.sql.startswith(rp._JOURNAL_INSERT_PREFIX):
            _org_id, _graph_id, seq = statement.params[:3]
            out.append(seq)
    return out


def test_replay_reads_a_carried_ordinal():
    """THE RULING'S SECOND HALF (V5 item 5a): replay READS the number instead of
    computing it.

    A record that carries `seq` is asserting its own position — that is the shape a
    record-sourced read produces (`journal_event.seq` is a column). A batch
    replaying a LATER segment (`start_seq=5`) keeps the positions the records
    really hold; the caller's watermark and the record's own number are two
    readings of one fact, and the plan binds the record's.

    ⚠️ THE DISCRIMINATING LEG IS THE REFUSAL BELOW, not this one: a plan that
    ignored `seq` entirely would also produce 5, 6 here, because it enumerates from
    the same `start_seq`. What only the READ gives is the ability to NOTICE a
    disagreement — which is `test_replay_refuses_a_carried_ordinal_that_disagrees`.
    """
    carried = [{**RECORD, "seq": 5}, {**RECORD, "seq": 6}]
    assert _position_plan(carried, start_seq=5) == [5, 6]
    # A record with no `seq` still enumerates (the JSONL journal's shape — no
    # writer emits one yet, `§A7.7` 6.3).
    assert _position_plan([RECORD, RECORD], start_seq=5) == [5, 6]


def test_replay_refuses_a_carried_ordinal_that_disagrees():
    """⛔ A DISAGREEMENT IS A REFUSAL, never a renumbering — the brief's
    `carried != start_seq + i`.

    Silently preferring either side would be the `#9190` defect with a new
    spelling: the record says one number and the enumeration says another, and one
    of them gets overwritten without anyone learning which was right. Both shapes
    are refused: a wholly different number (a reordered or foreign batch) and an
    off-by-one (a batch whose start_seq the caller got wrong).
    """
    for carried_value, start in ((9, 1), (6, 1)):
        with pytest.raises(rp.RecordProjectionError) as raised:
            rp.replay_plan([{**RECORD, "seq": carried_value}], org_id="o",
                           graph_id="g", start_seq=start)
        message = str(raised.value)
        assert str(carried_value) in message and str(start) in message, (
            f"the refusal does not name both numbers: {message}")

    with pytest.raises(rp.RecordProjectionError):
        rp.replay_plan([{**RECORD, "seq": "not-a-number"}], org_id="o",
                       graph_id="g", start_seq=1)

    # ⛔ AND A VALUE THAT ONLY *COERCES* TO THE RIGHT-NUMBERED POSITION IS A
    # DISAGREEMENT, NOT A MATCH (#12894 review cycle 4, P2). `int(carried)` used
    # to accept both of the first two below — 5.9 truncated to 5, "5" parsed to 5
    # — against `start_seq=5`, i.e. it silently renumbered a value that disagreed
    # into agreement, which is the `#9190` defect with a new spelling. `True` is
    # included because `bool` is an `int` SUBCLASS, so a naive `isinstance(…, int)`
    # would let it through as 1.
    for wrong_type in (5.9, "5", True):
        with pytest.raises(rp.RecordProjectionError) as coercion:
            rp.replay_plan([{**RECORD, "seq": wrong_type}], org_id="o",
                           graph_id="g", start_seq=5)
        assert "not an integer" in str(coercion.value), (
            f"seq={wrong_type!r} was accepted or refused for the wrong reason: "
            f"{coercion.value}")


# ── (3d) the journal line and its row carry the SAME number ────────────────

def test_the_armed_row_carries_the_journals_position(schema, tenant, app,
                                                     monkeypatch):
    """The cutover invariant: while both allocators exist they must AGREE.

    The journal's append reported N and the counter issued N. The row is at N. That
    is what makes a replay of the JSONL (which enumerates 1..N) reproduce the same
    numbers the record holds — and it is the test that would have caught a door
    that allocated `counter + 1` while the journal was at `N`.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])
    _seed_and_arm(app, org, graph, 2)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=3,
                                 tenant=(org, graph))
    assert write is not None and write.applied, f"refused: {write!r}"
    assert (write.first_seq, write.last_seq) == (3, 3)
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3]


def test_a_divergent_journal_position_is_refused_not_renumbered(
        schema, tenant, app, monkeypatch):
    """⛔ THE FALSIFIER, and it is RED without the guard.

    The counter is at 3 and the door is handed journal position 9 (what a door
    that trusted the append would report). Writing the row at 4 leaves the line at
    9 and the row at 4 — two coordinates, one record, and a replay of the JSONL can
    never reproduce the row. The write must REFUSE and the allocation must ROLL
    BACK, so the counter is still 3 afterwards: nothing burned, nothing renumbered.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=9,
                                 tenant=(org, graph))
    assert write is not None and not write.applied, (
        f"the door wrote a position {write.first_seq} for journal position 9 — "
        f"the line and its row now carry different numbers")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3], (
        "the refused write left a position row behind")
    assert _counter(schema, org, graph) == (3, "active"), (
        "the refused write did not roll the allocation back")


# ── (3d-bis) THE FOURTH DIVERGENCE CAUSE: no fault required ────────────────

def test_an_out_of_order_pair_WEDGES_the_graph_and_the_wedge_is_measured(
        schema, tenant, app, monkeypatch):
    """⛔ THE RESIDUAL, AS A FALSIFIER RATHER THAN A SENTENCE (review 4f5e542643).

    The cutover invariant compares the counter's number with the journal's
    POSITION, and the two are ordered by different events: the journal by APPEND
    (the `sdk` site appends, then calls the door — no lock across the two), the
    counter by ALLOCATION. So two in-flight writes can be numbered in the opposite
    order to their lines, and that needs **no fault at all** — not a restore, not a
    truncation, not a seal.

    Driven deterministically here (no threads, so the interleaving is exact and the
    leg cannot flake): the SECOND journal position is offered first, then the first,
    then a third.
      * seq=2 against a counter at 0 issues 1 ⇒ refused, counter rolled back to 0;
      * seq=1 issues 1 ⇒ APPLIED (the graph looks healthy);
      * seq=3 issues 2 ⇒ REFUSED — and now no later write can succeed either,
        because the counter is permanently one behind the journal.

    ⛔ THIS TEST PINS A KNOWN DEFECT, on purpose. It is not asserting that the
    behaviour is right; it is asserting that the behaviour is WHAT HAPPENS, so that
    (a) nobody arms `TORTOISE_RECORD_ALLOCATE` believing only a fault can wedge a
    graph, and (b) the fix — `#9318`'s journal identity in the key, or a per-graph
    lock spanning append→allocate — has a leg that must change when it lands. If
    this test goes green (three APPLIEDs), the fix has arrived: replace it.
    """
    org, graph = tenant
    _seed_and_arm(app, org, graph, 0)
    _arm_door(monkeypatch, flag=True)

    def write(seq: int):
        return rw.write_live_record(RECORD, durable=True, seq=seq,
                                    tenant=(org, graph))

    out_of_order = write(2)
    assert out_of_order is not None and not out_of_order.applied, (
        f"journal position 2 took the counter's 1 — the invariant is not armed: "
        f"{out_of_order!r}")
    assert _counter(schema, org, graph) == (0, "active"), (
        "the refusal did not roll the allocation back")

    in_order = write(1)
    assert in_order is not None and in_order.applied, (
        f"the write whose position MATCHES was refused: {in_order!r}")
    assert _counter(schema, org, graph) == (1, "active")

    # The wedge. The journal holds two lines; the counter holds 1. Every position
    # the journal can offer from here is ahead of what the counter can issue, so
    # the door refuses — for a reason no replay repairs, because on a cut-over
    # graph an enumerated replay is itself refused (`_refuse_an_enumerated_replay`).
    wedged = write(3)
    assert wedged is not None and not wedged.applied, (
        "a graph whose counter is behind its journal still accepted a write")
    assert _counter(schema, org, graph) == (1, "active"), (
        "the wedged write moved the counter")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1], (
        "the wedge did not leave the record one row behind the journal — if this "
        "changed, the invariant's failure mode changed with it")


# ── (3e) a truncated journal does not re-mint a position ───────────────────

def test_a_shrunken_journal_cannot_mint_a_position(schema, tenant, app,
                                                   monkeypatch):
    """The journal-can-shrink hazard (evidence V4.3, restore-replace / in-place
    truncate+regrow).

    A restored or truncated journal reports position 1 for its next append while
    the record's counter is at 4. Under enumeration that re-mints positions the
    record already holds (`log.py`'s in-place regrow is undetectable by any cheap
    identity check). Under the armed arm it cannot: `1 != 4` is a refusal, and no
    row is written at 5.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3, 4])
    _seed_and_arm(app, org, graph, 4)
    _arm_door(monkeypatch, flag=True)

    write = rw.write_live_record(RECORD, durable=True, seq=1,
                                 tenant=(org, graph))
    assert write is not None and not write.applied, (
        "the door minted a position from a journal that went backwards")
    assert [row[0] for row in _journal_rows(schema, org, graph)] == [1, 2, 3, 4]
    assert _counter(schema, org, graph) == (4, "active")


# ── (3f) the live seal trigger is GATED, not harmless ──────────────────────

def test_a_sealed_journal_is_gated_on_the_armed_arm(schema, tenant, app,
                                                    monkeypatch, tmp_path):
    """`TORTOISE_JOURNAL_SEGMENTS=1` is the round-1 LIVE trigger (evidence V4.2).

    `seal_active` renames the active segment aside and starts a fresh
    `events.jsonl`, so the FILE-LOCAL index restarts at 1 while the
    segment-spanning reader keeps counting. This test builds that state for real —
    a seal, then an append — rather than asserting the claim from the code, and
    then drives the door with the number the append really returned.

    GATED is the answer the brief allows: on the armed arm the mismatch is a
    refusal, so the seal cannot mint a position the record already holds.
    """
    from tortoise import journal_segments

    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2])
    _seed_and_arm(app, org, graph, 2)
    _arm_door(monkeypatch, flag=True)

    graph_dir = tmp_path / "journal"
    graph_dir.mkdir()
    active = graph_dir / journal_segments.ACTIVE_NAME
    active.write_text("", encoding="utf-8")

    from tortoise.log import EventLog

    log = EventLog(active)
    assert log.append({**RECORD, "id": "pre-seal"}) == 1
    monkeypatch.setenv(journal_segments.ENV_FLAG, "1")
    journal_segments.seal_active(graph_dir)
    after_seal = log.append({**RECORD, "id": "post-seal"})
    assert after_seal == 1, (
        f"the seal did not restart the file-local index (got {after_seal}) — "
        f"this test's premise, not the door's, is wrong")

    write = rw.write_live_record(RECORD, durable=True, seq=after_seal,
                                 tenant=(org, graph))
    assert write is not None and not write.applied, (
        f"a sealed journal re-used position {after_seal} on an armed counter at 2")
    assert _counter(schema, org, graph) == (2, "active")
