"""`#14552` — the PER-ID record ask behind the `:Point` strip: `point_vector_keys`.

The write gate (`record_vector.mirror_required`) answers about a LABEL —
``record_vectors >= graph_vectors`` — and `#13971`'s review recorded that a label aggregate
cannot license dropping ONE id's graph vector: `projection._rebuild_all_replay`'s pass-1b
restore runs for a GRAPH-ONLY id whose journal wrote no vector, i.e. exactly the population
the aggregate cannot speak about. The prescribed fix was *"a per-id ask (does the record hold
THIS row)"*, and this module is that ask's read half.

The pins below cover the three properties the fold's writer depends on:

  * it answers the SUBSET the record really holds (a row whose `embedding` is NULL is NOT a
    copy — answering it would license deleting the only one);
  * it is SCOPED, because an unscoped read returns nothing under the production role and
    "nothing" means "write every vector" — safe, but a silent lie the caller must not inherit;
  * it CHUNKS rather than TRUNCATES, because a cap would answer "the record holds none of
    these" for every id past the boundary, which is the fail-OPEN direction for its caller.
"""
from __future__ import annotations

import pytest

from tortoise import record_read


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn
        self._rows: list[tuple] = []

    def execute(self, sql: str, params) -> None:
        self._conn.statements.append((sql, params))
        if "node_key IN" not in sql:
            # The tenant-scope prologue: `set_config` then the read-back/role check,
            # whose answer is `(tenant, bypassing)`. `False` is what a correctly
            # provisioned reader role reports.
            self._rows = [(self._conn.org_id, False)]
            return
        _org, _graph, keys = params
        self._conn.asked.extend(keys)
        self._rows = [(key,) for key in keys if key in self._conn.held]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _Conn:
    """A record connection double: it answers the keys it HOLDS, and nothing else."""

    def __init__(self, held, org_id: str = "org") -> None:
        self.held = set(held)
        self.org_id = org_id
        self.statements: list[tuple[str, object]] = []
        self.asked: list[str] = []

    def cursor(self):
        return _Cursor(self)


def _scoped(conn: _Conn) -> bool:
    return any("set_config" in sql for sql, _params in conn.statements)


def test_it_answers_only_the_keys_that_carry_a_vector():
    conn = _Conn(held={"pt-1", "pt-3"})
    assert record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=["pt-1", "pt-2", "pt-3"]
    ) == frozenset({"pt-1", "pt-3"})
    # ⛔ AND THE NULL ARM IS THE ONE THAT MATTERS: a row the record holds with NO
    # `embedding` is not a copy, and `_Conn` only answers `held`, so `pt-2` (absent) and
    # any NULL-vector row are both simply not in the answer.
    assert record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=["pt-2"]
    ) == frozenset()


def test_it_asks_the_column_the_dense_leg_SERVES_not_the_bare_payload():
    """
    ⛔ ASKING THE PAYLOAD ALONE IS INSUFFICIENT, and the direction of the error is the
    dangerous one (`#14552` review, P1 — this test used to pin the payload-only predicate).

    `embedding_vec` is `GENERATED … STORED` from `embedding` through
    `record_embedding_vector`, which answers NULL for any payload it cannot convert — a
    non-384 width, a double-encoded array, an object, `[]`, a `[null, …]` element
    (`supabase/migrations/20261109000001_record_embedding_vector.sql` item 2). Those rows
    keep their jsonb `embedding` and are **not served**: the dense leg filters on
    `embedding_vec IS NOT NULL` (`record_vector.vector_search`), the coverage NUMERATOR
    counts the same column (`record_vector._record_vector_count`), and
    `embedding_residual_scan` measures the split and says such rows "are served by the graph
    leg only".

    So a payload-only predicate answers HELD for a row the record cannot serve, the fold
    withholds the graph's only servable copy, and `#5004` forbids re-encoding it. Requiring
    `embedding_vec` as well can only move an answer from HELD to "not held" — it WRITES the
    graph vector — which is the conservative direction; the accompanying
    `embedding IS NOT NULL` conjunct is a no-op belt-and-braces guard (the generated column's
    converter is STRICT), so this test asserts INCLUSION of the serving column and never that
    the payload column is absent. This test reads the STATEMENT because that is the one thing
    a fake row cannot lie about.
    """
    conn = _Conn(held={"pt-1"})
    record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=["pt-1"])
    selects = " ".join(sql for sql, _p in conn.statements)
    assert "embedding_vec IS NOT NULL" in selects, selects
    # …and it is part of the WHERE, i.e. it FILTERS the answer rather than being mentioned
    # elsewhere in the statement. (The payload conjunct beside it is a no-op guard and is
    # deliberately not pinned here: removing it can only make the answer a SUPERSET, which
    # this caller turns into "write the vector".)
    assert "embedding_vec IS NOT NULL" in selects.split("WHERE", 1)[1], selects


def test_it_installs_the_tenant_scope():
    conn = _Conn(held={"pt-1"}, org_id="org-1")
    record_read.point_vector_keys(
        conn, org_id="org-1", graph_id="graph-1", keys=["pt-1"])
    assert _scoped(conn), conn.statements


def test_it_CHUNKS_the_id_list_rather_than_truncating_it(monkeypatch):
    """A cap would read as "the record holds none of these" past the boundary."""
    monkeypatch.setattr(record_read, "POINT_VECTOR_KEY_CHUNK", 2)
    keys = [f"pt-{i}" for i in range(5)]
    conn = _Conn(held=set(keys))
    got = record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=keys)
    assert got == frozenset(keys), got
    reads = [params for sql, params in conn.statements if "node_key IN" in sql]
    assert len(reads) == 3, reads            # 2 + 2 + 1
    assert sorted(conn.asked) == sorted(keys), conn.asked


def test_an_empty_population_asks_the_record_NOTHING():
    conn = _Conn(held=set())
    assert record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=[]) == frozenset()
    assert record_read.point_vector_keys(
        conn, org_id="org", graph_id="graph", keys=["", None]) == frozenset()
    assert conn.statements == []


# ── the fold's conservative door ────────────────────────────────────────────


def test_the_fold_treats_every_unknown_as_write_the_vector(monkeypatch):
    """`projection._record_point_vector_keys` — UNKNOWN IS EMPTY, and empty means WRITE.

    ⛔ THE POLARITY IS THE OPPOSITE OF THE STRIP TOOL'S. For the read gate an unknown keeps
    the graph copy (writes cost bytes, skipping costs the row), so every failure here must
    answer "the record holds nothing": a missing tenant, an unarmed record, a connection
    that cannot open, a read that raises. An empty answer restores every vector, which is
    exactly the pre-`#14552` behaviour on every lane that cannot measure.
    """
    from tortoise import projection as projection_mod
    from tortoise import record_writer

    monkeypatch.setattr(record_writer, "record_tenant", lambda: None, raising=False)
    monkeypatch.setattr(
        "tortoise.record_serve.open_record",
        lambda *a, **k: pytest.fail("the record was opened with no tenant installed"),
        raising=True)
    assert projection_mod._record_point_vector_keys(["pt-1"]) == frozenset()

    monkeypatch.setattr(record_writer, "record_tenant",
                        lambda: ("org", "graph"), raising=False)
    monkeypatch.setattr(
        "tortoise.record_serve.open_record",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("unreachable")),
        raising=True)
    assert projection_mod._record_point_vector_keys(["pt-1"]) == frozenset()


def test_the_fold_returns_what_the_record_answers_and_closes_the_connection(monkeypatch):
    from tortoise import projection as projection_mod
    from tortoise import record_read, record_writer

    closed: list[bool] = []

    class _RecordConn:
        def close(self) -> None:
            closed.append(True)

    seen: dict = {}

    def _read(conn, *, org_id, graph_id, keys):
        seen.update(org_id=org_id, graph_id=graph_id, keys=list(keys))
        return frozenset({"pt-1"})

    monkeypatch.setattr(record_writer, "record_tenant",
                        lambda: ("org-1", "graph-1"), raising=False)
    monkeypatch.setattr("tortoise.record_serve.open_record",
                        lambda *a, **k: _RecordConn(), raising=True)
    monkeypatch.setattr(record_read, "point_vector_keys", _read, raising=True)

    assert projection_mod._record_point_vector_keys(["pt-1", "pt-2"]) == frozenset({"pt-1"})
    assert seen == {"org_id": "org-1", "graph_id": "graph-1",
                    "keys": ["pt-1", "pt-2"]}, seen
    assert closed == [True], "the record connection was not closed"
