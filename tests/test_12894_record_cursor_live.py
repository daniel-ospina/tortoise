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
    enumerated at the JOURNAL's ordinal for it (9). The replay must be REFUSED by
    name, and the position rows must still be exactly the ones the door wrote —
    which is the half that fails if the guard is removed (a row at 9 would
    appear here with no exception at all).
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
        RECORD, durable=True, seq=9, tenant=(org, graph), allocate=True)
    assert write is not None and write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"the allocated door write did not apply: {write}")
    assert (write.first_seq, write.last_seq) == (4, 4), (
        f"the door did not take the allocated number: {write.first_seq}")
    written = [row[0] for row in _journal_rows(schema, org, graph)]
    assert written == [1, 2, 3, 4], f"the door wrote {written}"

    # THE DERIVED REPLAY OF THE SAME JOURNAL EVENT, at the ordinal the JOURNAL
    # holds for it (9): refused, by name, before a single position statement.
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
    counter is halted and reopened to `(4, 'seeded')`; an enumerated replay at the
    journal's ordinal for it (9) must be REFUSED, and position 9 must NOT exist.
    Pre-fix this replay SUCCEEDED and `journal_event` held the record at 4 AND 9.
    """
    org, graph = tenant
    _append_positions(schema, org, graph, [1, 2, 3])
    _seed_and_arm(app, org, graph, 3)
    app.commit()
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    write = rw.write_live_record(
        RECORD, durable=True, seq=9, tenant=(org, graph), allocate=True)
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

    The falsifier drives the REAL live door with a journal ordinal of 9 on a
    graph whose counter is at 3: the write must APPLY at 4, and the position row
    must BE 4 — a forwarded `first_seq` reds this test twice over (a refusal, and
    a position of 9 if the refusal were ever relaxed).
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
        RECORD, durable=True, seq=9, tenant=(org, graph), allocate=True)
    assert write is not None, "the live door returned nothing (an unarmed read?)"
    assert write.outcome is rw.RecordWriteOutcome.APPLIED, (
        f"a journal ordinal of 9 refused an ALLOCATED write: outcome="
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
