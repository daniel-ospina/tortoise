"""#10620 — the record dropped every point carrying `search_keys`, silently.

Two defects, one lane:

**Attribution (fixed here, no decision).** `record_projection.replay()` let the
driver's exception escape unadorned and `record_writer.RecordWriter.write()`
formatted `type(exc).__name__` — and nothing else — into the message. So the
hosted deployment's log said:

    the record write failed after 1 journal record(s) were materialized from
    position 3412 (InvalidTextRepresentation)

A 302-write failure had no attributable cause on the deployment's own log. It
now names the STATEMENT, its TABLE and COLUMNS with their values, and the
entity/point id, plus the driver's PRIMARY message — never its `DETAIL`/`CONTEXT`,
which quote the refused row and would put back the very value the attribution half
withholds (`_primary_message`). `/health` also stopped
answering `ok` while rows were being dropped — its verdict is
`refusing: {reason, age_s}` inside a bounded window, `degraded`, and a reason's
remedy (`#10185`, the open home for that half: a cumulative counter cannot say
"now", so the verdict is the AGE of the last refusal, not a fold over totals).
⚠️ The lane's own number is `#10620`, which is a docs PR, not this defect — the
health half's issue is `#10185` and R4's is `#9264`/`#9552`. The file keeps its
name so `config/ci-surfaces.yml` and the CI job list keep pointing at it.

**Reconciliation (the `search_keys` refusal itself).** `public.point.search_keys`
was `jsonb`; every producer emits the graph's FLAT SPACE-JOINED STRING
(`projection/entities.py::flatten_search_keys_prop`), so Postgres refused every
record that carried one with `22P02 invalid input syntax for type json` and the
row was dropped. Nothing reads the record's copy as a JSON structure, so the
column narrows to `text` (`20261102000002_record_point_search_keys_text.sql`) —
one representation, the graph's, exactly as #9264 narrowed
`session.capture_redactions`.

The DB-free legs run everywhere. The Postgres legs skip without
`TEST_RECORD_DB_URI` (the record's own driver extra is `[postgres]`).
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from pathlib import Path

import pytest

from tortoise import record_projection, record_writer

RECORD_DB_ENV = "TEST_RECORD_DB_URI"


# ════════════════════════════════════════════════════════════════════════════
# 1. Attribution — a failed write names WHAT failed
# ════════════════════════════════════════════════════════════════════════════


class _BoomCursor:
    """A cursor that refuses the statement this test targets, like the driver.

    The message is the one Postgres actually produced for this defect, measured
    on a throwaway `postgres:16` through the repo's own `_split_properties` +
    `_upsert_statement` (#10620's reproduction). Postgres does NOT name the
    column in this error — which is precisely why the attribution has to come
    from the statement, not from the driver.

    Only the `point` upsert refuses: on the real path `node_identity` is written
    first and succeeds, and a stub that fails the FIRST statement instead would
    assert the attribution of a failure that never happens.
    """

    def __init__(self, message: str | BaseException,
                 refuse_on: str = "INSERT INTO public.point") -> None:
        self.message = message
        self.refuse_on = refuse_on
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=()) -> None:
        self.executed.append((sql, params))
        if self.refuse_on not in sql:
            return
        if isinstance(self.message, BaseException):
            raise self.message
        raise RuntimeError(self.message)


_JSON_REFUSAL = (
    'invalid input syntax for type json\nDETAIL:  Token "err4032" is invalid.\n'
    "CONTEXT:  JSON data, line 1: err4032 release"
)


class _DriverDiag:
    """The fields `psycopg2.extensions.Diagnostics` exposes on a driver error."""

    def __init__(self, primary: str, detail: str | None = None,
                 context: str | None = None) -> None:
        self.message_primary = primary
        self.message_detail = detail
        self.context = context


class _DriverError(Exception):
    """A psycopg2-SHAPED refusal: SQLSTATE on `pgcode`, the fields on `diag`.

    ``str(exc)`` is the DRIVER's own formatting — the primary message first, then
    the optional ``DETAIL:`` and ``CONTEXT:`` lines — which is the shape that put
    a refused value back into the very line this module withholds it from.
    """

    def __init__(self, primary: str, *, code: str = "22P02",
                 detail: str | None = None, context: str | None = None) -> None:
        self.pgcode = code
        self.diag = _DriverDiag(primary, detail, context)
        parts = [primary]
        if detail:
            parts.append(f"DETAIL:  {detail}")
        if context:
            parts.append(f"CONTEXT:  {context}")
        super().__init__("\n".join(parts))


def _point_record(key: str, search_keys: object) -> dict:
    return {"type": "PointAdded",
            "point": {"eventId": key, "id": key, "content": "a claim",
                      "point_kind": "statement", "search_keys": search_keys}}


def _replay_refusal(record: dict, monkeypatch) -> str:
    """The message `replay()` raises, with a cursor that refuses the statement."""
    cursor = _BoomCursor(_JSON_REFUSAL)
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: cursor)
    with pytest.raises(record_projection.RecordStatementError) as caught:
        record_projection.replay([record], object(), org_id="org-t",
                                 graph_id="graph-t", position=False)
    return str(caught.value)


def test_a_failed_write_names_the_statement_table_and_columns(monkeypatch):
    """The message an operator reads names WHICH statement refused WHICH column.

    The COLUMN is named and its length is given; its VALUE is withheld, because
    `search_keys` is the user's own text (see
    `test_content_columns_are_named_but_never_quoted`). Naming the column is what
    the driver's own message cannot do.
    """
    message = _replay_refusal(_point_record("pt-err4032", "err4032 release"),
                              monkeypatch)

    assert "INSERT INTO public.point" in message, message
    assert "search_keys=<15 chars withheld>" in message, (
        "the failing COLUMN must be named — the driver's own message does not "
        f"contain it: {message}"
    )
    assert "err4032 release" not in message, (
        "the user's text reached the log — from the ATTRIBUTION (a column paired "
        "with its value) or from the DRIVER's own `CONTEXT:`/`DETAIL:` fields, "
        "which quote the very value that was refused: "
        f"{message}"
    )
    assert "node_key=pt-err4032" in message, (
        f"the entity/point id must be named: {message}"
    )
    assert "invalid input syntax for type json" in message, (
        f"the driver's own str(exc) must survive verbatim: {message}"
    )


def test_the_attribution_never_mispairs_a_column_with_a_value(monkeypatch):
    """A shifted parameter list names NO values rather than the WRONG ones.

    The upsert's key may be a database-computed `_SqlExpr` (a scalar subquery
    carrying its own parameters, #9956), which shifts every later pairing by one
    INSIDE a `VALUES` list whose placeholder count is unchanged. A line reporting
    `node_key=<the url>` would point the operator at the wrong datum, so the
    builder declines to state the pairing at all.
    """
    node = record_projection.Node("source", "Source", "node_key")
    computed_key = record_projection._SqlExpr(
        "(SELECT node_key FROM public.source WHERE canonical_url = %s)",
        ("https://a.example",))
    statement = record_projection._upsert_statement(
        "o", "g", node, computed_key, {"url": "https://a.example"},
        {"kind": "web"})
    text = record_projection._statement_attribution(statement, 2, 5)

    assert statement.columns is None, (
        "a `_SqlExpr` key makes the positional pairing wrong by one, and the "
        "count is unchanged — the builder must refuse to state it"
    )
    assert "record statement 2/5" in text
    assert "INSERT INTO public.source" in text
    assert "params=" in text
    assert "url=https://a.example" not in text, (
        f"a paired line here names the wrong column's value: {text}"
    )


def test_the_attribution_pairs_columns_when_the_builder_declared_them():
    """The control: an ordinary point upsert DOES name each column's value."""
    node = record_projection.Node("point", "Point", "node_key")
    statement = record_projection._upsert_statement(
        "o", "g", node, "pt-1", {"search_keys": "err4032 release"}, {})
    text = record_projection._statement_attribution(statement, 1, 2)

    assert "node_key=pt-1" in text and "search_keys=<" in text, (
        f"an ordinary upsert names each column (values withheld where content): {text}"
    )


def test_the_driver_clause_carries_the_sqlstate_and_never_the_row_dump(monkeypatch):
    """⛔ `str(exc)` on a driver error is NOT the terse message (#10648 review P2).

    The protocol carries the primary message (`M`) *and* two optional fields that
    quote the data: `DETAIL` (for `22P02`, the refused token) and `CONTEXT` (the
    fragment being read). psycopg2's `str(exc)` is those fields CONCATENATED — so
    interpolating it re-emitted `err4032 release` at the end of a line whose whole
    job was to withhold it, while the attribution half carefully printed
    `search_keys=<15 chars withheld>`.

    This is the same rule the sibling record module already states and enforces for
    its own driver messages (`record_backup._connection_failure`, #9601 review
    cycle 9): the exception TYPE and the SQLSTATE, never the driver's text.
    """
    cursor = _BoomCursor(_DriverError(
        "invalid input syntax for type json",
        detail='Token "err4032" is invalid.',
        context="JSON data, line 1: err4032 release"))
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: cursor)

    with pytest.raises(record_projection.RecordStatementError) as caught:
        record_projection.replay([_point_record("pt-err4032", "err4032 release")],
                                 object(), org_id="org-t", graph_id="graph-t",
                                 position=False)

    message = str(caught.value)
    assert "22P02" in message, (
        f"the SQLSTATE is what makes a fault classifiable — it must survive: {message}"
    )
    assert "invalid input syntax for type json" in message, (
        f"the primary message IS the fault clause the 302-row drop lacked: {message}"
    )
    assert "err4032 release" not in message, (
        f"the driver's `CONTEXT:` field quoted the refused value: {message}"
    )
    assert 'Token "err4032"' not in message, (
        f"the driver's `DETAIL:` field quoted the refused token: {message}"
    )
    assert "DETAIL:" not in message and "CONTEXT:" not in message, (
        f"neither optional field belongs on this line: {message}"
    )


def test_a_check_violation_never_carries_the_failing_row(monkeypatch):
    """The other class of the same leak: a CHECK's DETAIL is the WHOLE ROW.

    For `chk_*_props_*` PostgreSQL's `DETAIL` reads `Failing row contains (…)` —
    i.e. every column of the row, `content` and `props` included, which are
    exactly the two the attribution withholds. A withheld column name whose value
    arrives one field later withholds nothing.
    """
    cursor = _BoomCursor(_DriverError(
        'new row for relation "point" violates check constraint '
        '"chk_point_props_no_derived"',
        code="23514",
        detail='Failing row contains (pt-1, org-t, graph-t, a private claim, '
               '{"note": "secret"}).'))
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: cursor)

    with pytest.raises(record_projection.RecordStatementError) as caught:
        record_projection.replay([_point_record("pt-1", "err4032 release")],
                                 object(), org_id="org-t", graph_id="graph-t",
                                 position=False)

    message = str(caught.value)
    assert "23514" in message, f"the SQLSTATE must survive: {message}"
    assert "violates check constraint" in message, (
        f"the primary message must survive: {message}"
    )
    assert "Failing row" not in message, (
        f"the whole row reached the log through the driver's DETAIL: {message}"
    )
    assert "a private claim" not in message and "secret" not in message, (
        f"`content`/`props` leaked through the row dump: {message}"
    )


def test_a_scope_failure_on_the_live_door_never_logs_the_drivers_detail(monkeypatch):
    """The SAME withhold at the other seam: the wrap that reaches the log.

    `replay()` raises `RecordStatementError` for a *statement* failure, but
    `scope_connection()` runs before the first statement and its driver error
    propagates raw into `RecordWriter.write`'s catch-all, which is what composes
    the LIVE warning (`record_writer.py:_bounded_cause`). Both seams carry the
    driver's text into a log stream, so both narrow it to the primary message.
    """
    refusal = _DriverError(
        "invalid input syntax for type json",
        detail='Token "err4032" is invalid.',
        context="JSON data, line 1: err4032 release")

    def _refuse_scope(conn, org_id):
        raise refusal

    monkeypatch.setattr(record_projection, "scope_connection", _refuse_scope)
    writer = record_writer.RecordWriter(dsn="postgresql://u:p@db.example.com/db")

    with pytest.raises(record_writer.RecordWriteError) as caught:
        writer.write([_point_record("pt-err4032", "err4032 release")],
                     org_id="org-t", graph_id="graph-t", conn=object(),
                     position=False)

    message = str(caught.value)
    assert "invalid input syntax for type json" in message, (
        f"the fault clause must survive the wrap: {message}"
    )
    assert "err4032 release" not in message, (
        f"the live warning carried the driver's CONTEXT line: {message}"
    )
    assert "DETAIL:" not in message and "CONTEXT:" not in message, (
        f"neither driver field belongs in the log: {message}"
    )


def test_a_failed_live_write_names_the_record_and_its_id(monkeypatch, caplog):
    """The LIVE door's warning carries the record type, the id, and the cause."""
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://u:p@db.example.com:5432/postgres")
    monkeypatch.setenv(record_writer.RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(record_writer.RECORD_ORG_ENV, "org-t")
    monkeypatch.setenv(record_writer.RECORD_GRAPH_ENV, "graph-t")

    def _explode(*args, **kwargs):
        raise record_writer.RecordWriteError(
            "the record write failed after 1 journal record(s) were materialized "
            "from position 7 (RecordStatementError: record statement 1/3: INSERT "
            "INTO public.point columns(node_key=pt-err4032, "
            "search_keys=<15 chars withheld>) params=9: invalid input syntax for "
            "type json)")

    monkeypatch.setattr(record_writer, "write_records", _explode)

    with caplog.at_level(logging.WARNING, logger=record_writer.__name__):
        result = record_writer.write_live_record(
            _point_record("pt-err4032", "err4032 release"), durable=True)

    assert result is not None and result.outcome == record_writer.RecordWriteOutcome.REFUSED
    line = "\n".join(record.getMessage() for record in caplog.records)
    assert "the record write did not happen (write_failed)" in line, (
        "the phrase the runbook's recipe greps must survive — a log line an "
        f"operator cannot find is not an attribution: {line}"
    )
    assert "record=PointAdded" in line and "pt-err4032" in line, (
        f"the log line must name the record and its point id: {line}"
    )
    assert "search_keys=<15 chars withheld>" in line, (
        f"the log line must name the failing column: {line}"
    )


def _wide_point_record() -> dict:
    """A realistic, WIDE point upsert — 21 populated columns.

    Width is the whole point of the leg below: the attribution for this record is
    a measured 488 characters, so anything that budgets the line by character
    count decides what survives. A narrow record would let a truncating wrap pass
    by luck.
    """
    return {"type": "PointAdded", "point": {
        "eventId": "pt-wide", "id": "pt-wide",
        "content": "a claim " + "x" * 200,
        "point_kind": "statement", "search_keys": "err4032 release",
        "quote": "q", "verbatim": "v", "speaker": "s", "label": "l",
        "status": "active", "op_type": "IMPL", "direction": "forward",
        "tags": ["a", "b"], "authored_by": "me", "assessor": "au",
        "reviewed": True, "currency": "USD", "amount_minor": 100,
        "grounding": "g", "as_of": "2026", "props": {"k": "v" * 50},
    }}


def test_the_wrap_cannot_truncate_the_fault_away_on_a_wide_statement(monkeypatch):
    """⛔ The FAULT is the half that must survive the wrap (#10648 review P2).

    `record_writer._bounded_cause` keeps the HEAD of a message, and the driver's
    own text used to sit at the TAIL of the attribution. On a wide statement
    (measured: a 488-char attribution for a 21-column point upsert) the wrap
    therefore returned 400 characters in which `invalid input syntax for type
    json` was ABSENT — the attribution existed and still could not say what went
    wrong.

    Driven through `RecordWriter.write` (the REAL wrap) against a cursor that
    refuses the real `point` statement, not through `replay()` (which is the
    unwrapped message) and not through a hand-written `RecordWriteError`.
    """
    cursor = _BoomCursor(_JSON_REFUSAL)
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: cursor)
    writer = record_writer.RecordWriter(dsn="postgresql://u:p@db.example.com/db")

    with pytest.raises(record_writer.RecordWriteError) as caught:
        writer.write([_wide_point_record()], org_id="org-t", graph_id="graph-t",
                     conn=object(), position=False)

    message = str(caught.value)
    assert "invalid input syntax for type json" in message, (
        "the driver's own text is the half that names the FAULT, and the wrap "
        f"truncated it away: {message}"
    )
    assert "record statement" in message and "public.point" in message, (
        f"the SUBJECT (which statement, which table) must still be named: {message}"
    )
    assert "node_key=pt-wide" in message and "search_keys=<15 chars withheld>" in message, (
        "the identifying column must survive: a wide statement truncates its "
        f"COLUMN LIST, never its identity or its fault: {message}"
    )
    assert len(message) < record_projection.ATTRIBUTION_LINE_LIMIT + 200, (
        "one log line, and the wrap must not have to cut the line the "
        f"projection composed: {len(message)} chars"
    )


def test_an_unattributed_update_names_its_set_column():
    """`_UPDATE_SETS_RE` must be WIRED, not a dead regex (#10620 review, P2).

    When a statement declines its positional pairing (`columns is None`), the
    attribution names the columns the SQL itself spells. The INSERT arm did that
    from the start; the UPDATE arm fell through to a bare `params=N`, naming no
    column at all — which is exactly the case the regex was written for.

    ⛔ This drives the REAL builder that reaches the arm.
    `_tier_demote_statement` (the record's half of a graph-tier removal) emits
    `UPDATE public.point SET cache_tier = %s …` with no `columns`, so this is not
    a hypothetical shape: a failed tier demotion in the delete path named no
    column at all. An earlier draft of this test hand-built the statement and
    asserted in its docstring that no builder declines an UPDATE's columns —
    which is false, and would have licensed deleting the arm again.
    """
    statement = record_projection._tier_demote_statement("o", "g", "pt-1")

    assert statement.columns is None, (
        "the premise: this builder states no pairing, so the fallback runs"
    )
    text = record_projection._statement_attribution(statement, 1, 2)

    assert "UPDATE public.point" in text, text
    assert "columns(cache_tier)" in text, (
        f"an unattributed UPDATE must still name the column it SET: {text}"
    )
    assert "params=4" in text, text
    assert "pt-1" not in text, (
        "declining the pairing means NO values — a mis-paired value is worse "
        f"than none: {text}"
    )


def test_an_unattributed_update_names_every_set_column():
    """Several `SET` terms with a jsonb merge are all named, in order."""
    statement = record_projection.Statement(
        "UPDATE public.point SET content = %s, search_keys = %s, "
        "props = props || %s::jsonb WHERE org_id = %s AND graph_id = %s",
        ("c", "err4032 release", "{}", "org", "graph"),
        None,
    )

    text = record_projection._statement_attribution(statement, 2, 3)

    assert "UPDATE public.point" in text, text
    assert "columns(content, search_keys, props)" in text, (
        f"an unattributed UPDATE must still name its SET columns: {text}"
    )
    assert "params=5" in text, text
    assert "err4032 release" not in text, (
        "declining the pairing means NO values — a mis-paired value is worse "
        f"than none: {text}"
    )


def test_the_identity_reader_never_invents_a_value():
    """Absence is STATED, never a blank an operator reads as 'no problem'."""
    assert "no identity field" in record_writer._record_identity({"type": "X"})
    assert record_writer._record_identity(
        {"type": "PointAdded", "point": {"eventId": "p1", "id": "p2"}}
    ) == "point.eventId=p1"


def test_the_identity_reader_withholds_a_content_bearing_identity():
    """⛔ `name` IDENTIFIES a source, and a source's `name` is its TITLE.

    `_RECORD_IDENTITY_KEYS` ends with `name` precisely so a `source`/`object`
    snapshot with no id can still be identified — but that field holds the
    document's title, the same datum `_CONTENT_COLUMNS` withholds as `title`, and
    this line is the SAME log line. Printed verbatim it put a document title in
    the operator's log through the column next door, which is the leak the
    withhold list exists to prevent (#10648 review P3). The key is still NAMED,
    with its length: that is what keeps the line diagnosable.
    """
    text = record_writer._record_identity(
        {"type": "SourceAdded", "source": {"name": "Q3 pricing memo"}})

    assert "source.name=" in text, f"the field must still be named: {text}"
    assert "Q3 pricing memo" not in text, f"a document title reached the log: {text}"
    assert "<15 chars withheld>" in text, text


def test_the_identity_reader_bounds_every_value():
    """Nothing that reaches this log may be sized by the caller."""
    text = record_writer._record_identity(
        {"type": "PointAdded", "point": {"eventId": "p" * 500}})

    assert len(text) < 200, f"an unbounded identity value sized the log line: {len(text)}"
    assert "point.eventId=" in text, text


# ════════════════════════════════════════════════════════════════════════════
# 2. Health honesty — a dropped row is not a healthy deployment
# ════════════════════════════════════════════════════════════════════════════


#: Set by the leg below so the isolation proof after it can SKIP (not fail) when
#: the two legs are not run together — a `-k` selection must not report a false
#: red for a property it never set up.
_REFUSAL_STAMPED_BY_A_PRIOR_LEG = False


def test_a_counted_refusal_is_visible_in_the_leg_that_counts_it():
    """The premise of the isolation proof below, asserted as a premise."""
    global _REFUSAL_STAMPED_BY_A_PRIOR_LEG
    from tortoise import monitoring

    monitoring.record_record_write_failure("write_failed")
    refusing = monitoring.record_refusing()

    assert refusing is not None and refusing["reason"] == "write_failed"
    _REFUSAL_STAMPED_BY_A_PRIOR_LEG = True


def test_the_next_leg_does_not_inherit_that_refusal():
    """⛔ THE CROSS-FILE BLEED, pinned by the file that causes it (#10648 P2).

    `record_refusing()` is PROCESS state on a 300 s window, so before the hoist
    this leg — and four `status == "ok"` legs in
    `tests/test_backup_watcher_health.py`, which drive the REAL block — asserted
    "no earlier test in this worker tripped the record seam". That is an ordering
    dependency between test FILES, not a property of the block, and it reddens
    for a reason that has nothing to do with the code under test.

    The clearing lives in `tests/conftest.py::_no_ambient_record_refusal`, which
    is autouse for the WHOLE suite; this leg fails if that hoist is removed or
    narrowed back to one file.
    """
    from tortoise import monitoring

    if not _REFUSAL_STAMPED_BY_A_PRIOR_LEG:  # pragma: no cover — `-k` selection
        pytest.skip("the stamping leg did not run in this selection")

    assert monitoring.record_refusing() is None, (
        "a refusal counted by an earlier leg is still inside the 5-minute "
        "window — every later `status == ok` leg in this worker now asserts "
        "test ORDER instead of the record's health"
    )
    assert monitoring.record_write_failure_counts().get("write_failed", 0) == 0, (
        "the cumulative counter leaked across legs too"
    )


def _health(monkeypatch, failures: dict[str, int], *, refusing=None,
            refusing_raises: BaseException | None = None) -> dict:
    """The record block with the counters stubbed and the refusal state given.

    `refusing` is the OUTPUT of ``monitoring.record_refusing()`` — ``None`` for
    "no refusal inside the window" or ``{"reason", "age_s"}``. The verdict is
    window-based (#10185), so a cumulative count alone must NOT redden it; the
    two are passed separately here precisely because the block must not conflate
    them.

    `refusing_raises` makes that read RAISE instead, which is the other half of
    ``_record_health``'s "never raises" contract.
    """
    from tortoise import hosted_api, monitoring

    monkeypatch.setattr(monitoring, "record_write_failure_counts",
                        lambda: dict(failures))
    # `_record_health` imports this name FROM `monitoring` at call time, so the
    # stub belongs on `monitoring` — patching `hosted_api` would bind a module
    # attribute the local import never reads, and the stub would be inert.
    if refusing_raises is not None:
        def _boom():
            raise refusing_raises

        monkeypatch.setattr(monitoring, "record_refusing", _boom)
    else:
        monkeypatch.setattr(monitoring, "record_refusing", lambda: refusing)
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)
    probe = {"ok": True, "latency_ms": 0.1, "error": None}
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(probe))
    return hosted_api._record_health()


def test_a_refusal_inside_the_window_makes_the_record_block_not_ok(monkeypatch):
    """A refusal NOW is the defect — the surface that lies about dropped rows."""
    record = _health(monkeypatch, {"write_failed": 302},
                     refusing={"reason": "write_failed", "age_s": 1.2})

    assert record["ok"] is False, "a surface dropping rows must not read healthy"
    assert record["degraded"] is True
    assert record["refusing"] == {"reason": "write_failed", "age_s": 1.2}
    assert record["degraded_reasons"] == {"write_failed": 302}, (
        "the reason behind the verdict, with its cumulative count — the number "
        "is still mirrored verbatim, the verdict is ADDED"
    )
    assert record["record_write_failures"] == {"write_failed": 302}


def test_a_historical_refusal_does_not_degrade_forever(monkeypatch):
    """#10185's first false direction: one transient refusal, red for ever.

    A cumulative count with NO refusal inside the window is HISTORY, not a
    current state. Folding it in (the pre-fix shape) reported `degraded: true`
    for the life of the process — and an operator who learns the flag is always
    red ignores the next real one.
    """
    record = _health(monkeypatch, {"write_failed": 302}, refusing=None)

    assert record["degraded"] is False, (
        "a refusal that already cleared the window must not pin `degraded`"
    )
    assert record["refusing"] is None
    assert record["degraded_reasons"] == {}, (
        "the current verdict has no reason behind it"
    )
    assert record["ok"] is True
    assert record["record_write_failures"] == {"write_failed": 302}, (
        "NOTHING is reset or hidden: the history stays readable"
    )


def test_an_unreadable_verdict_is_never_published_as_not_refusing(monkeypatch):
    """A failed verdict read is its OWN cause, and NOT EVALUATED is not `false`.

    `_record_health` promises it never raises, so the read is guarded — but the
    pre-fix guard filed the cause in `error`, the one field whose documented
    meaning is *a value was SET and the seam REFUSED it* (remedy: "fix the
    DSN"), which is how #9548's operator got sent to rotate a healthy secret.
    It also left `degraded` at `bool(None)` = `False`, publishing the claim "no
    armed write was refused inside the window" about a read that never happened.

    Both directions are pinned here: the cause lands in `refusal_error` (not in
    `error`, not in `counters_error` — the counter snapshot WAS read), the
    verdict reads `None`, and the endpoint still answers 200 with a degraded
    `status` rather than 5xxing.
    """
    import asyncio

    from tortoise import hosted_api

    record = _health(monkeypatch, {"write_failed": 302},
                     refusing_raises=RuntimeError("refusal state exploded"))

    assert record["ok"] is False, (
        "a surface that cannot read its own verdict must not read healthy"
    )
    assert record["error"] is None, (
        "`error` means SET-and-REFUSED and its remedy is 'fix the DSN'; a "
        "monitoring read filed there sends the operator to rotate a healthy secret"
    )
    assert "refusal state exploded" in record["refusal_error"], (
        f"the cause needs its own key: {record.get('refusal_error')!r}"
    )
    assert record["counters_error"] is None, (
        "the counter snapshot was READ — its key must not be borrowed to carry "
        "a different read's failure"
    )
    assert "refusing" not in record, (
        "an unevaluated read must not publish the `refusing` key at all: the "
        f"block's own `null` means 'nothing is refusing' (#10648 P3) — {record.get('refusing')!r}"
    )
    assert record["degraded"] is None, (
        "NOT EVALUATED is not 'not refusing': `false` here claims a quiet window "
        "the block never checked"
    )
    assert record["degraded_reasons"] is None
    assert record["record_write_failures"] == {"write_failed": 302}, (
        "the failure must not hide the history it could read"
    )

    body = asyncio.run(hosted_api.health())
    assert body["status"] == "degraded", body["record"]
    assert body["record"]["ok"] is False, body["record"]


@pytest.mark.parametrize("reason", [
    "write_failed", "no_tenant", "malformed_dsn", "circuit_open",
    "no_journal", "seam_unimportable",
])
def test_every_counter_reason_reaches_the_payload_with_its_own_identity(
        monkeypatch, reason):
    """The runbook's SIX-row reason table has a code side (#10620 review, 2).

    `no_journal` and `seam_unimportable` are the two the first cut omitted, and
    they are the two whose remedy is NOT a replay — a reason that never reaches
    the payload cannot name its own fix. Driven through the PUBLIC writer, so an
    allowlist hidden inside the window reader fails here instead of silently
    dropping a reason from the operator's diagnosis.
    """
    from tortoise import monitoring

    monitoring.record_record_write_failure(reason)
    refusing = monitoring.record_refusing()
    record = _health(monkeypatch, {reason: 1}, refusing=refusing)

    assert record["degraded"] is True, reason
    assert record["ok"] is False, reason
    assert record["refusing"]["reason"] == reason, record["refusing"]
    assert set(record["degraded_reasons"]) == {reason}, record["degraded_reasons"]


def test_zero_failures_stays_ok(monkeypatch):
    """The control: `off` and `healthy` must not be reddened into each other."""
    record = _health(monkeypatch, {}, refusing=None)

    assert record["ok"] is True
    assert record["degraded"] is False
    assert record["degraded_reasons"] == {}
    assert record["refusing"] is None


def test_the_refusal_window_clears_with_no_restart_and_no_reset():
    """#10185 indicator 2, on the real seam — not a stubbed dict.

    Count one refusal through the PUBLIC writer, read the signal (refusing), then
    advance past the window and read again (not refusing) while the cumulative
    counter is still non-zero and never reset.
    """
    from tortoise import monitoring

    monitoring.clear_record_last_refusal()
    before = dict(monitoring.record_write_failure_counts())
    try:
        monitoring.record_record_write_failure("write_failed")
        assert monitoring.record_refusing() is not None, (
            "a just-counted refusal must read as refusing"
        )
        assert monitoring.record_last_refusal() is not None
        # Advance the clock PAST the window rather than sleeping it out.
        window = monitoring.RECORD_REFUSAL_WINDOW_SECONDS
        stamp = monitoring.record_last_refusal()
        monitoring._RECORD_LAST_REFUSAL = (stamp[0] - window - 1.0, stamp[1])
        assert monitoring.record_refusing() is None, (
            "a quiet window must clear the signal with no restart (#10185)"
        )
        after = monitoring.record_write_failure_counts()
        assert after.get("write_failed", 0) >= before.get("write_failed", 0) + 1, (
            "the cumulative counter must never be reset or hidden — only the "
            "VERDICT is time-bounded"
        )
    finally:
        monitoring.clear_record_last_refusal()
        import contextlib
        for reason in list(monitoring.record_write_failure_counts()):
            if before.get(reason, 0) == 0:
                with contextlib.suppress(KeyError):
                    monitoring.RECORD_WRITE_FAILURE_COUNT.remove(reason)


def test_status_itself_degrades_not_only_the_sub_block(monkeypatch):
    """/health's top-level `status` is what an operator (and a monitor) reads."""
    import asyncio

    from tortoise import hosted_api, monitoring

    monkeypatch.setattr(monitoring, "record_write_failure_counts",
                        lambda: {"no_tenant": 3})
    monkeypatch.setattr(monitoring, "record_refusing",
                        lambda: {"reason": "no_tenant", "age_s": 2.0})
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)
    probe = {"ok": True, "latency_ms": 0.1, "error": None}
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(probe))

    body = asyncio.run(hosted_api.health())

    assert body["status"] == "degraded", body["record"]
    assert body["record"]["degraded_reasons"] == {"no_tenant": 3}


def test_a_quiet_record_block_reads_ok_through_the_real_seam(monkeypatch):
    """The OTHER direction of the fold, with NO stub on the record seam.

    `test_status_itself_degrades_not_only_the_sub_block` above stubs
    `record_refusing`, so it pins the degraded direction only. This leg stubs
    nothing on the record seam: it reads the PROCESS's real refusal state — the
    same read the four `status == "ok"` legs in
    `tests/test_backup_watcher_health.py` make. It is therefore the direct
    regression leg for the cross-file bleed (#10648 review P2): a refusal
    counted by ANY earlier leg in the worker reddens this one, which is only
    green because `tests/conftest.py::_no_ambient_record_refusal` clears the
    stamp and the counter on both sides of every test.
    """
    import asyncio

    from tortoise import hosted_api, monitoring

    assert monitoring.record_refusing() is None, (
        "the leg did not start from a clear refusal state — the conftest hoist "
        "is what makes this assertion a property of the code, not of the order"
    )
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)
    probe = {"ok": True, "latency_ms": 0.1, "error": None}
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(probe))

    body = asyncio.run(hosted_api.health())

    assert body["status"] == "ok", body["record"]
    assert body["record"]["degraded"] is False, body["record"]
    assert body["record"]["refusing"] is None, body["record"]
    assert body["record"]["ok"] is True, body["record"]


def test_the_health_docstring_states_the_window_not_a_counter_fold():
    """`health()`'s own docstring must not teach the reading #10185 forbids.

    This is the ONE place a maintainer reads what degrades `status`, and an
    earlier revision of this PR stated *"a NON-ZERO record write-failure count
    … degrades `status`"* — which is the fold `_record_health` deliberately does
    NOT perform (it folds `record_refusing()`, a bounded window) and which
    #10185 forbids verbatim. A future lane reading the docstring and "fixing"
    the code to match it would re-introduce the forever-red flag this half
    exists to remove, so the sentence is pinned instead of trusted.
    """
    from tortoise import hosted_api

    flat = " ".join((hosted_api.health.__doc__ or "").split())

    assert "RECORD_REFUSAL_WINDOW_SECONDS" in flat, (
        f"the docstring must name the WINDOW that degrades status: {flat[-400:]}"
    )
    assert "record.refusing" in flat, (
        f"the docstring must name the signal it folds: {flat[-400:]}"
    )
    assert "write-failure count" not in flat, (
        "the docstring states the cumulative-counter fold #10185 forbids and "
        f"the code does not do: {flat[-400:]}"
    )


# ════════════════════════════════════════════════════════════════════════════
# 3. One flattener, one representation
# ════════════════════════════════════════════════════════════════════════════


def test_the_record_flattener_agrees_with_the_graph_flattener():
    """The record boundary's copy of the rule must equal the graph's ONE home.

    `record_projection` restates the rule instead of importing
    `projection/entities.py` because it deliberately loads with no imports beyond
    the standard library (it must be importable without the graph stack). That
    duplication is only safe while the two agree, so this pins them together over
    the shapes a journal snapshot can carry — and it iterates the GRAPH
    declaration rather than a hardcoded key list, so the next property that joins
    `_POINT_FLATTEN_LIST_PROPS` reds HERE instead of silently leaving the record
    holding a second representation (`#11682`: `quote` was widened graph-side only,
    and a hardcoded `search_keys` case list stayed green while the record column
    received `["a b", "c"]` for a graph value of `a b c`).
    """
    from tortoise.projection.entities import (
        _POINT_FLATTEN_LIST_PROPS,
        flatten_search_keys_prop,
    )

    # ⛔ A non-empty FLOOR first: emptying BOTH declarations satisfies the
    # equality below AND empties the behavioural loop, which is the one polarity
    # that silently stores `["a b", "c"]` again while every assertion passes
    # (#11706 re-record review). A rule that names no key flattens nothing.
    assert record_projection._POINT_FLATTEN_COLUMNS, (
        "the record flatten declaration is EMPTY — the equality below cannot "
        "see it, and neither can the loop below it"
    )
    assert _POINT_FLATTEN_LIST_PROPS, (
        "the graph flatten declaration is EMPTY — same vacuity, graph side"
    )

    # The two declarations must name the SAME set: a key flattened on one side
    # and not the other is the #10620 drift in whichever direction it points.
    # Compared through the COLUMN spelling the code actually applies —
    # `_split_properties` calls `snake()` BEFORE `_record_column_value` sees the
    # property — never by raw string identity: a future key that is not
    # snake-invariant would otherwise red this pin with "the declarations
    # disagree" over an implementation that agrees, and then drive the loop
    # below at a key the rule never receives (#11706 re-record review).
    assert {record_projection.snake(key) for key in _POINT_FLATTEN_LIST_PROPS} == set(
        record_projection._POINT_FLATTEN_COLUMNS), (
        "the record and graph flatten declarations disagree: record="
        f"{sorted(record_projection._POINT_FLATTEN_COLUMNS)} graph="
        f"{sorted(record_projection.snake(k) for k in _POINT_FLATTEN_LIST_PROPS)}"
    )

    # ⛔ REACHABILITY — a key declared here but NOT a `point` COLUMN never reaches
    # `_record_column_value` in production at all: `_split_properties` falls
    # through to `tail[prop] = value`, so the record's `props` jsonb carries the
    # RAW LIST while every assertion above stays green (#11706 re-record review).
    point_columns = record_projection.table_columns()["point"]
    for key in sorted(_POINT_FLATTEN_LIST_PROPS):
        assert record_projection.snake(key) in point_columns, (
            f"{key} is declared as a flattened list prop but "
            f"{record_projection.snake(key)!r} is not a `point` record column — "
            "the rule is unreachable there, so the props tail would store the "
            "raw list the fulltext index cannot match"
        )

    cases = [
        ["err4032", "release"],
        ["  padded  ", "keys"],
        [],
        ["only"],
        ("tuple", "form"),
    ]
    for key in sorted(_POINT_FLATTEN_LIST_PROPS):
        for case in cases:
            props = {key: list(case)}
            flatten_search_keys_prop(props)
            graph_kept = key in props
            actual = record_projection._record_column_value(
                "point", record_projection.snake(key), case)
            record_kept = actual is not record_projection._OMIT
            assert record_kept == graph_kept, (
                f"{key} {case!r}: the graph "
                f"{'keeps' if graph_kept else 'POPS'} the key but the record "
                f"{'writes' if record_kept else 'omits'} the column — a blank "
                "list must never clear a value the graph preserves"
            )
            if graph_kept:
                assert actual == props[key], (
                    f"{key} {case!r}: record={actual!r} graph={props[key]!r} — "
                    "the record column must carry the GRAPH's flat string, not "
                    "the list the fulltext index cannot match"
                )


def test_content_columns_are_named_but_never_quoted():
    """A failure line names the column, not the user's conversation (#10620 P2).

    The DSN on this same seam is fingerprinted, never printed; a captured turn is
    the same class of value. `content` and `props` are the two a point insert
    carries, and they are exactly what an 80-character value cap would still leak.
    """
    node = record_projection.Node("point", "Point", "node_key")
    statement = record_projection._upsert_statement(
        "o", "g", node, "pt-1",
        {"content": "my bank PIN is 1234 and my address is 5 Example St",
         "search_keys": "err4032 release"},
        {"long": "x" * 500})
    text = record_projection._statement_attribution(statement, 1, 2)

    assert "content=<" in text and "props=<" in text, text
    assert "bank PIN" not in text, f"a captured turn reached the log: {text}"
    assert "x" * 20 not in text, f"the props tail reached the log: {text}"
    assert "search_keys=<15 chars withheld>" in text, (
        "`search_keys` is the SAME datum as `content` (E3's \"aliases + verbatim "
        "tokens\", derived from content ∪ search_keys), so printing it leaks the "
        f"turn through the column next door: {text}"
    )
    assert "err4032 release" not in text, (
        f"the user's text reached the log: {text}"
    )


def test_the_same_document_title_is_withheld_by_BOTH_halves_of_the_line():
    """⛔ ONE log line, ONE withhold rule — the halves must not disagree.

    `record_writer._IDENTITY_CONTENT_KEYS` rules that a `source`'s `name` is its
    document title and prints only `name=<N chars withheld>`. Two clauses later
    on the SAME line, the attribution printed the string verbatim
    (`name=Q3 pricing memo`), because `name` was never added to
    `record_projection._CONTENT_COLUMNS` — a withhold true of each half and false
    of the line (#10648 review P2). The column is still NAMED with its length;
    `node_key`/`url` is what an operator chases, and those are printed.
    """
    from tortoise import record_writer

    node = record_projection.Node("source", "Source", "node_key")
    col_values, tail = record_projection._split_properties(
        "source",
        {"name": "Q3 pricing memo", "url": "https://example.test/memo",
         "title": "Q3 pricing memo"},
        columns=record_projection.table_columns(),
        refused=record_projection.refused_props_names(),
    )
    assert "name" in col_values, (
        "the premise: a source snapshot's `name` reaches the `name` COLUMN "
        f"(got {sorted(col_values)})")

    statement = record_projection._upsert_statement(
        "o", "g", node, "https://example.test/memo", col_values, tail)
    attribution = record_projection._statement_attribution(statement, 2, 2)
    assert "INSERT INTO public.source" in attribution, attribution
    assert "name=<15 chars withheld>" in attribution, (
        "the attribution half still prints the datum the identity half "
        f"withholds: {attribution}")
    assert "name=Q3 pricing memo" not in attribution, attribution

    # …and on the whole line the operator reads, both halves together: the
    # identity clause, then the statement attribution.
    identity = record_writer._record_identity(
        {"type": "SourceCreated", "source": {"name": "Q3 pricing memo"}})
    line = record_projection._failure_line(
        attribution, record_projection._driver_fault(
            _DriverError("permission denied for table source", code="42501")))
    whole = f"record=SourceCreated {identity}: {line}"
    assert "source.name=<15 chars withheld>" in whole, whole
    assert "Q3 pricing memo" not in whole, (
        f"a document title reached the one warning line: {whole}")


def test_the_identity_withhold_list_is_a_subset_of_the_attribution_list():
    """The two withhold lists are ONE rule, pinned mechanically (#10648 P2).

    `_CONTENT_COLUMNS` is a maintained list (no schema can say whether a column's
    value is the user's text), so a name added to `_IDENTITY_CONTENT_KEYS` for
    the identity half can silently stay printable in the attribution half — which
    is exactly how `name` was withheld on one side of a line and printed on the
    other. The subset relation is the invariant: an identity key is content, so
    the attribution must withhold the column of the same name.
    """
    from tortoise import record_writer

    # The identity keys are PAYLOAD names (`eventId` is not a column; `event_id`
    # is), so they are compared in the column's own form — `snake` is the SAME
    # normalisation `_split_properties` applies before `_column_pair` ever sees
    # the name. Comparing raw payload names would demand a `_CONTENT_COLUMNS`
    # entry for `eventId`, which `test_content_columns_are_real_record_columns`
    # would then (correctly) reject as a name no table has.
    missing = sorted(
        {record_projection.snake(key)
         for key in record_writer._IDENTITY_CONTENT_KEYS}
        - record_projection._CONTENT_COLUMNS)
    assert not missing, (
        f"identity field(s) {missing} are withheld by `_record_identity` but "
        "PRINTED by `_statement_attribution` — the same value would appear twice "
        "on one failure line, once withheld and once quoted")


def test_content_columns_are_real_record_columns():
    """The withhold list may not rot into names no table has (#10620 cycle 3).

    TWO checks, and they are NOT the same kind of check — stated precisely here
    because an earlier revision claimed "both directions" for what is one
    mechanical direction plus one maintained list (#10648 review P3):

    1. NO STALE ENTRY, mechanically over the schema the projection itself reads:
       a withheld name that is not a column of any record table never matches, so
       it silently stops protecting anything.
    2. NO MISSING ENTRY, for the columns this PR's own diff can leak. This one
       CANNOT be derived from the schema — a column name does not say whether its
       value is the user's text — so it is a maintained list, and a new
       content-bearing column has to be added to it deliberately.
    """
    columns = set()
    for table_columns in record_projection.table_columns().values():
        columns |= set(table_columns)

    unknown = sorted(record_projection._CONTENT_COLUMNS - columns)
    assert not unknown, (
        f"withheld name(s) that are not a column of any record table: {unknown} — "
        "a stale entry protects nothing")
    for required in ("content", "quote", "props", "_search_text", "title",
                     "topics", "search_keys",
                     # #10648 review P2: the identity-NAME columns. `name` is
                     # ruled content by `record_writer._IDENTITY_CONTENT_KEYS`
                     # (a source's `name` IS its title); `canonical_name` is the
                     # same datum on `object`, and `event.subject` /
                     # `event.object` are an event's own participant text.
                     "name", "canonical_name", "subject", "object"):
        assert required in record_projection._CONTENT_COLUMNS, (
            f"{required!r} carries the user's own text and must be withheld")


def test_a_mapping_valued_search_keys_is_refused_not_serialized():
    """A dict is neither the graph's form nor a list — never JSON into `text`."""
    with pytest.raises(record_projection.RecordProjectionError) as caught:
        record_projection._record_column_value(
            "point", "search_keys", {"k": "v"})
    assert "second representation" in str(caught.value)
    # The divergence is STATED: the graph leaves a dict as-is, the record refuses.
    assert "leaves a dict as-is" in str(caught.value)


def test_a_string_passes_through_unchanged():
    """The canonical form is NEVER re-flattened — a string is already the answer."""
    assert record_projection._record_column_value(
        "point", "search_keys", "err4032 release") == "err4032 release"


# ════════════════════════════════════════════════════════════════════════════
# 4. The Postgres proof — the row LANDS (skips without a database)
# ════════════════════════════════════════════════════════════════════════════


def _tenant() -> tuple[str, str]:
    """A tenant pair no other test in this PROCESS has used (see #9307 F2)."""
    suffix = uuid.uuid4().hex[:12]
    return f"org-{suffix}", f"graph-{suffix}"


def _record_schema_conn():
    """A superuser connection carrying the record migrations, or skip.

    The record's write path may NOT use it: `replay()` refuses a connection that
    can bypass RLS (#9554). It is the seeding/assertion half only.
    """
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the row proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2-binary is the record's driver")
    from tests.test_9078_record_written_by_production_path import (
        _ensure_record_schema,
    )

    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    _ensure_record_schema(conn)
    return conn


def _app_role_conn():
    from tests.test_9078_record_written_by_production_path import (
        _record_replay_db,
    )

    return _record_replay_db()


def _seed(conn, tenant: tuple[str, str], keys: list[str]) -> None:
    org, graph = tenant
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (graph, org, graph))
        for key in keys:
            cur.execute(
                "INSERT INTO public.node_identity "
                "(org_id, graph_id, node_label, node_key) "
                "VALUES (%s, %s, 'Point', %s) ON CONFLICT DO NOTHING",
                (org, graph, key))
        cur.execute("DELETE FROM public.point WHERE org_id=%s AND graph_id=%s",
                    (org, graph))


def _search_keys_row(conn, tenant: tuple[str, str], key: str):
    org, graph = tenant
    with conn.cursor() as cur:
        cur.execute(
            "SELECT search_keys, pg_typeof(search_keys)::text FROM public.point "
            "WHERE org_id=%s AND graph_id=%s AND node_key=%s",
            (org, graph, key))
        return cur.fetchone()


def test_the_record_column_is_text_and_a_point_with_search_keys_lands():
    """THE regression `main` fails: the row is REFUSED there, and lands here.

    Measured on the pre-fix tree (#10620): the same record raised
    `invalid input syntax for type json` and the row was absent. The assertion is
    on the ROW, not on the absence of an exception — a projection that reported
    success while dropping the row is the failure being fixed.
    """
    conn = _record_schema_conn()
    tenant = _tenant()
    key = f"pt-{uuid.uuid4().hex[:10]}"
    _seed(conn, tenant, [key])
    org, graph = tenant
    try:
        row = _search_keys_row(conn, tenant, key)
        assert row is None
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='point' "
                "AND column_name='search_keys'")
            assert cur.fetchone()[0] == "text", (
                "point.search_keys is not TEXT — the 22P02 refusal is back, and "
                "every point carrying search keys will be dropped again")

        replay_conn = _app_role_conn()
        try:
            record_projection.replay(
                [_point_record(key, "err4032 release")], replay_conn,
                org_id=org, graph_id=graph, position=False)
            replay_conn.commit()
        finally:
            replay_conn.close()

        row = _search_keys_row(conn, tenant, key)
        assert row is not None, (
            "the point carrying search_keys did not reach the record — this is "
            "the 302-row drop (#10620)")
        assert row[0] == "err4032 release", row
    finally:
        conn.close()


def test_a_list_valued_snapshot_lands_flattened_not_as_json():
    """A raw graph value may still be an ARRAY (`sparse.py`) — and must land as
    the SAME flat string, never as `["a", "b"]` serialized into a text column."""
    conn = _record_schema_conn()
    tenant = _tenant()
    key = f"pt-{uuid.uuid4().hex[:10]}"
    _seed(conn, tenant, [key])
    org, graph = tenant
    try:
        replay_conn = _app_role_conn()
        try:
            record_projection.replay(
                [_point_record(key, ["err4032", "release"])], replay_conn,
                org_id=org, graph_id=graph, position=False)
            replay_conn.commit()
        finally:
            replay_conn.close()

        row = _search_keys_row(conn, tenant, key)
        assert row is not None, "the list-valued point did not reach the record"
        assert row[0] == "err4032 release", (
            f"the record stored a SECOND representation of the same datum: {row!r}"
        )
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════════
# 5. The migration's own array arm — the rows that ALREADY exist
# ════════════════════════════════════════════════════════════════════════════

MIGRATION_PATH = (Path(__file__).resolve().parent.parent
                  / "supabase" / "migrations"
                  / "20261102000002_record_point_search_keys_text.sql")

#: Where the array arm's non-`str` element divergence is STATED. NOT the
#: migration header: that file had already landed on `main` when the statement
#: was written, and migrations are append-only history
#: (`.github/scripts/check-migration-append-only` refuses an edit to a landed
#: one), so the paragraph lives in the doc a future lane actually reads. The leg
#: below pins it there (#10648 review P1).
DIVERGENCE_DOC_PATH = (Path(__file__).resolve().parent.parent
                       / "docs" / "epics"
                       / "2026-10-10-7879-postgres-record"
                       / "08-hosted-prerequisites.md")


def _shipped_flatten_ddl() -> str:
    """The `USING` helper, EXTRACTED from the shipped migration — not a copy.

    `USING` cannot carry a subquery, so the array arm is a helper function that
    the migration creates and then DROPS; it is not in the schema afterwards.
    The test re-creates the file's OWN SQL, so a drift between the shipped text
    and the rule the write path applies fails here instead of on a converted row
    in the record (a converted row is thereafter `replay(journal)`-divergent for
    good: `rebuild_from_record` copies this column back into the graph).
    """
    text = MIGRATION_PATH.read_text(encoding="utf-8")
    match = re.search(
        r"CREATE OR REPLACE FUNCTION public\.record_flatten_search_keys"
        r".*?\n\$\$;", text, re.S)
    assert match, (
        f"{MIGRATION_PATH.name} no longer carries the flattening helper its "
        "`USING` expression needs"
    )
    return match.group(0)


@pytest.mark.parametrize("value,expected", [
    (["err4032", "release"], "err4032 release"),
    (["a", "", "b"], "a b"),          # blank element DROPPED, never `a  b`
    (["a", " ", "b"], "a b"),         # …a whitespace element is blank too
    (["  x  "], "x"),                 # each element TRIMMED, never `  x  `
    (["\ttab\t"], "tab"),
    ([" "], None),                    # no surviving element -> no value at all
    ([], None),
    (["only"], "only"),
    ("err4032 release", "err4032 release"),
    # ⛔ The three arms BEYOND the write path (#10648 review P3). They are not
    # reachable from a new record (`_record_column_value` refuses a dict and
    # never emits a number/boolean), but a pre-existing row can hold one, and
    # the conversion's job there is to PRESERVE the value — `ELSE NULL` would
    # drop it. Pinned so a rewrite of any arm goes red instead of silently
    # changing what an existing row becomes.
    # ⚠️ A NON-`str` ARRAY ELEMENT is the fourth divergence and it is deliberately
    # NOT a row here: the `assert actual == written` below holds `actual ==
    # written` for every list in this matrix, and that equality is FALSE for
    # `["a", null]` / `["a", {"k": "v"}]` by design. It has its own leg —
    # `test_the_array_arms_non_str_elements_are_the_stated_divergence` — which
    # asserts BOTH ends (the SQL text and the writer's Python-repr rule) so the
    # exception is pinned rather than implied.
    ({"k": "v"}, None),                  # object: preserved, rendering-tolerant
    (42, "42"),                         # number: its own text form
    (True, "true"),                     # boolean: its own text form
])
def test_the_migration_converts_existing_rows_by_the_write_path_rule(value, expected):
    """One datum, one form: the `USING` arm must equal the writer's rule.

    The migration's header claims the converted value lands in "the one canonical
    form". It did not: `string_agg(element, ' ')` neither trimmed an element nor
    dropped a blank one, so a pre-existing array row could land as `a  b` or
    `  x  ` (or as `" "` where the graph's fold has NO value) — a SECOND shape of
    the datum, which is the defect this migration exists to remove. The
    statement-builder path already gets this right (`_record_column_value`
    restates `flatten_search_keys_prop`); this pins the SQL to the same rule, arm
    for arm, by executing the shipped text.
    """
    conn = _record_schema_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_shipped_flatten_ddl())
            try:
                cur.execute(
                    "SELECT public.record_flatten_search_keys(%s::jsonb)",
                    (json.dumps(value),))
                actual = cur.fetchone()[0]
            finally:
                cur.execute("DROP FUNCTION IF EXISTS "
                            "public.record_flatten_search_keys(jsonb)")
    finally:
        conn.close()

    if isinstance(value, dict):
        # `None` above means "assert the PRESERVE property, not a rendering":
        # jsonb's text form is the spaced `{"k": "v"}` on every server this
        # project runs, but a formatting difference must not read as a VALUE
        # difference — the arm exists so an existing value is not dropped, and
        # that is what is asserted.
        assert actual is not None, (
            f"an object row was DROPPED by the conversion ({value!r}) — `ELSE "
            "NULL` is the data loss this arm exists to prevent"
        )
        assert '"k"' in actual and '"v"' in actual, (
            f"the object's own content did not survive the conversion: {actual!r}"
        )
    else:
        assert actual == expected, (
            f"{value!r} converted to {actual!r}, expected {expected!r} — the record "
            "would hold a second representation of a value the graph holds as "
            f"{expected!r}"
        )

    if isinstance(value, list):
        # …and the writer's own rule agrees, which is the property the header
        # claims and the reason a converted row stays `replay(journal)`-equal.
        written = record_projection._record_column_value(
            "point", "search_keys", value)
        written = None if written is record_projection._OMIT else written
        assert actual == written, (
            f"{value!r}: the migration's arm gives {actual!r} while the write "
            f"path gives {written!r} — the two shapes diverge"
        )
    elif isinstance(value, dict):
        # ⛔ The divergence IN THE OTHER DIRECTION, pinned (#10648 review P3):
        # the conversion PRESERVES an object row, the write path REFUSES one.
        # Both are deliberate — the record must not gain a second representation
        # going forward, and a bootstrap must not drop a value that already
        # exists — so the test states both rather than letting the arms drift.
        with pytest.raises(record_projection.RecordProjectionError):
            record_projection._record_column_value("point", "search_keys", value)


def test_the_array_arms_non_str_elements_are_the_stated_divergence():
    """⛔ The ONE array shape where the migration's arm is not the write path.

    `jsonb_array_elements_text` yields SQL `NULL` for a JSON `null` element and
    the element's own JSON text for a nested object/array, so `["a", null]`
    lands as `a` and `["a", {"k": "v"}]` as `a {"k": "v"}`. The write path's
    `str(item)` would give `a None` / `a {'k': 'v'}` — PYTHON REPRS — and the
    migration's header says, in as many words, why it does not reproduce them
    (#10648 review P3). This leg asserts BOTH ends, so a change to either side
    (the SQL arm or the writer's rule) goes red instead of quietly making the
    converted column `replay(journal)`-divergent.

    Reachability is low and that is why this is a documented divergence rather
    than a rewrite: `search_keys` is `list[str]` in `commit_schema`, so only a
    pre-existing `jsonb` row (the old `_jsonb_or_scalar` path, or a hand-written
    row) can hold a non-`str` element.
    """
    conn = _record_schema_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(_shipped_flatten_ddl())
            try:
                cur.execute(
                    "SELECT public.record_flatten_search_keys(%s::jsonb)",
                    (json.dumps(["a", None]),))
                null_element = cur.fetchone()[0]
                cur.execute(
                    "SELECT public.record_flatten_search_keys(%s::jsonb)",
                    (json.dumps(["a", {"k": "v"}]),))
                nested_element = cur.fetchone()[0]
            finally:
                cur.execute("DROP FUNCTION IF EXISTS "
                            "public.record_flatten_search_keys(jsonb)")
    finally:
        conn.close()

    # An element with NO text is dropped — never the literal `None`.
    assert null_element == "a", (
        f"a JSON `null` element converted to {null_element!r}; the arm drops an "
        "element with no text (the write path would emit the repr `None`)")
    # A nested element keeps ITS OWN text — never a Python repr.
    assert nested_element is not None and nested_element.startswith("a "), (
        f"a nested element converted to {nested_element!r}")
    assert '"k"' in nested_element, (
        f"the nested element's own content did not survive: {nested_element!r}")
    assert "'k'" not in nested_element, (
        f"the arm reproduced a PYTHON REPR in a persisted column: {nested_element!r}")

    # …and the other end of the divergence, DB-free, so neither side can move
    # without this leg following it.
    assert record_projection._record_column_value(
        "point", "search_keys", ["a", None]) == "a None"
    assert record_projection._record_column_value(
        "point", "search_keys", ["a", {"k": "v"}]) == "a {'k': 'v'}"


def test_the_divergence_is_STATED_in_the_doc_the_next_lane_reads():
    """The divergence must be READABLE, not just asserted (#10648 review P1/P3).

    The paragraph is what a future lane reads before deciding the arm is "the
    SAME rule" and tidying it. It used to live in the migration HEADER, which
    cannot be edited: the file had already landed on `main`, and the repo's
    append-only-migrations gate refuses an `M` on a landed one
    (`.github/scripts/check-migration-append-only`). So the statement moved to
    the doc the next lane reads, and this pins it THERE — names BOTH ends
    (`a None` / `a {'k': 'v'}` on the Python side) so a rewrite of the text
    cannot quietly drop the reason the arms differ.
    """
    stated = DIVERGENCE_DOC_PATH.read_text(encoding="utf-8")

    assert "NON-`str` ELEMENT" in stated, (
        "the divergence doc no longer states the non-`str` array-element "
        "divergence, and the arm still diverges "
        "(`test_the_array_arms_non_str_elements_are_the_stated_divergence`)")
    for named in ("a None", "a {'k': 'v'}", "are_the_stated_divergence"):
        assert named in stated, (
            f"the divergence paragraph no longer names {named!r}, so a reader "
            "cannot tell which shape is the exception")
    assert "NON-`str` ELEMENT" not in MIGRATION_PATH.read_text(encoding="utf-8"), (
        "the landed migration's header must stay UNEDITED (append-only "
        "history); the statement belongs in the doc above")
