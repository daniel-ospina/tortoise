"""#9078 — the record is written in PRODUCTION, and this is the executable claim.

THE MEASURED GAP. `#7950`'s capstone returned S2 **NOT-GREEN**: `record_projection`,
`read_record_view`, `rebuild_from_record` and `reconcile` had **zero production
call sites**, so "Postgres is the system of record" (§2.0) was a claim about a
test fixture. The rebuild half was proven — `tests/test_cache_projection_live.py`
destroys the cache and rebuilds it from the record — but nothing in a user's
capture ever wrote that record.

WHAT THIS PINS, and each leg is a separate failure mode:

1. **THE PUBLIC PATH WRITES IT.** `TortoiseSDK.capture_session` — no probe
   module, no monkeypatched fold — appends to the journal and then projects that
   record into Postgres. Nothing here drives `record_projection` directly: the
   assertion is on what the production write path emitted.
2. **ONE ENGINE, NOT A SECOND MAPPING.** The statements the live path executes
   are EXACTLY the ones `record_projection.mutations_for` derives for the same
   journal records, in order. A live path with its own hand-written INSERTs
   would drift from the rebuild path silently — that is the defect class the
   record's totality/idempotence pins (`tests/test_record_projection.py`) exist
   to prevent, and they cannot see a caller that bypasses the engine.
3. **UNARMED IS BYTE-IDENTICAL.** With no `TORTOISE_RECORD_DSN` the capture
   emits no statement at all — the default deployment must not acquire a
   Postgres dependency or a round trip by accident.
4. **A HALF-CONFIGURED RECORD REFUSES.** A DSN without a tenant (org + graph)
   is refused and counted, never guessed: the record's tables are keyed by
   `(org_id, graph_id)` with a foreign key to `public.graphs`, so a guess is a
   cross-tenant write.
5. **A FAILED RECORD WRITE IS OBSERVED, AND LOSES NOTHING.** The journal append
   is the COMMIT POINT (§17.1: *"the append is the commit point"*), so a
   projection that fails after it cannot un-commit the write: the capture
   succeeds, the journal keeps the record, the failure is counted, and the
   healing path is the existing replay. A silent swallow would be this issue's
   own defect one door over.
6. **THE POSITION ROW IS DEFERRED, AND NAMED.** `public.journal_event.seq` is
   the journal ORDINAL per `(org, graph)` — the idempotency key a full replay
   reproduces with `enumerate(records, start=1)`. The live path has no ordinal
   to hand (no journal exposes one; see `record_writer.POSITION_ROW_DEFERRED_ON`),
   so it writes the four ONTOLOGY LAYERS and not the position bookkeeping. That
   is a stated residual with its own issue, not a silent omission — the
   ontology layers are what §2.0's acceptance is about, and the position row is
   bookkeeping *about* them.

WHY THE CONNECTION IS FAKE. Postgres is not a pytest lane in this repo — the
DB-level half of Interface A runs in the PGlite harness
(`supabase/tests/pglite/validate.mjs`), and `tests/test_record_projection.py`
states the reason: the Python suite is deliberately DB-free so every lane runs
it. So this suite injects a recording connection through the writer's own
connection factory and asserts on the SQL and transaction shape. The statements
themselves are already proven against a real Postgres by the PGlite harness.
"""

from __future__ import annotations

import json
import time as _time
from pathlib import Path

import pytest

from tortoise import monitoring, record_writer
from tortoise import record_projection as rp
from tortoise.sdk import TortoiseSDK

ROOT = Path(__file__).resolve().parent.parent

CONV = [
    {"role": "user", "content": "the projection handle is still unguarded"},
    {"role": "assistant", "content": "measured: proj.g.query writes the cache"},
    {"role": "user", "content": "then the record is never written in production"},
]

ORG_ID = "org_9078"
GRAPH_ID = "g_9078"


# ── the recording connection ────────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def execute(self, sql, params=None):
        if self._conn.fail_on is not None:
            raise self._conn.fail_on
        self._conn.statements.append((sql, params))

    def close(self) -> None:
        pass


class _FakeConn:
    """A DB-API connection that records instead of writing.

    `fail_on` makes every `execute` raise, which is how the failure leg is
    driven — the point is the SHAPE of the failure path (rollback + count +
    capture survives), not a driver-specific exception.
    """

    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_on: Exception | None = None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _offline_and_unarmed(monkeypatch):
    """No network extractor, and no record writer leaked between tests."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv(record_writer.DSN_ENV, raising=False)
    monkeypatch.delenv(record_writer.ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.GRAPH_ENV, raising=False)
    record_writer.reset()
    yield
    record_writer.reset()


@pytest.fixture()
def conn(monkeypatch) -> _FakeConn:
    """An ARMED record writer whose only connection is a recording fake."""
    fake = _FakeConn()
    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    monkeypatch.setattr(record_writer, "connect", lambda dsn: fake)
    return fake


@pytest.fixture()
def sdk(tmp_path):
    instance = TortoiseSDK(str(tmp_path / "tortoise.db"),
                           event_log_path=str(tmp_path / "events" / "sdk.jsonl"))
    yield instance
    instance.close()


def _journal(log_path: str) -> list[dict]:
    path = Path(log_path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _expected_statements(records: list[dict]) -> list[tuple[str, object]]:
    """What the ONE engine derives for these records — no position row.

    Deliberately computed here from `record_projection.mutations_for` rather
    than restated: the live path must be this function, so the assertion is an
    equality between the production path and the engine, not against a copy of
    its output.
    """
    columns = rp.table_columns()
    refused = rp.refused_props_names()
    out: list[tuple[str, object]] = []
    for record in records:
        for statement in rp.mutations_for(record, org_id=ORG_ID, graph_id=GRAPH_ID,
                                          columns=columns, refused=refused):
            out.append((statement.sql, tuple(statement.params)))
    return out


def _tables(statements: list[tuple[str, object]]) -> set[str]:
    tables = set()
    for sql, _ in statements:
        for table in rp.table_columns():
            if f"public.{table}" in sql:
                tables.add(table)
    return tables


# ── 1 + 2. the public path writes the record, through the one engine ─────────


def test_the_public_capture_path_writes_the_record(sdk, conn):
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        receipt = sdk.capture_session(CONV, session_id="s-9078")

    assert receipt["ok"] is True, receipt
    assert conn.statements, (
        "capture_session committed to the journal and wrote NOTHING to the "
        "record — this is #9078's measured defect (S2 NOT-GREEN)")
    assert conn.commits >= 1


def test_the_live_write_is_the_same_engine_a_rebuild_uses(sdk, conn):
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        sdk.capture_session(CONV, session_id="s-9078")

    records = _journal(sdk._event_log_path)
    assert records, "the journal is empty — nothing to project, nothing pinned"
    assert conn.statements == _expected_statements(records)


def test_the_four_ontology_layers_the_capture_declares_reach_postgres(sdk, conn):
    """The epic's acceptance is about the LAYERS, not about a row count.

    A capture declares an event stream, its source and its session; each must
    arrive as its own table. Asserted as a SUBSET so a future capture that
    declares more (points, subjects) does not red this leg — what it pins is
    that no declared layer is silently dropped.
    """
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        sdk.capture_session(CONV, session_id="s-9078")

    declared = _tables(_expected_statements(_journal(sdk._event_log_path)))
    written = _tables(conn.statements)
    assert declared, "the capture declared no record layer at all"
    assert declared <= written, f"declared but never written: {sorted(declared - written)}"
    assert "event" in written, f"the event stream never reached the record: {sorted(written)}"


# ── 3 + 4. unarmed, and half-configured ─────────────────────────────────────


def test_an_unarmed_record_writes_nothing_and_costs_nothing(sdk, conn, monkeypatch):
    """No DSN → no statement, no connection, no behaviour change."""
    monkeypatch.delenv(record_writer.DSN_ENV, raising=False)
    record_writer.reset()
    receipt = sdk.capture_session(CONV, session_id="s-9078")
    assert receipt["ok"] is True
    assert conn.statements == []
    assert record_writer.armed() is False


def test_a_dsn_without_a_tenant_is_refused_not_guessed(sdk, conn):
    """A DSN alone must never attribute a write to a guessed tenant."""
    before = record_writer.refusal_count("no_tenant")
    receipt = sdk.capture_session(CONV, session_id="s-9078")

    assert receipt["ok"] is True, "the refusal must not fail the capture"
    assert conn.statements == [], (
        "the record was written for a tenant nobody named — the tables are "
        "keyed by (org_id, graph_id) and a guess is a cross-tenant write")
    assert record_writer.refusal_count("no_tenant") > before


# ── 5. the failure path is observed, and loses nothing ──────────────────────


def test_a_failed_record_write_is_counted_and_loses_no_journal_record(sdk, conn):
    conn.fail_on = RuntimeError("record store unreachable")
    failures_before = monitoring.record_write_failure_count()

    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        receipt = sdk.capture_session(CONV, session_id="s-9078")

    assert conn.statements == [], "a failing store cannot have recorded a statement"
    assert conn.rollbacks >= 1, "a failed projection must roll back, not commit a partial record"
    assert monitoring.record_write_failure_count() > failures_before, (
        "the failure was swallowed silently — the journal keeps the record and a "
        "replay heals it, but only if the loss is OBSERVABLE")
    assert receipt["ok"] is True, "a record-store failure must not fail a capture that committed"
    assert _journal(sdk._event_log_path), "the journal lost the record to a downstream failure"


# ── 6. the position row is a stated residual, not an omission ───────────────


def test_the_position_row_is_deferred_and_named(sdk, conn):
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        sdk.capture_session(CONV, session_id="s-9078")

    assert not [sql for sql, _ in conn.statements
                if sql.startswith(rp._JOURNAL_INSERT_PREFIX)], (
        "journal_event.seq is the journal ORDINAL; the live path has no ordinal "
        "to hand, so writing this row here would fabricate a position")
    assert record_writer.POSITION_ROW_DEFERRED_ON.startswith("#"), (
        "a deferral must name its own issue, or the next lane reads it as done")


# ── the wiring is checkable without a capture ───────────────────────────────


def test_record_projection_has_a_production_importer():
    """Indicator 2 of #9078, as an executable check (and the tool it names)."""
    from tools import record_wiring_check

    report = record_wiring_check.check()
    assert report.ok, report.render()
    assert record_writer.__name__.split(".")[-1] in report.importers


def test_the_wiring_check_tool_is_runnable():
    """`tools/record_wiring_check.py` is the issue's own acceptance command."""
    from tools import record_wiring_check

    assert callable(record_wiring_check.main)




# ── the findings from review: concurrency, partial commits, silent zeros ────


def test_two_records_never_share_one_transaction(monkeypatch):
    """ONE connection ⇒ the transactions must SERIALIZE, or a commit lies.

    psycopg2's transaction is connection-scoped. Interleaved callers would have
    the first `commit()` publish the second's half-executed statements and the
    second's `rollback()` discard them — while BOTH return True. The hosted
    capture lane really is concurrent (`hosted_api._CAPTURE_EXECUTOR`, 4
    workers), so this is the shape, not a hypothetical.

    A ContextVar does not follow a thread, so each worker names its own tenant —
    which is exactly how a per-request hosted write will have to do it.
    """
    import threading

    inside = {"now": 0, "max": 0}
    guard = threading.Lock()

    class _SlowCursor:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=None):
            # The OPEN/CLOSE accounting is `_SlowConn`'s (one transaction per
            # cursor-to-commit span). Counting here too would count statements,
            # not concurrent transactions — the first cut of this leg read
            # max=9 for a correctly serialized writer.
            _time.sleep(0.001)
            self._conn.statements.append((sql, params))

        def close(self):
            pass

    class _SlowConn(_FakeConn):
        def __init__(self) -> None:
            super().__init__()
            self._open = False

        def cursor(self):
            if not self._open:  # a transaction opens with its first statement
                self._open = True
                with guard:
                    inside["now"] += 1
                    inside["max"] = max(inside["max"], inside["now"])
            return _SlowCursor(self)

        def commit(self) -> None:
            if self._open:
                self._open = False
                with guard:
                    inside["now"] -= 1
            super().commit()

        def rollback(self) -> None:
            if self._open:
                self._open = False
                with guard:
                    inside["now"] -= 1
            super().rollback()

    slow = _SlowConn()
    record_writer.reset()
    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    monkeypatch.setattr(record_writer, "connect", lambda dsn: slow)

    records = [{"type": "SessionRecorded", "id": f"s-{i}", "turn_count": i,
                "is_episodic": False,
                "ts": "2026-10-19T00:00:00+00:00"} for i in range(4)]

    def worker(record: dict) -> None:
        with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
            record_writer.project(record)

    threads = [threading.Thread(target=worker, args=(r,)) for r in records]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert inside["max"] == 1, (
        f"two record transactions were open at once (max={inside['max']}) — one "
        "connection, one transaction at a time")
    assert slow.commits == len(records), f"commits={slow.commits}"
    assert not record_writer.failures(), record_writer.failures()


def test_a_failure_on_the_second_statement_commits_nothing(monkeypatch):
    """The rollback leg must be able to see a PARTIAL set, or it pins nothing."""
    executed: list[str] = []

    class _FailSecondConn(_FakeConn):
        def __init__(self) -> None:
            super().__init__()
            self.in_txn = 0

        def cursor(self):
            return _FailSecondCursor(self)

        def commit(self) -> None:
            self.in_txn = 0
            super().commit()

        def rollback(self) -> None:
            self.in_txn = 0
            super().rollback()

    class _FailSecondCursor(_FakeCursor):
        def execute(self, sql, params=None):
            self._conn.in_txn += 1
            executed.append(sql)
            if self._conn.in_txn == 2:
                raise RuntimeError("record store dropped the connection")
            super().execute(sql, params)

    failing = _FailSecondConn()
    record_writer.reset()
    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    monkeypatch.setattr(record_writer, "connect", lambda dsn: failing)

    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        record_writer.project({"type": "SessionRecorded", "id": "s-partial",
                               "turn_count": 3, "is_episodic": False,
                               "ts": "2026-10-19T00:00:00+00:00"})

    assert len(executed) >= 2, "the statement set was never partially executed"
    assert failing.commits == 0, "a mid-set failure COMMITTED a partial record"
    assert failing.rollbacks >= 1
    assert record_writer.refusal_count("execute") == 1
    assert record_writer.failures().get("execute") == 1, (
        "the tally must SURVIVE the failure path — a reset there erases the "
        "number an operator reads during an outage")


def test_an_armed_record_over_no_journal_is_counted_not_silent(monkeypatch, tmp_path):
    """Armed + no journal is a misconfiguration, and it must not read as 0."""
    from tortoise import monitoring

    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    fake = _FakeConn()
    monkeypatch.setattr(record_writer, "connect", lambda dsn: fake)
    record_writer.reset()

    before = monitoring.record_write_failure_count("no_journal")
    instance = TortoiseSDK(str(tmp_path / "no-journal.db"))  # no event_log_path
    try:
        with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
            instance.create_point("fact", "the record has no journal to project")
    finally:
        instance.close()

    assert record_writer.refusal_count("no_journal") >= 1, (
        "an armed record over a journal-less SDK wrote nothing and said nothing")
    assert monitoring.record_write_failure_count("no_journal") > before
    assert fake.statements == []


# ── the other two live doors have their own executable claim ────────────────


def test_the_event_api_door_projects_its_records(monkeypatch):
    """`EventAPI` owns its own append, so it owns its own record write.

    Without this leg the door's wiring was green by absence: the cycle-1 review
    found the door unwired, and a wiring nobody exercises is not pinned.
    """
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    fake = _FakeConn()
    record_writer.reset()
    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    monkeypatch.setattr(record_writer, "connect", lambda dsn: fake)

    import tempfile
    from pathlib import Path as _Path
    tmp = _Path(tempfile.mkdtemp())
    api = EventAPI(EventLog(str(tmp / "api.jsonl")), initiated_by="extractor",
                   agent_id="test")
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        api.add_point("the record is written in production", {"source": "test"})

    assert fake.statements, "the EventAPI door journalled and wrote no record"
    tables = _tables(fake.statements)
    assert tables, f"the door's statements name no record table: {fake.statements[:2]}"


def test_the_miner_door_projects_its_nested_event_record(monkeypatch):
    """The miner's record nests the node under `event` — it must still land."""
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import ConversationMiner

    fake = _FakeConn()
    record_writer.reset()
    monkeypatch.setenv(record_writer.DSN_ENV, "postgresql://record.example/tortoise")
    monkeypatch.setattr(record_writer, "connect", lambda dsn: fake)

    import tempfile
    from pathlib import Path as _Path
    tmp = _Path(tempfile.mkdtemp())
    api = EventAPI(EventLog(str(tmp / "miner.jsonl")), initiated_by="extractor",
                   agent_id="test")
    with record_writer.use_record_tenant(org_id=ORG_ID, graph_id=GRAPH_ID):
        ConversationMiner._emit_event(api, {"id": "ev_mined", "eventId": "ev_mined",
                                            "eventKind": "decision",
                                            "content": "the record is written",
                                            "eventStatus": "active"})

    assert fake.statements, "the miner journalled and wrote no record"
    assert not record_writer.failures().get("unprojectable"), (
        "the nested event carrier was refused — every mined event would be lost")
