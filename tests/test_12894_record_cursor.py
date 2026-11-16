"""#12894 stage C — the record's ordinal allocator, pinned WITHOUT a database.

WHY THIS FILE EXISTS. Two review rounds on the 2,079-line plan found defects
that are MECHANICAL FACTS about SQL, each of which a runnable test answers
instantly and no amount of document revision can settle. This file is the
DB-free half of the answer: the SQL's own shape, the registry, and the failure
vocabulary. `tests/test_12894_record_cursor_live.py` executes the same SQL
against a real Postgres, because a string assertion is not a mechanism.

THE BRIEF'S RULE, applied to the assertions below: a review finding is answered
by a test that goes RED without the fix. Where the only honest DB-free assertion
is about the statement's TEXT, the live sibling file proves the behaviour and
the pairing is named in the PR's `finding → test` table.

THE FINDINGS → TESTS (each row is a round-1/round-2/round-3 item that was
marked WRONG or UNPROVEN):

================================= ==============================================
`GREATEST(0,0)=0` with `seq > 0`  `test_the_cursor_allows_zero_so_an_empty_graph_can_seed`
                                  + live `test_an_empty_graph_seeds_and_its_first_write_is_one`
RLS seed with no tenant 42501     `test_the_seed_sets_the_tenant_scope_itself`
                                  + live `test_a_seed_without_the_guc_is_a_refusal_not_a_divergence`
converter refuses `$n` + `%s`     `test_every_allocator_statement_survives_the_dollar_converter`
batch = N copies of one payload   `test_the_batch_pairs_each_payload_by_ordinality`
                                  + live `test_a_batch_writes_distinct_payloads_in_one_statement`
`ON CONFLICT DO NOTHING` silently   `test_the_allocated_insert_cannot_absorb_a_duplicate`
absorbs the position              + live `test_a_duplicate_position_fails_the_transaction`
allocated number never reaches     `test_the_written_watermark_is_the_allocated_one`
the watermark / byte meter        + live `test_the_allocated_number_reaches_the_watermark_and_the_byte_meter`
`record_cursor` not in the registry `test_the_cursor_is_in_the_record_registry`
(B3: restore restarts at zero)    + `test_the_migration_is_the_registered_file`
case 2 unrepresentable            `test_case_two_is_unrepresentable_in_the_seed_sql`
                                  + live `test_case_two_writes_no_counter_row`
isolation preconditions (M5)      `test_the_allocator_refuses_a_non_read_committed_dsn`
                                  + live `test_a_serializable_transaction_is_refused_at_arm`
40001/40P01/57014 handled (M5)    `test_each_retryable_sqlstate_is_named_and_counted`
                                  + live `test_a_cancelled_allocation_rolls_the_counter_back`
no global `statement_timeout`     `test_the_allocator_imposes_no_global_statement_timeout`
enumerated replay after cutover    `test_a_post_cutover_enumerated_replay_is_refused_not_duplicated`
  ⇒ same record at a 2nd position  (+ live `test_a_post_cutover_replay_cannot_`
                                     `add_a_second_position_for_one_record`)
`seeded` treated as cut over      `test_a_post_cutover_enumerated_replay_is_refused_not_duplicated`
  ⇒ the remedy named a verb      (cases 3/3b/3c: `seeded` names `arm`, `halted` the re-cut,
     that refuses                 and a REOPENED graph — `seeded` with consumed `seq` — stays refused)
the door's token is not the       `test_the_door_speaks_the_counters_vocabulary`
  counter's name (P3)              `position_collision` vs `duplicate_position`
==================================================================================
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tortoise import record_allocator as ra
from tortoise import record_projection as rp
from tortoise import record_writer as rw

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
MIGRATION_NAME = "20261116000001_record_cursor_allocator.sql"
MIGRATION = MIGRATIONS / MIGRATION_NAME


# ── the registry (B3) ───────────────────────────────────────────────────────

def test_the_cursor_is_in_the_record_registry() -> None:
    """`THE_RECORD_TABLES` drives the dump, the manifest, the counts and the RLS
    posture read. A counter outside it is round-1 finding B3: a restore returns
    a populated `journal_event` with no counter, the allocator restarts at 1, and
    every new position collides with an existing row.

    RE-DERIVED, not edited to fit: the assertion is that the tuple names the
    table the MIGRATION creates — the registry and the DDL must agree, and a
    hard-coded `== 10` could only ever encode one side of that.
    """
    declared = _created_tables(MIGRATION.read_text(encoding="utf-8"))
    assert "record_cursor" in declared, (
        f"the migration creates {sorted(declared)}, so `THE_RECORD_TABLES` "
        f"has no reason to list it")
    for table in declared:
        assert table in rp.THE_RECORD_TABLES, (
            f"{table} is created by {MIGRATION_NAME} but is not in "
            f"THE_RECORD_TABLES — it would be invisible to the dump, the "
            f"manifest and the tenancy census")


def test_the_migration_is_the_registered_file() -> None:
    """Both directions of `RECORD_MIGRATIONS`, the same way `test_9231`'s
    trigger pins it: the file this change adds is REGISTERED, and the registry
    entry is a file that exists.

    Round-1 B3 named `RECORD_MIGRATIONS` in both directions as a deliverable
    because an unregistered migration is invisible to `table_columns()` — the
    reader that parses these files — so the schema the projection believes in
    would not describe the schema that exists.
    """
    assert MIGRATION_NAME in rp.RECORD_MIGRATIONS, (
        f"{MIGRATION_NAME} is not in RECORD_MIGRATIONS")
    missing = [name for name in rp.RECORD_MIGRATIONS
               if not (MIGRATIONS / name).exists()]
    assert missing == [], f"registered record migrations that do not exist: {missing}"
    # The packaging glob is the third place a filename must match, or the wheel
    # ships a schema the repo reads and a deployment does not.
    assert re.fullmatch(r".*_record_.*\.sql", MIGRATION_NAME), (
        "pyproject's `*_record_*.sql` glob would not package this file")


def test_the_cursor_is_in_the_reapply_set() -> None:
    """A container migrated by an older tree carries `journal_event` and would
    take `_ensure_record_schema`'s "already migrated" shortcut — so this file
    must be re-applied, or the armed leg writes into a database with no
    `record_cursor` (`UndefinedTable`) while the suite reports green."""
    from tests.test_9078_record_written_by_production_path import (
        RECORD_REAPPLY_STEPS,
    )

    names = [path.name for path, _ in RECORD_REAPPLY_STEPS]
    assert MIGRATION_NAME in names, (
        f"{MIGRATION_NAME} is not re-applied on the already-migrated path; "
        f"the set is {names}")


# ── the seed: the empty graph, and case 2 ───────────────────────────────────

def _created_tables(sql: str) -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS public\.(\w+)", sql))


def test_the_cursor_allows_zero_so_an_empty_graph_can_seed() -> None:
    """FINDING (round 2, blocking): `GREATEST(0,0) = 0` with `CHECK (seq > 0)`
    raises `23514`, so the EASIEST cutover — a brand-new graph — cannot happen.

    The fix is not "make the seed write 1": the cursor stores the LAST ALLOCATED
    position, `GREATEST(MAX(journal_event.seq), <enumerated floor>)` is 0 when
    nothing exists, and 0 is the truthful answer. What must change is the
    constraint, and it must change on the CURSOR only — `journal_event.seq` keeps
    `> 0`, because a POSITION is always at least 1.
    """
    sql = MIGRATION.read_text(encoding="utf-8")
    cursor_ddl = re.search(
        r"CREATE TABLE IF NOT EXISTS public\.record_cursor \((.*?)\n\);",
        sql, re.DOTALL)
    assert cursor_ddl, "the migration does not create public.record_cursor"
    assert "CHECK (seq >= 0)" in cursor_ddl.group(1), (
        "record_cursor.seq must accept 0 — the last-allocated position of an "
        "empty graph — or the seed raises 23514 on the easiest cutover")
    assert "CHECK (seq > 0)" not in cursor_ddl.group(1)

    journal = (MIGRATIONS / "20261012000001_record_event_stream.sql").read_text(
        encoding="utf-8")
    assert re.search(r"seq\s+bigint NOT NULL CHECK \(seq > 0\)", journal), (
        "journal_event.seq must keep its positive-only CHECK — a position of 0 "
        "is not a position")
    # And the seed must not fabricate a 1 to dodge the constraint: a seed of 1
    # on an empty graph would make the first write 2 and leave position 1
    # unallocated forever.
    assert "GREATEST(observed.row_max, observed.enumerated_floor)" in ra.SEED_SQL
    assert not re.search(r"COALESCE\(.*?,\s*1\s*\)", ra.SEED_SQL), (
        "the seed coerces an absent maximum to 1 — that SKIPS position 1")


def test_case_two_is_unrepresentable_in_the_seed_sql() -> None:
    """FINDING (round 2/3, P0): case 2 — the record holds a position the journal
    cannot reproduce (a replaced or shortened journal) — must be refused by the
    SQL, not by prose (round 3: a fix that "reads as responsive and changed
    nothing mechanically").

    The mechanism is the INSERT's own guard: `WHERE row_max <= enumerated_floor`
    means the SELECT yields NO ROW, so no counter row CAN be written for that
    case. Python only classifies the resulting absence.
    """
    assert re.search(r"WHERE observed\.row_max <= observed\.enumerated_floor",
                     ra.SEED_SQL), (
        "the seed has no guard making case 2 unrepresentable — a row could be "
        "written for a graph whose record is ahead of its journal")
    # The guard is on the INSERT's SELECT, i.e. it constrains what can be
    # written rather than what is reported afterwards.
    insert_at = ra.SEED_SQL.index("INSERT INTO public.record_cursor")
    assert ra.SEED_SQL.index("WHERE observed.row_max") > insert_at, (
        "the guard must be part of the INSERT's SELECT, not a pre-flight check")

    # And the Python side must not pre-empt it: the refusal is raised only after
    # the statement returned nothing. A Python `if row_max > floor: raise` before
    # the execute would make the SQL guard unreachable and untested.
    source = Path(ra.__file__).read_text(encoding="utf-8")
    seed_body = source.split("def seed(", 1)[1].split("\ndef arm(", 1)[0]
    execute_at = seed_body.index("cursor.execute(SEED_SQL")
    # The LAST occurrence: the docstring names the reason too, and the mechanism
    # is the code that runs.
    divergence_at = seed_body.rindex('reason="divergence"')
    assert execute_at < divergence_at, (
        "the divergence refusal happens before the seed statement runs — case 2 "
        "would be enforced by prose rather than by the SQL")

    # ⛔ THE CLASSIFIER, NOT A GATE (#12894 review cycle 1, P2). The line here
    # used to read `assert seed_body.count("row_max >") == 0 or True` — `X or
    # True` can never fail, so it certified nothing while reading as the check
    # that case 2 is enforced by the SQL. It also had the polarity backwards:
    # `seed()` DOES carry `row_max > floor`, and that is correct — it names
    # WHICH refusal the statement just produced. The property that matters is
    # ORDER: the comparison can only CLASSIFY a refusal that already happened, so
    # it must run AFTER the statement — a Python check placed before
    # `cursor.execute(SEED_SQL)` would be the enforcement the SQL is supposed to
    # own.
    #
    # ⛔ AND THE SUBSTRING SCAN WAS THE SAME DEFECT ONE LAYER DOWN (measured).
    # Two earlier spellings were vacuous or wrong: `X or True` could not fail;
    # then `rindex("row_max > floor") > execute_at` passed with the exact
    # pre-flight this comment forbids, because the statement's own classifier
    # STAYS the last occurrence. A textual scan is wrong in BOTH directions, and
    # both were measured on this file: `if floor < row_max:` (identical
    # semantics, operator flipped) was GREEN — case 2 enforced by Python with the
    # SQL guard unreachable — and adding a COMMENT above the statement that merely
    # NAMED `row_max > floor` was RED.
    # ⛔ AND A COMPARE-OPERAND TEST WAS STILL TOO NARROW (measured, review cycle
    # 3). Two pre-flights enforced case 2 in Python before the statement and left
    # the previous pin GREEN, because neither put `row_max` where the walk looked:
    # `peak = _journal_max(cursor, …)` / `if peak > floor: raise` (an ALIAS, so
    # the name differs) and `if (row_max - floor) > 0: raise` (`Compare.left` is
    # a `BinOp`, not a `Name`). So the rule is not "which names appear inside a
    # comparison" but "can the value that DECIDES CASE 2 be read before the
    # statement at all": no `Name` naming `row_max`, and no call to the reader
    # that produces it (`_journal_max`), may appear before the execute line. That
    # is agnostic about an alias and about how a comparison is spelled, and it is
    # AST-only, so a comment is never a node.
    #
    # ⛔ ITS BOUND, stated rather than implied: a pre-flight that reads the journal
    # through some OTHER route (`cursor.fetchone()` and its own integer
    # comparison) would name neither and would pass. This defends the realistic
    # edit — moving the threshold check up using the value the function already
    # computes — not an adversarial rewrite of `seed()`.
    tree = ast.parse(source)
    seed_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "seed")
    execute_line = next(
        i for i, line in enumerate(source.splitlines(), start=1)
        if "cursor.execute(SEED_SQL" in line
    )
    #: The value that decides case 2, and the ONE reader that produces it.
    decides_case_two = frozenset({"row_max", "_journal_max"})

    def _name_of(func: ast.expr) -> str | None:
        if isinstance(func, (ast.Name, ast.Attribute)):
            return getattr(func, "id", getattr(func, "attr", None))
        return None

    early = sorted(
        # `ast.arguments` carries no `lineno`; a node without one cannot be
        # before the statement, so it defaults to the boundary and drops out.
        node.lineno for node in ast.walk(seed_fn)
        if getattr(node, "lineno", execute_line) < execute_line
        and ((isinstance(node, ast.Name) and node.id in decides_case_two)
             or (isinstance(node, ast.Call)
                 and _name_of(node.func) in decides_case_two))
    )
    assert not early, (
        f"the value that decides case 2 is read at line(s) {early}, BEFORE the "
        f"seed statement at line {execute_line} \u2014 case 2 would be enforced by "
        f"Python rather than by the SQL's own `WHERE row_max <= "
        f"enumerated_floor` guard, which would make that guard unreachable and "
        f"untested")


def test_the_seed_sets_the_tenant_scope_itself() -> None:
    """FINDING (round 2, blocking): a seed under RLS with no
    `app.current_org_id` raises `42501`, and the plan's own read-back then
    reported it as a DIVERGENCE — sending the operator to the wrong remedy.

    `seed()` therefore SETS AND VERIFIES the tenant itself
    (`record_projection.scope_connection`, which also refuses a bypassing role)
    before its first statement. The live sibling proves the 42501 is real on the
    raw SQL and that the documented path never draws it.
    """
    source = Path(ra.__file__).read_text(encoding="utf-8")
    seed_body = source.split("def seed(", 1)[1].split("\ndef arm(", 1)[0]
    assert "_scope(conn, org_id)" in seed_body, (
        "seed() does not scope the connection — an app-role seed with no tenant "
        "GUC raises 42501 on the WITH CHECK policy")
    scope_at = seed_body.index("_scope(conn, org_id)")
    execute_at = seed_body.index("cursor.execute(SEED_SQL")
    assert scope_at < execute_at
    # The isolation guard is FIRST: `SET TRANSACTION` (and any judgement about
    # the transaction's level) is only meaningful before the first statement.
    assert seed_body.index("require_read_committed(conn)") < scope_at


# ── the allocation statement ────────────────────────────────────────────────

def test_every_allocator_statement_survives_the_dollar_converter() -> None:
    """FINDING (round 2): `to_dollar_placeholders` renumbers by SPLITTING on
    `%s`, so a statement mixing a literal `$1` with `%s` is refused outright.

    Both allocator statements are pure `%s`, and the converter — the ONE
    converter PGlite and psql use — reproduces them with `$n` in the same order.
    """
    seed = rp.Statement(ra.SEED_SQL, ("o", "g", 0, "o", "g"))
    converted, params = rp.to_dollar_placeholders(seed)
    assert "$1" in converted and "%s" not in converted
    assert params == ["o", "g", 0, "o", "g"]

    allocation = rp.Statement(
        ra.ALLOCATION_SQL,
        (["T"], ["{}"], [None], 1, "o", "g", 1, "o", "g"))
    converted, params = rp.to_dollar_placeholders(allocation)
    assert converted.count("$") == 9, (
        f"the allocation binds {converted.count('$')} placeholders for 9 "
        f"parameters — a literal `$n` in the SQL would shift the renumbering")
    assert len(params) == 9

    mixed = rp.Statement("SELECT $1, %s", ("a", "b"))
    with pytest.raises(rp.RecordProjectionError):
        rp.to_dollar_placeholders(mixed)


def test_the_batch_pairs_each_payload_by_ordinality() -> None:
    """FINDING (round 2): `generate_series` without `unnest(...) WITH ORDINALITY`
    emits THE SAME payload N times — the "one statement" claim does not carry N
    distinct records.

    The ordinal is what pairs row i of the payload arrays with `base + i`. The
    live sibling asserts three DISTINCT payloads land on three consecutive
    positions.
    """
    assert "WITH ORDINALITY" in ra.ALLOCATION_SQL
    assert "generate_series" not in ra.ALLOCATION_SQL, (
        "a series joined to a payload list is exactly the N-copies defect")
    assert "allocated.base + payloads.ord" in ra.ALLOCATION_SQL, (
        "the position must be derived from the ORDINAL, not from a constant")
    assert re.search(r"ORDER BY payloads\.ord", ra.ALLOCATION_SQL)


def test_the_allocated_insert_cannot_absorb_a_duplicate() -> None:
    """FINDING (round 1, B5): `ON CONFLICT ... DO NOTHING` on the position insert
    while the counter UPDATE is unconditional absorbs a duplicate WITHOUT an
    error — the counter advances, the payload is dropped, and the layer rows land
    with no idempotency key.

    The allocated insert therefore has NO conflict clause: a duplicate is a
    `23505` that fails the transaction, and the increment rolls back with it.
    """
    assert "ON CONFLICT" not in ra.ALLOCATION_SQL, (
        "the allocated position insert can still absorb a duplicate silently")
    # The REPLAY path keeps DO NOTHING, and that asymmetry is deliberate: a
    # replay must be idempotent (#5285 — replay twice and compare the tables),
    # and its numbers are derived, not issued.
    replay_sql = rp._journal_row(
        {"type": "T", "id": "x"}, 1, "o", "g").sql
    assert "ON CONFLICT (org_id, graph_id, seq) DO NOTHING" in replay_sql


def test_the_written_watermark_is_the_allocated_one() -> None:
    """FINDING (round 2, blocking): `replay()` DISCARDS each statement's result
    and both `last_seq` and the byte meter use the CALLER's `first_seq`, so an
    allocated number never reached them — the meter matched zero rows and
    charged 0, silently, under a contract that says it never fails a write.

    The plan must therefore CARRY the allocation statement, the report must
    carry what it returned, and the writer must take the watermark from the
    report rather than from its own argument.
    """
    records = [{"type": "PointAdded", "ts": "2026-11-16T00:00:00Z", "id": "p1",
                "point": {"id": "p1", "content": "c", "pointKind": "claim"}}]
    plan = rp.replay_plan(records, org_id="o", graph_id="g", allocate=True)
    assert plan[-1].sql.lstrip().startswith(ra.ALLOCATION_STATEMENT_PREFIX), (
        "the allocation is not LAST in the plan — the counter's row lock must be "
        "the last CONTENDED lock the transaction takes")
    assert sum(1 for s in plan
               if s.sql.lstrip().startswith(ra.ALLOCATION_STATEMENT_PREFIX)) == 1
    # No per-record position row on the allocated path: ONE statement writes all
    # of them, so a second writer of the same positions cannot exist. (The
    # allocation statement itself inserts into journal_event — that is the
    # point — so the count, not the substring, is the assertion.)
    position_writers = [s for s in plan if rp._JOURNAL_INSERT_PREFIX in s.sql]
    assert len(position_writers) == 1, (
        f"{len(position_writers)} statements write journal positions — the "
        f"allocated path must have exactly one")
    assert position_writers[0] is plan[-1]
    assert "first_seq: int = 0" in Path(rp.__file__).read_text(encoding="utf-8")

    import inspect

    from tortoise import record_writer as rw

    source = inspect.getsource(rw.RecordWriter.write)
    assert "report.first_seq if allocate" in source, (
        "RecordWriter.write still derives the watermark from the caller's "
        "first_seq when the record allocated the number")
    assert "first_seq=watermark_first" in source, (
        "the byte meter is still measuring the caller's positions")


def test_an_allocating_plan_never_asks_the_collision_question() -> None:
    """REBASE FINDING (#12894 + #9318 met here, and the merge was silent):
    `replay()`'s `#9318` guard selected its statements with
    `_JOURNAL_INSERT_PREFIX in statement.sql`. The ALLOCATION statement's text
    CONTAINS that prefix — it ends in `INSERT INTO public.journal_event …
    RETURNING seq` — so the guard fired on it, read `_journal_row`'s parameter
    ORDER out of `statement.params[:5]` (three ARRAYS and an int on this path),
    and asked the database a question whose answer Postgres refused with
    `operator does not exist: text = text[]` (42883, MEASURED on a real server).
    The allocator's own `CursorNotActive` refusal was replaced by a
    `RecordStatementError` naming a position nobody had asked for.

    The guard decides whether an ABSORBED insert is a replay or a collision, and
    the allocation has no `ON CONFLICT` — it can absorb nothing — so it must
    never be asked. Driven here through a connection on which EVERY statement
    reports `rowcount == 0` (the absorbed shape, and the shape an allocation
    that wrote nothing has), which is exactly what made the guard proceed.
    """
    records = [{"type": "PointAdded", "ts": "2026-11-16T00:00:00Z", "id": "p1",
                "point": {"id": "p1", "content": "c", "pointKind": "claim"}}]

    class _Cursor:
        #: Every insert is ABSORBED — the guard's own precondition.
        rowcount = 0

        def __init__(self, conn: _AbsorbedConn) -> None:
            self._conn = conn
            self._sql = ""

        def execute(self, sql, params=None) -> None:
            self._conn.executed.append(sql)
            self._sql = sql
            if sql == rp._JOURNAL_CONFLICT_CONTENT_SQL:
                self._conn.conflict_reads += 1
            if sql == rp.TENANT_GUC_SQL and params:
                self._conn.guc_set = params[0]

        def fetchall(self):
            # The allocation's `RETURNING seq` — nothing was allocated.
            return []

        def fetchone(self):
            if self._sql == ra.ISOLATION_SQL:
                return (ra.READ_COMMITTED,)
            if self._sql == rp.TENANT_SCOPE_SQL:
                return (self._conn.guc_set, False)
            return (self._conn.guc_set,)

        def close(self) -> None:
            pass

    class _AbsorbedConn:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.conflict_reads = 0
            self.guc_set = None

        def cursor(self) -> _Cursor:
            return _Cursor(self)

    conn = _AbsorbedConn()
    #: The refusal this path OWES. `test_an_unarmed_cursor_allocates_nothing`
    #: asserts it live; asserting it here is what makes the DB-free half a
    #: falsifier rather than a `conflict_reads` counter nobody would notice.
    with pytest.raises(ra.CursorNotActive):
        rp.replay(records, conn, org_id="o", graph_id="g", allocate=True)

    allocated = [sql for sql in conn.executed
                 if sql.startswith(ra.ALLOCATION_STATEMENT_PREFIX)]
    assert len(allocated) == 1, (
        f"the allocation did not run on the allocating path: {conn.executed!r}")
    assert conn.conflict_reads == 0, (
        "the #9318 collision question was asked about the ALLOCATION statement — "
        "its params are (event_types[], payloads[], recorded_at[], n, org_id, …), "
        "not `_journal_row`'s (org_id, graph_id, seq, event_type, payload), so the "
        "question is malformed and Postgres answers it with `operator does not "
        "exist: text = text[]`, replacing the allocator's own CursorNotActive "
        "refusal with a statement error naming a position nobody asked for. The "
        "allocation has no `ON CONFLICT`, so it can absorb nothing and there is "
        "nothing for that guard to decide")


def test_a_post_cutover_enumerated_replay_is_refused_not_duplicated() -> None:
    """#9190's reopened ruling, on the path that could silently break it.

    THE DEFECT. `replay()` numbers records by ENUMERATION (`start_seq` is the
    journal's own ordinal for `records[0]`), and it passes `position=True` —
    which is what the derived live door does on every write today
    (`allocate` defaults to False). Once a graph is CUT OVER the ordinal is not
    the journal's to give (owner ruling 2026-11-15: *"the record allocates the
    ordinal and the journal record CARRIES it; replay READS the number instead
    of computing it"*), so a replay of the SAME journal event lands the same
    payload at a SECOND position — and neither existing guard can see it:
    `ON CONFLICT (org_id, graph_id, seq) DO NOTHING` keys on `seq` and the
    enumerated `seq` is free, and `_refuse_a_journal_collision` only runs on an
    insert that was ABSORBED (this one affects a row). No error, no counter, one
    record at two positions — and the door's refusal text recommended exactly
    that replay as the remedy.

    ⛔ THE REFUSAL IS NARROW, and that is asserted in the same breath: the SAME
    replay on the SAME recorder, with no cursor row, still writes its position
    row. That is every graph before cutover, and it is what keeps the existing
    suite honest rather than merely green.
    """
    records = [{"type": "PointAdded", "ts": "2026-11-16T00:00:00Z", "id": "p1",
                "point": {"id": "p1", "content": "c", "pointKind": "claim"}}]

    class _Cursor:
        def __init__(self, conn: _CounterConn) -> None:
            self._conn = conn
            self._sql = ""
            self.rowcount = 1

        def execute(self, sql, params=None) -> None:
            self._conn.executed.append((sql, params))
            self._sql = sql
            if sql == rp.TENANT_GUC_SQL and params:
                self._conn.guc_set = params[0]

        def fetchall(self):
            return []

        def fetchone(self):
            # Per STATEMENT, never per call: `replay` scopes the tenant, verifies
            # the read-back, then asks whether this graph's positions are ISSUED.
            if self._sql == rp.TENANT_SCOPE_SQL:
                return (self._conn.guc_set, False)
            if self._sql == rp.CURSOR_PRESENCE_SQL:
                if self._conn.unreadable:
                    # The shape of a database the counter migration never
                    # reached: the relation is absent, so the read raises.
                    raise _NoSuchRelation(
                        'relation "public.record_cursor" does not exist')
                return self._conn.cursor_row
            return (self._conn.guc_set,)

        def close(self) -> None:
            pass

    class _NoSuchRelation(Exception):
        """`psycopg2.errors.UndefinedTable`, as far as this guard reads it."""

        pgcode = "42P01"

    class _CounterConn:
        def __init__(self, cursor_row=None, *, unreadable: bool = False) -> None:
            self.cursor_row = cursor_row
            self.unreadable = unreadable
            self.executed: list[tuple[str, object]] = []
            self.guc_set = None

        def cursor(self) -> _Cursor:
            return _Cursor(self)

        def journal_statements(self):
            return [sql for sql, _ in self.executed
                    if sql.startswith(rp._JOURNAL_INSERT_PREFIX)]

    # 1. CUT OVER — the counter holds a row, so the positions are ISSUED.
    cut_over = _CounterConn((3, ra.MODE_ACTIVE))
    with pytest.raises(rp.RecordProjectionError) as refused:
        rp.replay(records, cut_over, org_id="o", graph_id="g", start_seq=9)
    message = str(refused.value)
    for needle in ("ENUMERATED replay is REFUSED", "ISSUED", "record_cursor",
                   "mode='active'", "#9190", "allocate=True", "seed"):
        assert needle in message, (
            f"the refusal does not name {needle!r}, so an operator cannot tell it "
            f"from a lost write or a driver fault: {message}")
    assert cut_over.journal_statements() == [], (
        "the refusal came AFTER a position write — the duplicate the guard exists "
        f"to prevent was already stored: {cut_over.executed!r}")

    # 2. NOT CUT OVER — no cursor row, so the enumeration IS the coordinate and
    # the replay is byte-identical to what it was before the guard existed.
    fresh = _CounterConn(None)
    report = rp.replay(records, fresh, org_id="o", graph_id="g", start_seq=9)
    assert report.events == 1
    writes = fresh.journal_statements()
    assert len(writes) == 1, (
        f"a pre-cutover replay stopped writing its position row: {fresh.executed!r}")
    # 2b. AND A READ THAT FAILS IS A REFUSAL THAT NAMES ITS REMEDY — never a
    #     silent "no cursor" and never a bare driver error. The realistic shape
    #     is a database the counter migration has not reached (`42P01`), and the
    #     remedy is the MIGRATION, not a retry; catching-and-continuing is not an
    #     option on a real driver either (a failed statement aborts the caller's
    #     transaction, `25P02`).
    unreadable = _CounterConn(unreadable=True)
    with pytest.raises(rp.RecordProjectionError) as could_not_classify:
        rp.replay(records, unreadable, org_id="o", graph_id="g")
    detail = str(could_not_classify.value)
    for needle in ("could not be classified", "42P01",
                   "20261116000001_record_cursor_allocator.sql"):
        assert needle in detail, (
            f"a failed counter read does not name {needle!r}, so the operator is "
            f"sent to the wrong remedy: {detail}")
    assert unreadable.journal_statements() == [], (
        "a failed counter read still wrote a position row: "
        f"{unreadable.executed!r}")

    # 3. `SEEDED` IS STILL REFUSED, and the MODE decides the REMEDY (cycle-1 P2
    #    → cycle-2 P1). A `seeded` exemption was tried and MEASURED WRONG: §A7.7's
    #    `halted → seeded` is `REOPEN_SQL`, which sets `mode='seeded'` and KEEPS
    #    `seq`, so a reopened graph's ordinals WERE issued and an enumerated
    #    replay put the same record at a second position, silently (measured live:
    #    the record at 4, then a replay at 9, both rows present). What was right
    #    in cycle 1's finding is narrower: the refusal named two verbs a `seeded`
    #    graph cannot execute. Its writer is `arm`, and the message now names it.
    seeded = _CounterConn((0, ra.MODE_SEEDED))
    with pytest.raises(rp.RecordProjectionError) as not_armed:
        rp.replay(records, seeded, org_id="o", graph_id="g", start_seq=1)
    seeded_message = str(not_armed.value)
    for needle in ("SEEDED", "ARM it", "arm", "allocate=True", "seeded → active"):
        assert needle in seeded_message, (
            f"the `seeded` refusal does not name {needle!r}, so the operator is "
            f"sent to a verb that refuses rather than to the arming step: "
            f"{seeded_message}")
    assert "allocate=True) or a seed, not through a replay" not in seeded_message, (
        "the `seeded` arm still prints the generic remedy, whose two verbs both "
        f"refuse from `seeded`: {seeded_message}")
    assert seeded.journal_statements() == [], (
        f"a `seeded` graph's replay wrote a position row: {seeded.executed!r}")

    # 3b. HALTED names the re-cut, which is the only route off a halted counter.
    halted = _CounterConn((5, ra.MODE_HALTED))
    with pytest.raises(rp.RecordProjectionError) as re_cut:
        rp.replay(records, halted, org_id="o", graph_id="g", start_seq=9)
    halted_message = str(re_cut.value)
    for needle in ("HALTED", "reopen", "re-seed"):
        assert needle in halted_message, (
            f"the `halted` refusal does not name {needle!r}: {halted_message}")
    assert halted.journal_statements() == [], (
        "a `halted` graph's CONSUMED numbers were enumerated over: "
        f"{halted.executed!r}")

    # 3c. AND A REOPENED GRAPH KEEPS ITS CONSUMED NUMBERS (cycle-2 P1's exact
    #     shape, DB-free): `halted → seeded` does not reset `seq`, so the row is
    #     refused even though the mode reads `seeded`.
    reopened = _CounterConn((4, ra.MODE_SEEDED))
    with pytest.raises(rp.RecordProjectionError):
        rp.replay(records, reopened, org_id="o", graph_id="g", start_seq=9)
    assert reopened.journal_statements() == [], (
        "a graph reopened from `halted` still holds consumed numbers, and "
        f"enumerating over them duplicated a payload: {reopened.executed!r}")


def test_allocate_and_position_false_is_refused() -> None:
    """An allocated number exists to be CARRIED by a position row. Both flags set
    their opposite is a contradiction, refused before a connection is opened."""
    with pytest.raises(rp.RecordProjectionError):
        rp.replay_plan([{"type": "PointAdded", "id": "p", "point": {}}],
                       org_id="o", graph_id="g", position=False, allocate=True)
    with pytest.raises(rp.RecordProjectionError):
        rp.replay_plan([{"type": "PointAdded", "id": "p", "point": {}}],
                       org_id="o", graph_id="g", start_seq=7, allocate=True)


# ── the isolation guard and the retry vocabulary ────────────────────────────

def _fake_error(sqlstate: str) -> Exception:
    """A driver error carrying `pgcode`, the attribute classify() reads.

    ⛔ WHY A DOUBLE AND WHY IT IS STILL HONEST. psycopg2's `errors.<Class>()`
    instances carry NO `pgcode` until a real server error populates it, so
    "construct the real class" cannot express a state offline. What ties this
    double to reality is the assertion below: the SQLSTATE must be one the
    driver's OWN registry maps (`psycopg2.errors.lookup`), and the class it maps
    is the class the server's error would be raised as. The LIVE sibling then
    exercises a real `57014` (a statement timeout through the allocator's own
    SQL), so the classify() path is not only ever fed a double.
    ⛔ THE TIE IS CONDITIONAL ON THE DRIVER BEING INSTALLED, AND THAT IS NOT A
    WEAKENING (measured 2026-11-16 on this branch's head). `psycopg2-binary` is
    the OPTIONAL `[postgres]` extra (`pyproject.toml`), absent from
    `dependency-groups.dev`, so it is absent from every fast shard
    (`python-ci.yml`'s `test` job: `pip install -e '.[test,embeddings]'` +
    `uv sync --frozen`) — and this DB-free file IS in the fast pool. An
    unconditional import here raised `ModuleNotFoundError: No module named
    'psycopg2'` out of both callers: `2 failed, 19 passed` on the URI-less pool,
    which reds `test (x)` on the PR and on the push to `main`.

    The registry cross-check therefore runs WHERE THE DRIVER EXISTS (a local
    `uv sync --extra postgres`, and the `test-record-postgres` leg, which
    installs `.[test,postgres]` and is where this file is now registered for
    that reason) and is skipped by ABSENCE of the driver, never by an unreadable
    value. The assertions that ARE the test — the SQLSTATE classification, the
    counter, the `RetryableAllocationFailure`/`PositionCollision` split — run
    unconditionally, so only the ORACLE can be degraded, never the behaviour
    under test.
    """
    try:
        from psycopg2 import errors as pg_errors
    except ImportError:  # pragma: no cover - exercised on the URI-less pool
        pg_errors = None

    expected = {
        "40001": "SerializationFailure",
        "40P01": "DeadlockDetected",
        "57014": "QueryCanceled",
        "23505": "UniqueViolation",
    }[sqlstate]
    if pg_errors is not None:
        assert pg_errors.lookup(sqlstate).__name__ == expected, (
            f"psycopg2 maps {sqlstate} to {pg_errors.lookup(sqlstate).__name__}, "
        f"not {expected} — the sqlstate this suite names is not the driver's")

    class _DriverError(Exception):
        pass

    error = _DriverError("simulated")
    error.pgcode = sqlstate            # type: ignore[attr-defined]
    return error


def test_each_retryable_sqlstate_is_named_and_counted() -> None:
    """FINDING (M5): nothing on this path handles `40001`, `40P01` or `57014`, and
    there is no retry anywhere. Each class is now NAMED, COUNTED, and raised as
    retryable — the caller retries the whole transaction, this module never does.

    `40001` is additionally unreachable at arm (the isolation guard refuses a
    non-READ-COMMITTED transaction), so its counter is the tripwire that says the
    guard was bypassed.
    """
    for state in ("40001", "40P01", "57014"):
        ra.reset_metrics()
        with pytest.raises(ra.RetryableAllocationFailure) as caught:
            ra.raise_named(_fake_error(state), where="the position allocation")
        assert caught.value.sqlstate == state
        assert ra.metrics()[caught.value.metric] == 1, (
            f"{state} was raised but no counter moved — the class is not "
            f"observable")


def test_a_duplicate_position_is_not_retryable() -> None:
    """`23505` is a DEFECT, not a retry: the counter is behind the table, and
    retrying would fail identically forever."""
    ra.reset_metrics()
    with pytest.raises(ra.PositionCollision):
        ra.raise_named(_fake_error("23505"), where="the position allocation")
    assert ra.metrics()["duplicate_position"] == 1
    assert ra.metrics()["serialization_failure"] == 0


def test_the_door_speaks_the_counters_vocabulary() -> None:
    """The door's log token IS the allocator's counter name, or the operator
    cannot join the two (fresh-context review, P3).

    THE DEFECT. `_allocator_refusal_cause` returned `position_collision` for a
    `PositionCollision` while `record_allocator.metrics()` counted
    `duplicate_position`, so the WARNING's token matched no key on the surface
    the door's own docstring tells the operator to consult — and the remedy was
    reached through `.get(state, <generic fallback>)`, which is a silent miss
    rather than the gate the docstring claimed. Pre-fix `position_collision` is
    absent from `metrics()` and BOTH assertions below red.

    ⛔ The lookup stays DEFENSIVE (`.get`) at runtime on purpose: it runs inside
    the door's failure path, where a `KeyError` would replace the failure being
    reported. So the invariant is pinned HERE, not enforced by a raise.
    """
    counted = set(ra.metrics())
    for state in rw._ALLOCATOR_REMEDY:
        assert state in counted, (
            f"the door can print a remedy for {state!r}, which is not a counter "
            f"the allocator keeps ({sorted(counted)}) — the token the operator "
            f"reads in the WARNING cannot be joined to metrics()")
    for expected, exc in (
        ("cursor_not_active", ra.CursorNotActive("x")),
        ("duplicate_position", ra.PositionCollision("x")),
    ):
        got = rw._allocator_refusal_cause(exc)
        assert got == expected, (
            f"{type(exc).__name__} classifies as {got!r}, not {expected!r} — the "
            f"door's vocabulary has drifted from the allocator's counters")
        assert got in counted, (
            f"the door reports {got!r} for {type(exc).__name__}, which is not a "
            f"counter the allocator keeps ({sorted(counted)}) — the operator is "
            f"sent to a token metrics() cannot answer")
        assert got in rw._ALLOCATOR_REMEDY, (
            f"{got!r} has no remedy in _ALLOCATOR_REMEDY, so the WARNING falls "
            f"back to the generic text the docstring says cannot happen")


def test_the_allocator_refuses_a_non_read_committed_dsn() -> None:
    """The isolation guard reads the ONE place a transaction-scoped setting can
    be read, and refuses anything that is not READ COMMITTED — before the first
    allocation statement, so no position is issued under an isolation level that
    breaks "allocation order = commit order".

    A fake connection is enough for the REFUSAL half (the SQL is a constant and
    the comparison is the mechanism); the live sibling executes it against a
    transaction that really is SERIALIZABLE.
    """
    class _Cursor:
        def execute(self, sql, params=None):
            assert sql == ra.ISOLATION_SQL

        def fetchone(self):
            return ("serializable",)

        def close(self):
            pass

    class _Conn:
        def cursor(self):
            return _Cursor()

    ra.reset_metrics()
    with pytest.raises(ra.IsolationRefused):
        ra.require_read_committed(_Conn())
    assert ra.metrics()["isolation_refused"] == 1
    assert ra.READ_COMMITTED == "read committed"
    assert "transaction_isolation" in ra.ISOLATION_SQL, (
        "the guard must read the TRANSACTION-scoped setting; "
        "`default_transaction_isolation` is the session default and would answer "
        "about a different transaction than the one allocating")


def test_the_allocator_imposes_no_global_statement_timeout() -> None:
    """FINDING (M5): do NOT impose a global `statement_timeout` — it would bound
    legitimate backfill/replay transactions that share the connection. `57014` is
    handled as a retryable failure instead of pre-empted globally; a caller that
    wants a bound scopes it locally."""
    module = Path(ra.__file__).read_text(encoding="utf-8")
    for statement in (ra.SEED_SQL, ra.ALLOCATION_SQL):
        assert "statement_timeout" not in statement
    # The module may MENTION it (the docstring explains why not); it must not SET
    # it. A `SET statement_timeout` would be the global bound the finding forbids.
    assert not re.search(r"SET\s+(LOCAL\s+)?statement_timeout", module, re.I)


# ── the ruling's boundary ───────────────────────────────────────────────────

def test_the_allocator_does_not_move_the_graph_event_coordinate() -> None:
    """The reopen authorises ONE thing: the record allocates and the record
    carries the ordinal. It does not authorise `:GraphEvent` to change
    coordinate, and issued cursors ARE `:GraphEvent.seq`.

    This is the cheap mechanical half: the allocator module names neither the
    graph's event stream nor the cursor encoding, so it cannot have re-pointed
    them. The other half is a non-change (no diff in `sdk.py`/`event_store.py`)
    and is stated in the PR body, not asserted vacuously here.
    """
    # The MODULE DOCSTRING names `:GraphEvent` on purpose (it states the
    # boundary), so the assertion is on what the module EXECUTES and IMPORTS.
    for statement in (ra.SEED_SQL, ra.ALLOCATION_SQL):
        for forbidden in ("GraphEvent", "graph_event", "next_cursor"):
            assert forbidden not in statement
    module = Path(ra.__file__).read_text(encoding="utf-8")
    assert "import tortoise.log" not in module
    assert "from .log" not in module and "from tortoise.log" not in module
    assert "event_store" not in module
    assert "sdk" not in module.split('"""', 2)[2], (
        "the allocator imports or references the SDK's cursor surface")


def test_the_plan_builder_loads_without_the_package():
    """A REGRESSION PIN, not a style preference.

    `supabase/tests/pglite/validate.mjs` loads `tortoise/record_projection.py` BY
    FILE PATH and registers it as a package-less module, so ANY relative import
    reachable from `replay_plan` raises `ImportError: attempted relative import
    with no known parent package` — and the schema drill dies at its `#7969 record
    projection` step, which is what the first version of this change did
    (MEASURED: the drill's failure was the only place it surfaced; no unit test
    ran that code path package-less).

    Executed in a SUBPROCESS with the module's own directory NOT importable as a
    package, because an in-process call would find `tortoise` already in
    `sys.modules` and pass for the wrong reason.
    """
    import subprocess
    import sys

    module = Path(__file__).resolve().parent.parent / "tortoise" / "record_projection.py"
    script = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("record_projection", {str(module)!r})
rp = importlib.util.module_from_spec(spec)
sys.modules["record_projection"] = rp
spec.loader.exec_module(rp)
plan = rp.replay_plan([], org_id="o", graph_id="g")
print("STATEMENTS", len(plan))
"""
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, cwd="/")
    assert "relative import" not in done.stderr, (
        "replay_plan reached a RELATIVE IMPORT under the package-less load the "
        f"PGlite harness uses:\n{done.stderr}")
    assert done.returncode == 0, done.stderr


def test_the_allocation_sql_has_one_definition() -> None:
    """FINDING (review): the statement was moved to `record_projection` so the
    PGlite drill's package-less load works. A move that left a COPY behind in
    `record_allocator` would drift the moment either side is edited, and the
    tests would keep passing on whichever copy they import."""
    assert ra.ALLOCATION_SQL is rp.ALLOCATION_SQL, (
        "record_allocator holds its own copy of the allocation SQL")
    assert ra.ALLOCATION_STATEMENT_PREFIX is rp.ALLOCATION_STATEMENT_PREFIX
    assert ra.RECORD_CURSOR_TABLE == rp.RECORD_CURSOR_TABLE
    assert (ra.MODE_SEEDED, ra.MODE_ACTIVE, ra.MODE_HALTED) == (
        rp.MODE_SEEDED, rp.MODE_ACTIVE, rp.MODE_HALTED)
    # ⛔ AND THE COUNTER READ IS ONE SPELLING TOO (#9190). `replay()`'s
    # enumerated-replay guard asks exactly what `read_cursor` reads, so a
    # second hand-written copy could drift into asking a DIFFERENT question —
    # and the two would then disagree about whether a graph is cut over, which
    # is the one fact both exist to establish.
    assert rp.CURSOR_PRESENCE_SQL == ra.READ_SQL, (
        "`replay`'s guard and `read_cursor` read the counter with two "
        f"different statements:\n{rp.CURSOR_PRESENCE_SQL!r}\n{ra.READ_SQL!r}")


def test_an_empty_batch_allocates_nothing() -> None:
    """FINDING (review): `replay([], allocate=True)` reached the allocation
    validator with no numbers returned and reported `CursorNotActive` — a FALSE
    diagnosis of a non-event, because `write([])` is APPLIED and consumes no
    position.

    The connection records every statement, so "allocates nothing" is asserted as
    EXECUTED SQL, not as a return value."""
    # ⛔ THE STUB ANSWERS PER STATEMENT, not per call: the plan's prologue VERIFIES
    # the tenant scope by reading `(tenant, bypassing)` back, so a hard-coded
    # `fetchone` makes the scope check fail — which is how the first version of
    # this test failed, with the REAL error and for the wrong reason.
    class _Cursor:
        def __init__(self, parent):
            self.parent = parent
            self.rowcount = 1
            self._sql = ""

        def execute(self, sql, params=None):
            self.parent.executed.append((sql, params))
            self._sql = sql
            self.rowcount = 1

        def fetchall(self):
            return []

        def fetchone(self):
            if self._sql == ra.ISOLATION_SQL:
                return ("read committed",)
            if self._sql == rp.TENANT_SCOPE_SQL:
                return ("o", False)
            return None

        def close(self):
            pass

    class _Conn:
        def __init__(self):
            self.executed = []

        def cursor(self):
            return _Cursor(self)

    conn = _Conn()
    report = rp.replay([], conn, org_id="o", graph_id="g", allocate=True)
    assert (report.events, report.first_seq, report.last_seq) == (0, 0, 0), (
        f"an empty batch reported a block: {report}")
    allocation = [s for s, _ in conn.executed
                  if s.lstrip().startswith(ra.ALLOCATION_STATEMENT_PREFIX)]
    assert not allocation, (
        f"an empty batch EXECUTED the allocation statement ({len(allocation)}x) — "
        f"it would move the counter for zero records")


def test_read_cursor_scopes_the_connection_before_it_reads() -> None:
    """FINDING (review, P2): `read_cursor` read `record_cursor` WITHOUT setting
    the tenant, and the table's policy filters on `app.current_org_id` — so RLS
    returned ZERO ROWS and the reader reported "not cut over" for a graph that was
    allocating. A silent zero-row is indistinguishable from an absent row.

    The order is the assertion: the tenant scope must be the FIRST statement on
    the connection, before the read.
    """
    class _Cursor:
        def __init__(self, parent):
            self.parent = parent
            self._sql = ""

        def execute(self, sql, params=None):
            self.parent.executed.append((sql, params))
            self._sql = sql

        def fetchone(self):
            # The scope's read-back, then the cursor row itself — the two reads
            # this call makes, told apart by the statement that produced them.
            if self._sql == rp.TENANT_SCOPE_SQL:
                return ("o", False)
            if self._sql == ra.READ_SQL:
                return (7, ra.MODE_ACTIVE)
            return None

        def close(self):
            pass

    class _Conn:
        def __init__(self):
            self.executed = []

        def cursor(self):
            return _Cursor(self)

    conn = _Conn()
    assert ra.read_cursor(conn, org_id="o", graph_id="g") == (7, ra.MODE_ACTIVE)
    assert conn.executed, "read_cursor executed nothing"
    first_sql, first_params = conn.executed[0]
    assert "current_org_id" in first_sql, (
        f"the FIRST statement was {first_sql!r} — the read ran before the tenant "
        f"scope, so the policy would filter every row away")
    assert first_params == ("o",)
    assert ra.READ_SQL in [sql for sql, _ in conn.executed], (
        "the read itself is missing")
