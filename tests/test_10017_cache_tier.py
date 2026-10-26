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
from tortoise import record_projection as rp
from tortoise.projection.entities import _EntityHandlers
from tortoise.record_projection import _RECORD_ONLY_KEYS, refused_props_names, table_columns

# ── the schema half ────────────────────────────────────────────────────────

def test_the_point_table_declares_the_tier_column() -> None:
    assert "cache_tier" in table_columns()["point"]


def test_the_tier_is_refused_as_a_props_key() -> None:
    """Without this, `_props_from_row` raises `UnknownRecordColumn` for every
    tenant — and the tempting "fix" is to delete the guard that caught it."""
    assert "cache_tier" in refused_props_names()["point"]


def test_the_tier_never_reaches_a_node_as_a_property() -> None:
    """The RECORD half is the schema refusal (test above); the GRAPH half is the
    fold's strip set, so a `PointAdded` carrying the tier cannot set a node
    property a journal replay never writes.

    ⛔ Both halves are asserted, because BOTH are deletable in silence: removing
    the `_META_KEYS` entry reds nothing anywhere else in the suite (measured),
    and the record strip is checked below by its own leg.

    ⛔ Which is also why the tier CANNOT be set through an event payload — it is
    stripped before the insert, so the row lands the DEFAULT `graph`. Step 7
    must write it as its own column statement; a test that assumed otherwise
    would pass here and materialise turns in production."""
    assert "cache_tier" in _EntityHandlers._META_KEYS, "the GRAPH half is gone"
    assert "cache_tier" in _RECORD_ONLY_KEYS, "the RECORD half is gone"


def test_an_entity_mutation_cannot_carry_the_tier() -> None:
    """`EntityMutated` is the arm where the tier lands in a COLUMN, not a tail.

    `_split_properties` checks a `drop` name BEFORE `column in cols`, so this
    drop is the only thing standing between a `state` map and
    `UPDATE public.point SET cache_tier = 'record_only'` — which flips a REAL
    graph point to record-only, so `read_record_view` stops projecting it and a
    record-sourced rebuild LOSES the node. Deleted, that reds nothing (measured).
    """
    for label in ("Point", "Object"):
        record = {"type": "EntityMutated", "ts": "T", "id": "n1", "label": label,
                  "op": "restatus",
                  "state": {"status": "retracted", "cache_tier": "record_only"}}
        statements = rp.mutations_for(record, org_id="o", graph_id="g")
        assert statements, f"{label}: no mutation emitted, so this proves nothing"
        for st in statements:
            assert "cache_tier" not in st.sql, (label, st.sql)
            # ⛔ SUBSTRING, not tuple-element membership: for Object/Subject the
            # tier rides inside ONE jsonb param, so `"record_only" not in
            # st.params` never fires and the leg would be vacuous.
            assert not any("cache_tier" in str(p) for p in st.params), (
                label, st.sql, st.params)
            assert not any("record_only" in str(p) for p in st.params), (
                label, st.sql, st.params)


# ── the read half
# `read_record_view` reads NODE tables and (now) the relation table. The fake
# keys on the MAIN table so the relation query's `public.point` SUBQUERY — the
# record-only key set — is not mistaken for a node read.

def _read_view(monkeypatch, point_rows, labels=("Point",)):
    sqls: list[str] = []

    def _rows(cur, sql, params):
        sqls.append(sql)
        if sql.startswith("SELECT * FROM public.point"):
            # Honour the reader's own predicate rather than restating the answer:
            # delete the filter from the reader and this returns both rows.
            if "cache_tier = 'graph'" in sql:
                return [r for r in point_rows if r["cache_tier"] == "graph"]
            return list(point_rows)
        return []

    monkeypatch.setattr(cp, "_rows", _rows)
    monkeypatch.setattr(cp._rp, "scope_connection", lambda conn, org: conn)
    view = cp.read_record_view(object(), org_id="o1", graph_id="g1",
                               labels=labels)
    return view, sqls


def test_a_record_only_row_is_not_planned_into_the_graph(monkeypatch) -> None:
    """Two rows, one of each tier. Only the graph-tier row becomes a node — and
    the turn row (id shape + is_episodic) is exactly the one that must vanish."""
    view, sqls = _read_view(monkeypatch, [
        {"id": "s1_t0", "is_episodic": True, "cache_tier": "record_only",
         "props": {"id": "s1_t0"}},
        {"id": "p-real", "is_episodic": False, "cache_tier": "graph",
         "props": {"id": "p-real"}},
    ])
    assert [node.key for node in view.nodes] == ["p-real"]
    assert any("cache_tier = 'graph'" in sql for sql in sqls)


def test_the_filter_is_never_applied_to_a_table_without_the_column(monkeypatch) -> None:
    """A `cache_tier` predicate on any other node table is a SQL error on every
    rebuild of every tenant (`42703`), so where it may appear is the contract.

    ⛔ Read EVERY node table here, not just `Point`: with `labels=("Point",)` the
    only statements mentioning the tier are the point read and the relation
    subquery, so applying the predicate to every table — the exact reversal this
    test names — left it green (round-2 review, measured)."""
    _v, sqls = _read_view(monkeypatch, [], labels=tuple(cp.NODE_TABLES))
    node_reads = {f"SELECT * FROM public.{t}" for t in cp.NODE_TABLES.values()}
    other_tables = [sq for sq in sqls
                    if any(sq.startswith(p) for p in node_reads)
                    and not sq.startswith("SELECT * FROM public.point")]
    assert other_tables, "no non-point node read was exercised — test is blind"
    for sql in other_tables:
        assert "cache_tier" not in sql, sql


def test_a_tiered_turns_edge_is_not_planned_either(monkeypatch) -> None:
    """A CONTAINS edge to a `record_only` turn would MATCH nothing and land in
    `relations_not_written`, making the operator-facing "declared partial"
    signal permanently non-empty — and therefore worthless."""
    _v, sqls = _read_view(monkeypatch, [])
    relation_sql = [sq for sq in sqls
                    if sq.startswith("SELECT * FROM public.relation")]
    assert relation_sql, "the relation read is missing"
    # Scoped to POINT endpoints, not to every key: another label may legally
    # carry the same key string (node_identity is keyed by label too).
    # ⛔ The `NOT` is the assertion. Without it this test passes on the INVERTED
    # predicate — which keeps ONLY the edges incident to a record-only point, so
    # `rebuild_from_record` drops every real edge while `relations_not_written`
    # reports clean (its denominator is the view's own count) — total, silent
    # loss with a clean operator signal.
    assert "NOT (src_label = 'Point' AND src_key IN" in relation_sql[0]
    assert "NOT (tgt_label = 'Point' AND tgt_key IN" in relation_sql[0]
    # …and the exclusion is of the record-only keys, not of every key.
    assert "cache_tier <> 'graph'" in relation_sql[0]

def test_the_forward_re_definition_cannot_shrink_the_refusal_set() -> None:
    """`refused_props_names()` UNIONS the CREATE with the forward ALTERs, so a
    re-declaration that names FEWER keys is invisible to the parser while the
    DATABASE stops refusing them — the silent-shrink class `subject` already has
    a leg for. `point` is now a re-declaration site, so it gets one too."""
    import re

    from tortoise.record_projection import RECORD_MIGRATIONS, _migrations_dir

    base = _migrations_dir()
    sql = "\n".join((base / n).read_text(encoding="utf-8")
                     for n in RECORD_MIGRATIONS if (base / n).exists())
    create = set()
    for table, body in re.findall(
            r"CREATE TABLE IF NOT EXISTS public\.(\w+)\s*\((.*?)\n\);", sql, re.S):
        if table != "point":
            continue
        for arr in re.findall(r"NOT \(props \?\| ARRAY\[(.*?)\]", body, re.S):
            create |= {n.strip().strip("'") for n in arr.split(",") if n.strip()}
    alters = [set(n.strip().strip("'") for n in arr.split(",") if n.strip())
              for table, arr in re.findall(
                  r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?public\.(\w+)\b"
                  r"[^;]*?ADD\s+CONSTRAINT\s+chk_\w+_props_no_derived\s+CHECK"
                  r"\s*\(\s*NOT\s*\(\s*props\s*\?\|"
                  r"\s*ARRAY\[(.*?)\]\s*::text\[\]", sql, re.S)
              if table == "point"]
    assert alters, "no forward re-declaration found for point — the pin is blind"
    # ⚠️ Compare against EVERYTHING that could have introduced a name, not just
    # the CREATE: `refused_props_names()` unions all forward ALTERs while the SQL
    # DROP+ADD REPLACES, so a name an INTERMEDIATE re-declaration added and the
    # newest one omits shrinks the live constraint with the parser still
    # reporting it. Latent today (one ALTER) and silent the day it is not.
    superset = set(create)
    for earlier in alters[:-1]:
        superset |= earlier
    lost = superset - alters[-1]
    assert not lost, f"the newest forward re-declaration dropped {sorted(lost)}"
    assert "cache_tier" in alters[-1]
