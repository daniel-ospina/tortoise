"""The record's TENANCY POSTURE, pinned at the seam (R6, epic #7879).

WHAT THIS PINS. R6's target is *"a pgTAP test proving a cross-tenant read
returns 0 rows under the chosen posture"* plus a recorded decision naming the
connecting role. The DATABASE half of that (the policies, the role's attributes,
the cross-tenant read, the write-refusal) lives where a Postgres is available:
``supabase/tests/20261022000001_record_tenancy_posture.sql``, run by the PGlite
harness. This file holds the half a Postgres cannot speak about — the CODE that
makes the posture true — so it runs in every Python lane, with no DB and no
driver:

1. **The GUC the code sets is the GUC the schema reads.** ``TENANT_GUC`` is
   compared against every ``current_setting('…')`` name appearing in a record
   policy, parsed from the migrations. This is the one way the two halves drift
   apart without either side noticing: a renamed GUC makes every policy read
   NULL, and NULL is a silent empty read, not an error.
2. **It is transaction-local, never session-local.** The third argument of
   ``set_config`` is ``true``. A session ``SET`` is the failure mode, not the
   mechanism: under the pooler's transaction mode (:6543) it does not survive to
   the next transaction (a silent zero-row read), and under session mode (:5432,
   R1's DSN today) it survives onto a connection an application-side pool may
   hand to the NEXT tenant (another tenant's rows). Transaction-local is correct
   under both, so the DSN can move without a code change.
3. **It carries the org the plan is about to write, and only that one.** One
   value feeds the GUC and the rows, so a writer cannot file under one tenant
   while scoped to another (test-design surface S2). Pinned with two tenants on
   the SAME connection, in sequence.
4. **Totality is untouched.** An undeclared record type still raises having
   executed no statement at all — the prologue runs after the plan is
   materialized, so it cannot leak a setting for a write that never happened.
5. **Every record table is covered by the migration.** A table added to
   ``THE_RECORD_TABLES`` without its grant + tenant policy is a table whose RLS
   state the decision does not explain — R6's third target, made mechanical so
   the next table (#9555's) cannot quietly arrive unexplained.
6. **The app role is non-bypass, by statement and by intent.** The migration
   creates it ``NOBYPASSRLS`` and re-asserts the attribute on every apply; a
   ``BYPASSRLS`` here turns the whole posture into decoration, which is the
   trap this issue exists to close (``service_role`` looks like a tenancy
   boundary while bypassing RLS entirely).

⛔ WHAT THIS FILE DELIBERATELY DOES NOT DO: prove a cross-tenant read returns
nothing. That needs a database — a fake connection cannot have a policy
evaluated against it, and a test that asserted it here would be asserting on a
string. The SQL suite owns it, and the two are registered together.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tortoise import record_projection as rp
from tortoise import record_writer as rw

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
TENANCY_MIGRATION = MIGRATIONS / "20261022000001_record_tenancy_role_and_guc.sql"

ORG, GRAPH = "org-1", "graph-1"

#: A small valid segment: one semantic and one epistemic write, which between
#: them emit inserts into `node_identity`/`object`/`point` — enough for the
#: prologue's ORDER (before the plan) to be observable and non-vacuous.
RECORDS: list[dict[str, Any]] = [
    {"type": "PointAdded", "ts": "2026-10-22T00:00:00Z", "id": "p1",
     "point": {"id": "p1", "content": "a claim", "pointKind": "claim",
               "ep_alpha": 1.0, "ep_beta": 1.0}},
    {"type": "ObjectRegistered", "ts": "2026-10-22T00:00:01Z", "id": "o1",
     "name": "a document", "objectKind": "document"},
]


# ════════════════════════════════════════════════════════════════════════════
# A DB-API fake — a recorder, not a database
# ════════════════════════════════════════════════════════════════════════════


class RecordingCursor:
    def __init__(self, conn: RecordingConnection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Any = None) -> None:
        self._conn.executed.append((sql, None if params is None else list(params)))
        if sql == rp.TENANT_GUC_SQL:
            self._conn.guc_set = params[0] if params else None

    def fetchone(self) -> tuple[Any]:
        # #9600 review cycle 1 (P2): `replay()` reads the tenant GUC BACK after
        # setting it (a `set_config(..., true)` that set nothing is silent for
        # an UPDATE/DELETE-only plan). The recorder mirrors the value it was
        # asked to set unless a test OVERRIDES it to model a lossy connection.
        return (self._conn.guc_reports,)


class RecordingConnection:
    """Remembers every statement it was asked to execute, and nothing else."""

    def __init__(self, guc_lost: bool = False) -> None:
        self.executed: list[tuple[str, list[Any] | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.guc_set: Any = None
        #: `True` models a connection whose transaction-local `set_config` did
        #: nothing (autocommit, or a pooler that dropped the context).
        self.guc_lost = guc_lost

    @property
    def guc_reports(self) -> Any:
        return None if self.guc_lost else self.guc_set

    def cursor(self) -> RecordingCursor:
        return RecordingCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1

    def prologues(self) -> list[list[Any] | None]:
        return [params for sql, params in self.executed if sql == rp.TENANT_GUC_SQL]

    def plan_statements(self) -> list[tuple[str, list[Any] | None]]:
        """Everything that is not the prologue or its read-back."""
        return [(sql, params) for sql, params in self.executed
                if sql != rp.TENANT_GUC_SQL and "current_setting" not in sql]


def _migration_text(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


#: Every record migration — the four CREATE-TABLE files plus the forward ones —
#: read by NAME from the module's own registry, never by glob, so a file the
#: image does not read cannot make this test green (#9231's rule).
def _record_migration_text() -> str:
    return "\n".join(_migration_text(name) for name in rp.RECORD_MIGRATIONS)


# ════════════════════════════════════════════════════════════════════════════
# 1. The code's GUC is the schema's GUC — one name, checked against both sides
# ════════════════════════════════════════════════════════════════════════════

#: Every `current_setting('x.y', …)` a RECORD POLICY reads. Anchored on
#: `current_setting` in a policy body rather than on the file, because the
#: record migrations set other GUCs for other reasons (the purge flag) and a
#: file-wide grep would fold them into the same answer.
_POLICY_GUC = re.compile(
    r"USING\s*\([^)]*current_setting\(\s*'([^']+)'", re.IGNORECASE)


def test_the_code_sets_the_guc_the_record_policies_read() -> None:
    """A renamed GUC in either half is a silent empty read, not an error.

    Derived from the SQL, not restated in prose: the set of setting names the
    record's `USING` predicates read must be exactly `{TENANT_GUC}`. A second
    name in that set means some policy is scoped by something production code
    never sets (it would read NULL ⇒ zero rows forever); a missing one means
    the code sets a setting nothing reads.
    """
    read = set(_POLICY_GUC.findall(_record_migration_text()))

    assert read == {rp.TENANT_GUC}, (
        f"the record policies read {sorted(read)} but the code sets "
        f"{rp.TENANT_GUC!r} — a policy whose setting is never set returns "
        "NULL, which is zero rows and no error"
    )


def test_the_guc_is_transaction_local_and_hardcoded_not_interpolated() -> None:
    """`is_local=true`, and the org id travels as a BIND PARAMETER.

    The third argument of `set_config` is the whole pooler-safety story (§G1 of
    the issue): `true` = transaction-local. `false` would make the tenant a
    property of the connection, which is exactly what a pooled connection is
    not allowed to carry. The parameter is asserted separately: an org id
    interpolated into SQL is an injection surface the fold never has.
    """
    sql = rp.TENANT_GUC_SQL

    assert sql == "SELECT set_config('app.current_org_id', %s, true)"
    assert sql.count("%s") == 1, "the org id must be bound, never interpolated"
    assert sql.rstrip().endswith("true)"), (
        "is_local must be true — a session-scoped tenant leaks onto a pooled "
        "connection reused by the next tenant"
    )


# ════════════════════════════════════════════════════════════════════════════
# 2. The prologue is first, once, and carries THIS write's tenant
# ════════════════════════════════════════════════════════════════════════════


def test_the_prologue_is_the_first_statement_and_appears_once() -> None:
    """Before anything it scopes — a policy evaluated first would see NULL."""
    conn = RecordingConnection()
    rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.executed, "the replay executed nothing — the assertion is vacuous"
    assert conn.executed[0][0] == rp.TENANT_GUC_SQL
    assert len(conn.prologues()) == 1


def test_the_prologue_carries_the_org_the_plan_writes() -> None:
    """S2: the GUC's value and the rows' org id are ONE value, not two."""
    conn = RecordingConnection()
    rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.prologues() == [[ORG]]
    # …and the plan then binds that same org everywhere it names a tenant:
    # every parameter list that carries the org column carries THIS org.
    plan = rp.replay_plan(RECORDS, org_id=ORG, graph_id=GRAPH)
    assert plan, "the segment projected nothing — vacuous"
    executed_after = conn.plan_statements()
    assert [params for _, params in executed_after] == [
        list(s.params) for s in plan]


def test_a_lost_tenant_setting_refuses_instead_of_applying_unscoped() -> None:
    """#9600 review cycle 1 (P2): the prologue is READ BACK, not assumed.

    `set_config(..., true)` sets nothing on an autocommit connection (and a
    pooler can drop the transaction-local context). Every INSERT then fails
    ``WITH CHECK`` loudly, but an UPDATE/DELETE is gated by ``USING`` alone and
    simply affects ZERO rows — so a patch- or delete-only plan would be dropped
    while `replay()` reported APPLIED. The read-back makes that a refusal.
    """
    conn = RecordingConnection(guc_lost=True)

    with pytest.raises(rp.RecordProjectionError, match="tenant GUC"):
        rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.plan_statements() == [], (
        "the plan was applied after the tenant setting was lost — an unscoped "
        "UPDATE/DELETE would affect zero rows without raising")


def test_two_tenants_on_one_connection_each_get_their_own_prologue() -> None:
    """The failure this prevents: a connection reused across tenants.

    Two writes, two tenants, ONE connection — the shape an application-side pool
    produces. The second write must scope the connection to ITS tenant; if the
    GUC were hoisted to connect time (or made session-scoped), the second write
    would inherit the first tenant's setting and the policies would evaluate
    against the wrong org.
    """
    conn = RecordingConnection()
    rp.replay(RECORDS, conn, org_id="org_a", graph_id="graph_a")
    first_run = len(conn.executed)
    rp.replay(RECORDS, conn, org_id="org_b", graph_id="graph_b")

    assert conn.prologues() == [["org_a"], ["org_b"]]
    second = [params for _, params in conn.executed[first_run:]]
    assert second[0] == ["org_b"]
    assert all("org_a" not in (p or []) for p in second), (
        "the previous tenant leaked onto the reused connection"
    )


def test_the_pure_plan_emits_no_prologue() -> None:
    """`replay_plan` stays the journal→record declaration, with no session state.

    The plan is what the PGlite harness and every statement-shape test reads; a
    session statement inside it would make "the plan" mean two things.
    """
    plan = rp.replay_plan(RECORDS, org_id=ORG, graph_id=GRAPH)

    assert plan, "vacuous"
    assert all(s.sql != rp.TENANT_GUC_SQL for s in plan)


def test_totality_is_untouched_by_the_prologue() -> None:
    """An undeclared record type raises having executed NOTHING.

    The prologue runs after `replay_plan` materializes, so a refused segment
    never leaves a tenant set on a transaction whose write did not happen.
    """
    conn = RecordingConnection()

    with pytest.raises(rp.UnknownJournalRecordType):
        rp.replay([{"type": "NoSuchRecord", "ts": "T"}], conn,
                  org_id=ORG, graph_id=GRAPH)

    assert conn.executed == []


def test_the_live_door_sets_the_prologue_too() -> None:
    """The production door, not just the function: `write_live_record`.

    The seam and the live door are two spellings of one path; pinning only
    `replay` would leave the door that production actually calls unproven.
    """
    conn = RecordingConnection()
    writer = rw.RecordWriter(dsn="postgresql://record/db",
                             connect=lambda _dsn: conn)

    writer.write(RECORDS, org_id=ORG, graph_id=GRAPH)

    assert conn.prologues() == [[ORG]]
    assert conn.executed[0][0] == rp.TENANT_GUC_SQL


def test_an_unarmed_write_sets_nothing_and_opens_nothing() -> None:
    """No DSN ⇒ NOT_ARMED: no connection, hence no prologue to set."""
    writer = rw.RecordWriter(dsn=None)

    result = writer.write(RECORDS, org_id=ORG, graph_id=GRAPH)

    assert result.outcome is rw.RecordWriteOutcome.NOT_ARMED


# ════════════════════════════════════════════════════════════════════════════
# 3. The migration explains EVERY record table — R6's third target, mechanical
# ════════════════════════════════════════════════════════════════════════════


def test_the_migration_covers_every_record_table() -> None:
    """"0 record tables whose RLS state is unexplained by the decision."

    A table in `THE_RECORD_TABLES` with no grant and no tenant policy for the
    application role has an RLS state the decision does not cover: the writer
    would connect as a role either unable to touch it (a dead write) or, worse,
    covered only by a `service_role` policy that bypasses. Checked against the
    module's own tuple, so the NEXT record table (#9555's) cannot arrive
    unexplained.
    """
    text = _migration_text(TENANCY_MIGRATION.name)

    # #9600 review cycle 1 (P2): this half used to test
    # `f"ON public.{t}" not in text` — and EVERY table also carries
    # `CREATE POLICY {t}_app_tenant ON public.{t}`, so the substring was always
    # present and a DELETED grant could not redden the test (measured: the
    # assertion passed with the grant block removed). The grant itself is
    # asserted now, per table and per privilege, against the exact statement the
    # migration writes.
    _PRIVS = "SELECT, INSERT, UPDATE, DELETE"
    missing_grants = [
        t for t in rp.THE_RECORD_TABLES
        if not re.search(
            rf"GRANT\s+{_PRIVS}\s+ON\s+public\.{re.escape(t)}\s+"
            rf"TO\s+tortoise_record_app\s*;", text)
    ]
    missing_policies = [
        t for t in rp.THE_RECORD_TABLES
        if f"CREATE POLICY {t}_app_tenant ON public.{t}" not in text
    ]

    assert not missing_grants, (
        f"the tenancy migration grants the app role no privilege on {missing_grants}"
    )
    assert not missing_policies, (
        f"the tenancy migration adds no tenant policy for {missing_policies} — "
        "those tables' RLS state is unexplained by the recorded decision"
    )


def test_every_app_policy_binds_reads_and_writes_to_the_same_guc() -> None:
    """`USING` AND `WITH CHECK`, both on the tenant GUC, spelled identically.

    `USING` alone leaves INSERT unchecked (a writer could file another tenant's
    row); `WITH CHECK` alone leaves reads and row-targeted UPDATE/DELETE
    unbounded. Each policy must carry both, and the two must be the SAME
    predicate: two clauses derived from one expression is what stops the read
    and write halves of the boundary from drifting into different definitions
    of "this tenant".
    """
    text = _migration_text(TENANCY_MIGRATION.name)
    # One block per policy, up to its terminating `;` — a clause-level regex
    # cannot be used here because the predicate itself contains parentheses
    # (`current_setting(...)`).
    blocks = re.findall(
        r"CREATE POLICY (\w+_app_tenant) ON public\.(\w+)\s+"
        r"FOR ALL TO tortoise_record_app\s+(.*?);",
        text, re.DOTALL,
    )
    predicate = f"org_id = current_setting('{rp.TENANT_GUC}', true)"

    assert len(blocks) == len(rp.THE_RECORD_TABLES), (
        f"{len(blocks)} app policies for {len(rp.THE_RECORD_TABLES)} record tables"
    )
    for name, table, body in blocks:
        assert f"USING ({predicate})" in body, f"{name} ({table}): USING is not the tenant predicate"
        assert f"WITH CHECK ({predicate})" in body, (
            f"{name} ({table}): WITH CHECK is missing or uses a different predicate"
        )


def test_the_app_role_is_non_bypass_and_reasserted_on_every_apply() -> None:
    """`NOBYPASSRLS` is the decision; `BYPASSRLS` would make it decoration.

    `service_role` looks like a tenancy boundary while bypassing RLS entirely,
    which is trap (i) of this issue. The role is created non-bypass AND
    re-asserted on every apply — an operator who flipped the attribute would
    otherwise silently switch the boundary off on a live deployment.

    #9600 review cycle 1 (P1): the re-assert is SUPERUSER-ONLY (Postgres refuses
    `ALTER ROLE … NOSUPERUSER`/`… NOBYPASSRLS` to any other role), so it lives in
    a `DO` block, and a non-superuser applier must FAIL CLOSED on drift rather
    than silently skip the assertion. This pins BOTH halves: the re-assert
    exists, and the drift-without-superuser arm raises.
    """
    text = _migration_text(TENANCY_MIGRATION.name)

    assert "CREATE ROLE tortoise_record_app " in text
    assert re.search(
        r"EXECUTE 'ALTER ROLE tortoise_record_app '"
        r"\s*'NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE'", text), (
        "the attribute must be re-asserted on every apply by an applier that "
        "CAN re-assert it — a one-time CREATE leaves a flipped BYPASSRLS "
        "undetected"
    )
    assert re.search(r"_applier_is_super boolean := ", text), (
        "the re-assert must be guarded: `ALTER ROLE … NOBYPASSRLS` is a "
        "superuser-only clause, and a bare ALTER ROLE aborted the whole "
        "migration on a hosted non-superuser applier (measured)"
    )
    assert re.search(r"ELSIF _drifted THEN\s*\n\s*RAISE EXCEPTION", text), (
        "a non-superuser applier that cannot re-assert a DRIFTED role must "
        "raise — skipping the assertion silently is the fail-open"
    )
    assert not re.search(r"CREATE ROLE tortoise_record_app[^']*BYPASSRLS(?!\s*=)",
                         text.replace("NOBYPASSRLS", ""))
