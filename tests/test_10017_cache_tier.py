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

THIS FILE IS THE MERGE OF PR #10076 AND PR #10070'S TIGHTENINGS (review P1-2,
P1-3). #10070's head decides that the tier may NOT ride an event payload — the
record's strip set (`_RECORD_ONLY_KEYS`) drops it, the graph's skip set
(`_META_KEYS`) drops it, and `EntityMutated` drops it — while #10076's first
draft carried it through the payload straight into the column. Keeping only
#10076's removal left a PUBLIC write able to set placement authority
(`create_point(cache_tier=...)`, measured); keeping only #10070's strip silently
killed R2's record half. Both are kept here: the strip stays, and R2's tier
reaches the COLUMN through one explicit statement that only the CARRIER may
issue (`record_projection._turn_row_statements`, the carrier-keyed projection
that writes one `public.point` row per turn in `turns`). The property pinned is
unchanged — a turn row is `record_only` and never a node — and it is pinned from
one more direction (a forged `PointAdded` cannot move it).
"""

from __future__ import annotations

import pytest

from tortoise import cache_projection as cp
from tortoise import record_projection as rp
from tortoise.log import EventLog
from tortoise.projection.entities import _EntityHandlers
from tortoise.record_projection import (
    _RECORD_ONLY_KEYS,
    RECORD_ONLY_TIER,
    refused_props_names,
    table_columns,
)
from tortoise.sdk import TortoiseSDK

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
    stripped before the insert, so the row lands the DEFAULT `graph`. The
    carrier's own tier is written as its own column statement
    (`test_the_carrier_writes_the_tier_to_the_column_not_the_props_tail`); a
    test that assumed the payload passthrough would pass here and materialise
    turns in production."""
    assert "cache_tier" in _EntityHandlers._META_KEYS, "the GRAPH half is gone"
    assert "cache_tier" in _RECORD_ONLY_KEYS, "the RECORD half is gone"


def test_a_forged_point_added_cannot_place_the_row() -> None:
    """#10017 review P1-2, the hole #10070's strip closes and this branch's
    first draft opened: a `PointAdded` payload carrying the tier.

    Measured on that draft, through the public SDK:
    `create_point(..., cache_tier="record_only")` put `cache_tier` on the NODE
    and wrote `public.point.cache_tier` from the journal snapshot — a real claim
    permanently hidden from `read_record_view` and therefore from every
    `rebuild_from_record`. The strip is what makes that a silent no-op at the
    record layer; the intake rejection
    (`test_a_tenant_write_cannot_place_the_row`) is what tells the caller.
    """
    record = {"type": "PointAdded",
              "point": {"id": "p1", "content": "a real graph claim",
                        "pointKind": "statement",
                        "cache_tier": RECORD_ONLY_TIER}}
    statements = rp.mutations_for(record, org_id="o", graph_id="g")
    assert statements, "no mutation emitted, so this proves nothing"
    for st in statements:
        assert "cache_tier" not in st.sql, st.sql
        assert not any(RECORD_ONLY_TIER in str(p) for p in st.params), (
            st.sql, st.params)


def test_the_carrier_writes_the_tier_to_the_column_not_the_props_tail() -> None:
    """R2's record half, under the strip: the turn row IS `record_only`.

    This is the assertion #10070's own comment predicted would be needed
    ("Step 7 must write it as its own column statement"). Delete that statement
    and the row lands the `graph` DEFAULT, so `read_record_view` projects the
    turn and `rebuild_from_record` re-creates it — the resurrection, restored.

    Pinned as the property (the plan), not the call site: the INSERT carries the
    COLUMN and the props tail does not carry the key at all.
    """
    statements = rp.mutations_for(
        {"type": rp.TURNS_RECORD_TYPE, "contains_session": "s1",
         "turns": [{"id": "s1_t0", "content": "c", "is_episodic": True,
                    "pointKind": "event", "speaker": "user",
                    "status": "draft", "createdAt": "2026-10-25T00:00:00Z",
                    "embedding": None}]},
        org_id="o", graph_id="g")
    insert = next(s for s in statements
                  if s.sql.startswith("INSERT INTO public.point"))
    assert "cache_tier" in insert.sql, insert.sql
    assert RECORD_ONLY_TIER in insert.params, insert.params
    # …and NOT through the tail, which is the passthrough that let a public
    # write reach the column.
    assert "cache_tier" not in str(insert.params[-1]), insert.params[-1]


def test_a_carrier_with_no_turns_is_REFUSED() -> None:
    """A `TurnsRecorded` with an empty turn list is not a turn row — it reports a
    capture as filed while the record holds none of it. Refuse rather than fall
    back to an empty statement set (the DEFAULT-tier bug's silent cousin)."""
    with pytest.raises(rp.RecordProjectionError, match="turns"):
        rp.mutations_for(
            {"type": rp.TURNS_RECORD_TYPE, "turns": []},
            org_id="o", graph_id="g")


def test_a_tenant_write_cannot_place_the_row(tmp_path) -> None:
    """#10017 review P1-2, the INTAKE half — measured through the public SDK.

    `create_point(..., cache_tier="record_only")` and
    `update_point(pid, cache_tier="graph")` were both accepted on this branch's
    head and MOVED placement: the first hid a real claim from the cache
    projection (and so from `rebuild_from_record`), the second un-hid it. The
    tier is the record's own declaration, written by the record's producers, so
    a props bag reaching it is refused at the one intake every props surface
    passes through (`sdk._sanitize_props`).
    """
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"))
    try:
        with pytest.raises(ValueError, match="cache_tier"):
            sdk.create_point("statement", "a claim that must stay visible",
                             cache_tier=RECORD_ONLY_TIER)
        point = sdk.create_point("statement", "a claim that must stay visible")
        assert sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i}) RETURN p.cache_tier",
            params={"i": point["id"]}).result_set == [[None]]
        with pytest.raises(ValueError, match="cache_tier"):
            sdk.update_point(point["id"], cache_tier="graph")
    finally:
        sdk.close()


def test_a_legacy_journal_line_cannot_put_the_tier_on_a_node(tmp_path) -> None:
    """The GRAPH half, BEHAVIOURALLY: a replay must not write the key.

    A journal written before this change can carry `cache_tier` inside a
    `PointAdded` snapshot (nothing refused it then), and `_persist_extra_props`
    applies an open-set payload onto the node. Without the `_META_KEYS` entry
    the replay puts the tier on `:Point` while a record-sourced rebuild drops it
    (`_RECORD_ONLY_KEYS`) — the two rebuild routes diverge on a property, which
    is the divergence this whole change removes.

    ⛔ Asserted through a REAL replay, not by reading the skip set: membership
    of a frozenset is a shape, and the shape is what a later edit preserves
    while the behaviour changes.
    """
    log_path = str(tmp_path / "events" / "sdk.jsonl")
    EventLog(log_path).append({
        "type": "PointAdded", "id": "p-legacy-10017", "kind": "statement",
        "point": {"id": "p-legacy-10017", "content": "a legacy claim",
                  "pointKind": "statement", "is_episodic": False,
                  "cache_tier": RECORD_ONLY_TIER},
    })
    sdk = TortoiseSDK(str(tmp_path / "tortoise.db"), event_log_path=log_path)
    try:
        sdk._get_proj().rebuild(EventLog(log_path), confirm_destructive=True)
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i}) RETURN p.cache_tier",
            params={"i": "p-legacy-10017"}).result_set
    finally:
        sdk.close()
    assert rows == [[None]], (
        "the replay materialised `cache_tier` as a NODE property — a "
        f"record-sourced rebuild never does: {rows}")


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


def test_the_builder_REFUSES_a_tier_on_any_op_but_delete() -> None:
    """#10017 review P3 — a tier on a state op is a record that lies.

    `_journal_entity_mutation` accepted `cache_tier` for ANY op although only
    the delete arm has a fold that reads it (`_delete_statements`' demote).
    Pre-fix the call below is accepted and the assembled record carries a tier
    no fold applies — placed-by-nothing, while reading as an applied placement.
    """
    sdk = TortoiseSDK()
    try:
        with pytest.raises(ValueError, match="cache_tier"):
            sdk._journal_entity_mutation(
                "Point", "p-1", "restatus", state={"status": "retracted"},
                cache_tier=RECORD_ONLY_TIER)
    finally:
        sdk.close()


def test_provenance_cannot_rewrite_the_tier(monkeypatch) -> None:
    """#10017 review P3 — the EXPLICIT argument wins over a provenance bag.

    `provenance` is merged AFTER `cache_tier`, so without the reserve entry a
    caller passing both could have the tier silently replaced — and the tier
    decides hard-delete vs demote in the delete fold (`graph` is refused loudly
    by `_delete_statements`; a VALID value would not be)."""
    sdk = TortoiseSDK()
    seen: dict = {}
    monkeypatch.setattr(sdk, "_emit_event",
                        lambda _type, **record: seen.update(record))
    try:
        sdk._journal_entity_mutation(
            "Point", "s1_t0", "delete", cache_tier=RECORD_ONLY_TIER,
            provenance={"cache_tier": "graph", "session_id": "s1"})
    finally:
        sdk.close()
    assert seen.get("cache_tier") == RECORD_ONLY_TIER, seen
    assert seen.get("session_id") == "s1", "non-reserved provenance was dropped"


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


def test_the_filter_is_never_applied_to_a_table_without_the_column(monkeypatch) -> None:
    """Same property as the leg above, read EVERY node table.

    ⛔ `labels=("Point",)` (the leg above) leaves the relation subquery as the
    only other statement mentioning the tier, so applying the predicate to every
    table — the exact reversal this test names — left that leg green
    (#10070 round-2 review, measured)."""
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
