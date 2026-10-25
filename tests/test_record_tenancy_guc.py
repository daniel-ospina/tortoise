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
7. **The CONNECTING ROLE is verified, not assumed** (#9600 review cycle 2). The
   GUC read-back proves the tenant took effect; it says nothing about whether
   the policies are evaluated at all. ``scope_connection`` refuses a superuser,
   a ``BYPASSRLS`` role, a member of either, and a table OWNER — the four
   measured bypasses — so a ``postgres``/``service_role`` DSN fails closed on
   the first write instead of writing across tenants with the report saying
   APPLIED. An unreadable posture is refused too: an unverifiable role is not a
   verified one.
8. **The READ door installs the same scope** (#9600 review cycle 2).
   ``record-reconcile`` opens its own connection from the SAME
   ``TORTOISE_RECORD_DSN`` and reads through ``cache_projection.read_record_view``
   — which therefore sets the prologue (and the role gate) before its first
   ``SELECT``. Without it, an ARMED reconciler on the app-role DSN would have read
   zero rows — every cache key ``extra_in_cache`` with ``missing_in_cache = 0``,
   the chokepoint-violation signature, not a lag — and reported a coherent pair
   as DIVERGENT (exit 3). That is the counterfactual the read-back prevents, not
   an observed event: the DSN is unset on the deployment.
9. **The identity sequence is granted in its own right** (#9600 review cycle 2).
   ``public.relation.id`` is ``GENERATED ALWAYS AS IDENTITY``, so the app role
   needs ``USAGE`` on ``relation_id_seq``: a table ``INSERT`` grant does not
   confer ``nextval``, and the failed insert aborts the whole replay
   transaction. Derived from the CREATE-TABLE migrations, never restated.

⛔ WHAT THIS FILE DELIBERATELY DOES NOT DO: prove a cross-tenant read returns
nothing. That needs a database — a fake connection cannot have a policy
evaluated against it, and a test that asserted it here would be asserting on a
string. The SQL suite owns it, and the two are registered together.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tortoise import cache_projection as cp
from tortoise import record_projection as rp
from tortoise import record_writer as rw

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"
TENANCY_MIGRATION = MIGRATIONS / "20261025000001_record_tenancy_role_and_guc.sql"

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
        self._conn.last_sql = sql
        if sql == rp.TENANT_GUC_SQL:
            self._conn.guc_set = params[0] if params else None

    def fetchone(self) -> tuple[Any, ...] | None:
        # `replay()` reads the tenant GUC BACK after setting it (a
        # `set_config(..., true)` that set nothing is silent for a
        # UPDATE/DELETE-only plan) and, on the SAME statement, the connecting
        # role's posture (#9600 review cycle 2). The recorder answers both from
        # the connection being modelled: a test OVERRIDES `guc_lost` to model a
        # lossy connection and `role_posture` to model a bypassing one.
        if self._conn.last_sql == rp.TENANT_SCOPE_SQL:
            if self._conn.role_posture is _UNREADABLE:
                return None
            if self._conn.mapping_rows:
                return {"tenant": self._conn.guc_reports,
                        "bypassing": self._conn.role_posture}
            return (self._conn.guc_reports, self._conn.role_posture)
        return (self._conn.guc_reports,)

    def fetchall(self) -> list[Any]:
        # The READ door's shapes (`record-reconcile`): this recorder holds no
        # rows, because the reader tests assert on the STATEMENTS it issued and
        # on the scope preceding them, never on projected data.
        return []

    # ClassVar, not a mutable instance default (RUF012): this double mimics
    # psycopg2's `cursor.description`, which the readers only ever read.
    description: ClassVar[list[Any]] = []


#: Sentinel: `pg_roles` returned no row for `current_user` (the posture is
#: unreadable, and an unverifiable role must be refused, not assumed safe).
_UNREADABLE = object()


class RecordingConnection:
    """Remembers every statement it was asked to execute, and nothing else."""

    def __init__(self, guc_lost: bool = False, role_posture: Any = False,
                 mapping_rows: bool = False) -> None:
        self.executed: list[tuple[str, list[Any] | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.guc_set: Any = None
        self.last_sql: str | None = None
        #: `True` models a connection whose transaction-local `set_config` did
        #: nothing (autocommit, or a pooler that dropped the context).
        self.guc_lost = guc_lost
        #: `False` = a non-bypassing role (the decision's app role); `True` = a
        #: BYPASSRLS/superuser/owner role (the DSN trap this file pins).
        self.role_posture = role_posture
        #: `True` models a `RealDict`-style cursor, which `read_record_view`
        #: advertises support for: the scope row arrives as a MAPPING, so the
        #: prologue must read it by key (`tenant`, `bypassing`) and not by index.
        self.mapping_rows = mapping_rows

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
        """Everything that is not the scope (prologue + its verification).

        The prologue is `set_config(...)` and the verify statement READS the GUC
        back (`current_setting(...)`) on the same round trip as the connecting
        role's posture, so each is named rather than filtered by position.
        """
        # Identity, not substring (#9600 review cycle 5, P3): a statement that
        # merely mentioned `current_setting` used to disappear from this list,
        # which is exactly the "statement the seam invented" this pins.
        return [(sql, params) for sql, params in self.executed
                if sql != rp.TENANT_GUC_SQL and sql != rp.TENANT_SCOPE_SQL]


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


def test_a_bypassing_role_is_refused_before_the_plan_is_applied() -> None:
    """#9600 review cycle 2 (P2): the GUC is verified, and now the ROLE is too.

    A `TORTOISE_RECORD_DSN` naming `postgres` (the table owner — R1's documented
    pre-change DSN shape) or `service_role` (`BYPASSRLS`) satisfies EVERY other
    check this PR adds: the prologue sets the GUC, the read-back returns it, and
    the report says APPLIED — while the policies are never evaluated, so the
    write crosses tenants. Nothing in the tree caught that before this pin; the
    connection is now refused, before a plan statement runs.
    """
    conn = RecordingConnection(role_posture=True)

    with pytest.raises(rp.RecordProjectionError, match="BYPASSES RLS"):
        rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.plan_statements() == [], (
        "a plan statement ran on a connection whose role bypasses RLS — the "
        "tenancy boundary was absent while the replay reported APPLIED")


def test_an_unreadable_role_posture_is_refused_not_assumed() -> None:
    """An unverifiable role is not a verified one: no pg_roles row ⇒ refuse.

    The fail-open shape is the `None` one — a query that returned nothing read as
    "not bypassing" would make the role check decorative on exactly the
    connection whose role the query could not describe.
    """
    conn = RecordingConnection(role_posture=_UNREADABLE)

    with pytest.raises(rp.RecordProjectionError, match="could not be verified"):
        rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.plan_statements() == []


def test_the_scope_statement_names_both_columns_and_uses_usage_not_member() -> None:
    """The two properties the mapping read and the gate both depend on.

    `_apply_tenant_scope` reads this row by KEY when the cursor is a `RealDict`
    one, so both columns must carry the aliases the reader uses: without
    `AS bypassing` the second column is named `?column?` and the mapping read
    raises `KeyError` (#9600 review cycle 5, P3 — the fake in this file cannot
    catch that, because it invents the keys).

    `'USAGE'` rather than `'MEMBER'` is deliberate: `NOINHERIT` / `WITH INHERIT
    FALSE` makes the permissive policy inapplicable and `pg_has_role(...,'USAGE')`
    false, so the two agree — pinned here because a silent `USAGE -> MEMBER` edit
    would make the gate permissive toward exactly that grant.
    """
    sql = rp.TENANT_SCOPE_SQL

    assert "AS tenant" in sql, "the tenant column has no alias — a mapping row cannot be read"
    assert "AS bypassing" in sql, (
        "the role-posture column has no alias — `row['bypassing']` on a RealDict "
        "cursor raises KeyError on the first statement either door runs")
    assert "pg_has_role(r.oid, g.oid, 'USAGE')" in sql, (
        "the membership leg must read 'USAGE': a non-inheriting grant makes the "
        "policy inapplicable, and 'MEMBER' would call that harmless")
    assert "pg_has_role(r.oid, c.relowner, 'USAGE')" in sql


def test_the_runtime_gate_statement_is_executed_here() -> None:
    """The posture suite RUNS this statement; keep the two copies in step.

    Every Python test fakes the scope row, so a syntax break or a dropped leg in
    `TENANT_SCOPE_SQL` was invisible to the whole tree (#9600 review cycle 5, P3).
    `supabase/tests/20261022000001_record_tenancy_posture.sql` §11 now executes a
    COPY of it for the app role and for `service_role` and requires the two
    answers to DISAGREE — a gate that says `false` for everyone is as broken as
    one that says `true`. This test is the pin between the copy and the module:
    the four predicates must be in both, so drift reddens here instead of waiting
    for a reader to notice.
    """
    posture = (MIGRATIONS.parent / "tests" /
               "20261022000001_record_tenancy_posture.sql").read_text(encoding="utf-8")

    for predicate in (
        "r.rolsuper OR r.rolbypassrls",
        "pg_has_role(r.oid, g.oid, 'USAGE')",
        "c.relowner = r.oid",
        "pg_has_role(r.oid, c.relowner, 'USAGE')",
    ):
        assert predicate in rp.TENANT_SCOPE_SQL, (
            f"the runtime gate no longer carries {predicate!r}")
        assert predicate in posture, (
            f"the posture suite's executed copy of the gate no longer carries "
            f"{predicate!r} — the SQL lane is testing something else now")


def test_the_role_gate_reads_a_mapping_shaped_row_by_key() -> None:
    """#9600 review cycle 4 (P2): the scope is verified on BOTH cursor shapes.

    `read_record_view` advertises `dict`-shaped rows (a `RealDict` cursor, or a
    plain cursor's `description`) and its own `_rows()` accepts a `Mapping`. The
    new scope verify is the FIRST statement either door runs, so unpacking it by
    position alone raised `KeyError: 0` on exactly the shape the reader
    documents as supported — before the first SELECT. Both columns are aliased so
    the mapping form is readable, and this pins it in both directions.
    """
    conn = RecordingConnection(mapping_rows=True)

    report = rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.plan_statements(), (
        "a mapping-shaped scope row stopped the plan from running entirely")
    assert report.statements > 0


def test_a_mapping_shaped_bypassing_row_is_still_refused() -> None:
    """The key the reader reads must be the key the gate means."""
    conn = RecordingConnection(mapping_rows=True, role_posture=True)

    with pytest.raises(rp.RecordProjectionError, match="BYPASSES RLS"):
        rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)

    assert conn.plan_statements() == []


def test_the_role_gate_is_one_statement_covering_all_four_bypasses() -> None:
    """One round trip, four bypasses, and none of them a role NAME.

    `rolsuper` and `rolbypassrls` are the direct shapes; `pg_has_role` catches
    membership (which re-opens the bypass while `rolbypassrls` still reads
    false — measured, and the migration header says so); the `relowner` leg
    catches the table OWNER, which is the `postgres` DSN the decision rejects.
    A hardcoded `pg_has_role(current_user, 'service_role', 'USAGE')` would raise
    on a Postgres that has no such role, so the membership leg is generic.
    """
    assert rp.TENANT_SCOPE_SQL.count("pg_has_role") == 2, (
        "the membership leg OR the ownership leg is missing — one of the four "
        "bypasses is not checked")
    for fragment in ("rolsuper", "rolbypassrls", "relowner", "pg_roles"):
        assert fragment in rp.TENANT_SCOPE_SQL, f"the role gate does not check {fragment}"

    conn = RecordingConnection()
    rp.replay(RECORDS, conn, org_id=ORG, graph_id=GRAPH)
    gates = [sql for sql, _ in conn.executed if sql == rp.TENANT_SCOPE_SQL]
    assert len(gates) == 1, f"{len(gates)} role-gate round trips for one write"
    assert conn.executed[0][0] == rp.TENANT_GUC_SQL, (
        "the tenant must be set before the statement that reads it back")


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


def test_the_live_door_refuses_a_bypassing_connection_as_a_write_failure() -> None:
    """The role refusal reaches the CALLER — it is not swallowed by the seam.

    A refusal that the seam converted into `APPLIED`, or into a success with
    zero statements, would be the same silent fail-open one level up: the
    capture would count a durable write that never happened. It surfaces as
    `RecordWriteError` (the class the live door bounds and counts) with the
    projection's refusal as its `__cause__`, and nothing was executed or
    committed.
    """
    conn = RecordingConnection(role_posture=True)
    writer = rw.RecordWriter(dsn="postgresql://record/db",
                             connect=lambda _dsn: conn)

    with pytest.raises(rw.RecordWriteError) as excinfo:
        writer.write(RECORDS, org_id=ORG, graph_id=GRAPH)

    assert isinstance(excinfo.value.__cause__, rp.RecordProjectionError), (
        "the seam re-wrapped the refusal without the projection's cause — the "
        "operator cannot tell a bypassing DSN from an engine error")
    assert "BYPASSES RLS" in str(excinfo.value.__cause__)
    assert conn.plan_statements() == [], "a plan statement ran anyway"
    assert conn.commits == 0


def test_an_unarmed_write_sets_nothing_and_opens_nothing() -> None:
    """No DSN ⇒ NOT_ARMED: no connection, hence no prologue to set."""
    writer = rw.RecordWriter(dsn=None)

    result = writer.write(RECORDS, org_id=ORG, graph_id=GRAPH)

    assert result.outcome is rw.RecordWriteOutcome.NOT_ARMED


# ════════════════════════════════════════════════════════════════════════════
# 2b. The READ door installs the SAME scope — a reader is not exempt
# ════════════════════════════════════════════════════════════════════════════


def test_the_reader_installs_the_tenant_scope_before_it_reads() -> None:
    """#9600 review cycle 2 (P1): the read half of the posture.

    `record-reconcile` opens its OWN connection from the SAME
    `TORTOISE_RECORD_DSN` and calls `read_record_view`. Before this pin the
    prologue lived only on the write door, so an ARMED reconciler on the
    app-role DSN this PR mandates WOULD have read ZERO rows from every record
    table (a missing GUC is "no rows", never an error), landed every cache key
    in `extra_in_cache` with `missing_in_cache = 0` (the chokepoint-violation
    signature, not a lag) and exited 3 — "divergence OBSERVED" — on a coherent
    pair. That is the counterfactual the read-back prevents, not an observed
    event: the DSN is unset on the deployment. The verb's own docstring: a
    wrong VERDICT is worse than no verdict.
    """
    conn = RecordingConnection()

    view = cp.read_record_view(conn, org_id=ORG, graph_id=GRAPH)

    assert conn.prologues() == [[ORG]], (
        "the reader read the record WITHOUT scoping the connection to the org "
        "it filters on — under the app role every policy reads NULL and returns "
        "zero rows, silently")
    assert conn.executed[0][0] == rp.TENANT_GUC_SQL, (
        "the scope must precede the first SELECT — a row read before the GUC is "
        "set is a row read across tenants")
    assert any(sql == rp.TENANT_SCOPE_SQL for sql, _ in conn.executed), (
        "the reader did not verify the connecting role's posture")
    # …and the scope is set ONCE, from the org_id the reader was GIVEN (one
    # value feeds the GUC and the predicates, so they cannot disagree).
    assert len(conn.prologues()) == 1
    assert view.record_keys == ()


def test_the_reader_refuses_a_bypassing_role() -> None:
    """A read-only door needs the role gate at least as much as a writer.

    A bypassing reader does not merely write across tenants — it READS every
    tenant's rows, and the reconciler would then compare another tenant's record
    against this tenant's cache and report a divergence nobody can act on.
    """
    conn = RecordingConnection(role_posture=True)

    with pytest.raises(rp.RecordProjectionError, match="BYPASSES RLS"):
        cp.read_record_view(conn, org_id=ORG, graph_id=GRAPH)


def test_the_reconciler_verb_reads_through_that_same_reader() -> None:
    """The scope is installed where the verb reads, not only where it writes.

    Ordering pinned at the seam: `read_record_view` installs the scope BEFORE
    its first SELECT, so there is no window in which the verb reads unscoped
    rows. The verb's own arm is driven end-to-end in
    `tests/test_record_reconcile_trigger.py::test_the_verbs_OWN_connection_is_tenant_scoped`.
    """
    conn = RecordingConnection()

    cp.read_record_view(conn, org_id=ORG, graph_id=GRAPH)

    scope_at = conn.executed.index((rp.TENANT_GUC_SQL, [ORG]))
    first_select = next(
        i for i, (sql, _) in enumerate(conn.executed)
        if sql.startswith("SELECT * FROM public."))
    assert scope_at < first_select, (
        "the reader selected rows before installing the tenant scope")
    assert conn.executed[scope_at + 1][0] == rp.TENANT_SCOPE_SQL, (
        "the prologue was not verified (GUC read-back + role posture) before "
        "the read")


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
    #
    # #9600 review cycle 2 (P2): `journal_event` is the ONE table whose grant is
    # deliberately narrower — `SELECT, INSERT` only. The journal is append-only
    # by trigger, and that trigger's single escape is a GUC
    # (`app.record_journal_purge`) any role may `set_config`, so a DELETE grant
    # would let the boundary role erase its own audit trail. Asserting the
    # narrow grant in BOTH directions (present here, absent the wider one) means
    # a "make the grants uniform" tidy-up reddens rather than silently widening
    # the boundary.
    _APPEND_ONLY = "journal_event"
    _WRITE_PRIVS = "SELECT, INSERT, UPDATE, DELETE"
    _NARROW_PRIVS = "SELECT, INSERT"
    missing_grants = [
        t for t in rp.THE_RECORD_TABLES
        if not re.search(
            rf"GRANT\s+{_NARROW_PRIVS if t == _APPEND_ONLY else _WRITE_PRIVS}"
            rf"\s+ON\s+public\.{re.escape(t)}\s+TO\s+tortoise_record_app\s*;", text)
    ]
    missing_policies = [
        t for t in rp.THE_RECORD_TABLES
        if f"CREATE POLICY {t}_app_tenant ON public.{t}" not in text
    ]

    assert not missing_grants, (
        f"the tenancy migration grants the app role no privilege on {missing_grants}"
    )
    assert not re.search(
        rf"GRANT\s+{_WRITE_PRIVS}\s+ON\s+public\.{_APPEND_ONLY}\s+"
        rf"TO\s+tortoise_record_app\s*;", text), (
        f"{_APPEND_ONLY} is granted UPDATE/DELETE — the append-only trigger's "
        "one escape is a GUC any role may set, so that grant hands the boundary "
        "role the erasure path it is supposed to be bounded by"
    )
    assert not missing_policies, (
        f"the tenancy migration adds no tenant policy for {missing_policies} — "
        "those tables' RLS state is unexplained by the recorded decision"
    )


def test_every_identity_sequence_the_record_declares_is_granted() -> None:
    """#9600 review cycle 2 (P1): a table INSERT does not confer `nextval`.

    `public.relation.id` is `bigint GENERATED ALWAYS AS IDENTITY`, so an
    app-role INSERT into `relation` needs `USAGE` on `relation_id_seq` in its
    OWN right — the sequence carries a separate ACL and its owner is the
    migration applier. Without it every `INSERT INTO public.relation` raises
    `permission denied for sequence` (42501), and because the raised statement
    aborts the replay transaction, a capture's CONTAINS edge and every
    direct-edge record are lost — not just the edge.

    `has_table_privilege(..., 'INSERT')` reads TRUE while the insert still
    fails, so the migration text is what decides it here, and the posture suite
    lands a real `relation` row as the positive proof.

    Derived from the CREATE-TABLE migrations, never restated: a future identity
    column arrives with its own sequence and therefore its own red.
    """
    tenancy = _migration_text(TENANCY_MIGRATION.name)
    declares: list[tuple[str, str]] = []          # (table, sequence)
    for name in rp.RECORD_MIGRATIONS:
        text = _migration_text(name)
        for table, body in re.findall(
                r"CREATE TABLE IF NOT EXISTS public\.(\w+) \((.*?)\n\);",
                text, re.DOTALL):
            if "AS IDENTITY" in body:
                declares.append((table, f"{table}_id_seq"))

    assert declares, (
        "no record migration declares an identity column — this pin would be "
        "vacuous, and the sequence grant has nothing to prove")
    missing = [
        f"public.{seq}" for _, seq in declares
        if not re.search(
            rf"GRANT\s+USAGE\s+ON\s+SEQUENCE\s+public\.{re.escape(seq)}\s+"
            rf"TO\s+tortoise_record_app\s*;", tenancy)
    ]
    assert not missing, (
        f"the tenancy migration grants the app role no USAGE on {missing} — a "
        "table INSERT privilege does not confer nextval, so every insert into "
        "the identity-bearing record table raises 42501 and the whole replay "
        "transaction aborts")


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


def test_the_role_comment_cannot_abort_the_migration() -> None:
    """#9600 review cycle 4 (P2): the comment is documentation, not a gate.

    `COMMENT ON ROLE` needs superuser **or ADMIN OPTION on the target role** —
    a different privilege from the `ALTER ROLE` guard above it. On a database
    where `tortoise_record_app` already exists and was created by somebody else
    (an operator's admin role in the SQL editor, a role restored from
    `pg_dumpall --roles`, a second applier), a bare statement raises
    `permission denied` and aborts the WHOLE tenancy posture — the same failure
    shape cycle 1 fixed for `ALTER ROLE`, with the same consequence: no grants,
    no policies, boundary off. The PGlite lane runs as superuser and cannot see
    it, so this is pinned as text: the comment must live inside a `DO` block
    that swallows exactly `insufficient_privilege`.
    """
    text = _migration_text(TENANCY_MIGRATION.name)

    assert "COMMENT ON ROLE tortoise_record_app" in text, (
        "the role's documented purpose must still be on the role")
    guarded = re.search(
        r"DO \$\$\s*\n\s*BEGIN\s*\n\s*COMMENT ON ROLE tortoise_record_app", text)
    assert guarded, (
        "`COMMENT ON ROLE` must be inside a DO block — unguarded, a "
        "non-admin applier aborts the entire tenancy posture on a role it did "
        "not create")
    tail = text[guarded.start():guarded.start() + 2000]
    assert re.search(r"EXCEPTION WHEN insufficient_privilege THEN\s*\n\s*NULL;\s*\n\s*END \$\$", tail), (
        "the comment's DO block must swallow `insufficient_privilege` and "
        "nothing else — a bare COMMENT, or a handler for the wrong class, is "
        "either fatal or a silent failure")
    bare = re.findall(r"^COMMENT ON ROLE tortoise_record_app", text, re.M)
    assert not bare, (
        "a top-level `COMMENT ON ROLE` bypasses the handler")
