"""#10017 R2 — a turn row must not become a graph node, and the TIER says so.

The seam this pins: `cache_projection.read_record_view` reads every row of every
node table, and `record_plan` plans a `MERGE` for each — so with turns living in
`public.point` (they are what `record_read` serves), `rebuild_from_record` would
re-create the very turn nodes the journal rebuild correctly skips. Two rebuild
routes, two different graphs.

The fix is a COLUMN, not a reader's opinion: §9.1 puts raw turns outside the
graph, so a turn row is `cache_tier = 'record_only'` and the cache projection
skips it. A predicate expressed in one reader would be inherited by nobody —
the next export, sibling projection or tool to read this table would node-ify
the row again, silently.

⛔ The column is ALSO in the schema's `props_no_derived` refusal, and that half
is load-bearing, not cosmetic: `_props_from_row` has three destinations and "no
fourth" — a column it can neither place nor refuse raises `UnknownRecordColumn`
and would break cache projection for every tenant.
"""

from __future__ import annotations

from tortoise import cache_projection as cp
from tortoise.record_projection import (
    _RECORD_ONLY_KEYS,
    RECORD_ONLY_TIER,
    refused_props_names,
    table_columns,
)

# ── the schema half ────────────────────────────────────────────────────────

def test_the_point_table_declares_the_tier_column() -> None:
    assert "cache_tier" in table_columns()["point"]


def test_the_tier_is_refused_as_a_props_key() -> None:
    """Without this, `_props_from_row` raises `UnknownRecordColumn` for every
    tenant — and the tempting "fix" is to delete the guard that caught it."""
    assert "cache_tier" in refused_props_names()["point"]


def test_the_tier_reaches_the_column_and_never_a_node_property() -> None:
    """⛔ `cache_tier` must NOT be in `_RECORD_ONLY_KEYS`, and this reverses the
    first draft of this file (#10070) for a measured reason.

    That set is stripped BEFORE `_split_properties` — i.e. before the
    column-vs-`props`-tail split — so listing the tier there dropped it ahead of
    the COLUMN: every replayed turn row kept its `DEFAULT 'graph'`, the cache
    projection kept selecting it, and `rebuild_from_record` re-created exactly
    the turn nodes the tier exists to keep out of the graph. The guard restored
    the resurrection it was added to prevent.

    The intent is served where it belongs — by the schema, which refuses
    `cache_tier` as a `props` key, so `_props_from_row` drops it by name and no
    node can carry it.
    """
    assert "cache_tier" not in _RECORD_ONLY_KEYS
    assert "cache_tier" in table_columns()["point"]
    assert "cache_tier" in refused_props_names()["point"]

    # The property, not the call site: a carrier record's tier lands the COLUMN
    # and leaves the props tail empty.
    from tortoise import record_projection as rp

    statements = rp.mutations_for(
        {"type": "TurnRecorded",
         "point": {"id": "s1_t0", "content": "c", "is_episodic": True,
                   "cache_tier": RECORD_ONLY_TIER}},
        org_id="o", graph_id="g")
    insert = next(s for s in statements
                  if s.sql.startswith("INSERT INTO public.point"))
    assert "cache_tier" in insert.sql, insert.sql
    assert RECORD_ONLY_TIER in insert.params, insert.params


# ── the read half: the property, not the call site ─────────────────────────

def test_a_record_only_row_is_not_planned_into_the_graph(monkeypatch) -> None:
    """Two rows, one of each tier. Only the graph-tier row becomes a node — and
    the turn row (id shape + is_episodic) is exactly the one that must vanish.
    The reader's own SQL carries the filter, so the test asserts the SQL as
    well as the result: a future edit that drops the predicate fails here."""
    point_rows = [
        {"id": "s1_t0", "is_episodic": True, "cache_tier": "record_only",
         "props": {"id": "s1_t0"}},
        {"id": "p-real", "is_episodic": False, "cache_tier": "graph",
         "props": {"id": "p-real"}},
    ]
    sqls: list[str] = []

    def _rows(cur, sql, params):
        sqls.append(sql)
        if "public.point " in sql:
            # Honour the reader's own filter rather than restating the answer.
            if "cache_tier = 'graph'" in sql:
                return [r for r in point_rows if r["cache_tier"] == "graph"]
            return list(point_rows)
        return []

    monkeypatch.setattr(cp, "_rows", _rows)
    monkeypatch.setattr(cp._rp, "scope_connection", lambda conn, org: conn)

    view = cp.read_record_view(object(), org_id="o1", graph_id="g1",
                               labels=("Point",))
    keys = [node.key for node in view.nodes]
    assert keys == ["p-real"], keys
    assert any("cache_tier = 'graph'" in sql for sql in sqls)


def test_the_filter_is_applied_to_point_only(monkeypatch) -> None:
    """Other tables have no such column; a filter added to them would be a
    SQL error on every rebuild, so it is exquisitely important that it is not."""
    sqls: list[str] = []

    def _rows(cur, sql, params):
        sqls.append(sql)
        return []

    monkeypatch.setattr(cp, "_rows", _rows)
    monkeypatch.setattr(cp._rp, "scope_connection", lambda conn, org: conn)
    cp.read_record_view(object(), org_id="o1", graph_id="g1")
    assert all("cache_tier" not in sq for sq in sqls if "public.point " not in sq)
