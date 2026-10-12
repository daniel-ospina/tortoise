"""Interface B — the cache projection's contract, made executable (#7971).

WHAT THIS PINS. `docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 makes
**Postgres the record and FalkorDB a derived hot cache**. `tortoise/projection/`
already is a replay — **of the journal**. This module's subject is the *other*
direction: the record's **state**, materialised into the cache, one way.

Four claims, and each is a test here rather than a sentence in a design doc:

1. **ONE-WAY BY CONSTRUCTION.** Nothing on this path reads the cache to decide
   what to project. The only reads the materializer issues are the endpoint
   `MATCH`es, which resolve a **record** coordinate — never a cache fact.
2. **THE CACHE IS NEVER AUTHORITATIVE.** Every property the projection writes
   has a **PRIMARY claim in the record**, read from the schema and the placement
   table (`#7968`/`#7955`) — and no DERIVED/VOLATILE name is ever written, which
   is what makes the clause non-vacuous: the schema *refuses* those names, and a
   projection that dropped the distinction would carry them.
3. **REBUILDABLE FROM THE RECORD ALONE.** A rebuild's node key set and per-node
   field set are a function of the record and of nothing else, and projecting
   the same view twice yields the same statement multiset (idempotent).
4. **A COLD REGION READS `not-hot`, NEVER `empty`.** The declaration is written
   **after** the writes and is a function of what was materialised, so a record
   key the projection did not write is not claimed hot — and an `empty` read
   over it becomes **`degraded`**, never `empty`.

WHY THE PLACEMENT TABLE IS IMPORTED, NOT RE-DECLARED. `#7968`'s
`tests/test_record_schema_placement.py` already parses §17.5 and resolves its
eight rows; `tortoise/cache_projection.py` reads the same table for the property
namespace. This suite asserts the two readings agree with the schema on disk, so
a third declaration cannot appear.

NO DATABASE IS NEEDED for everything above — the live half of S3/S9 (project →
destroy the cache → re-project from the record alone) is
`tests/test_cache_projection_live.py`, which skips visibly when no engine is
reachable rather than passing vacuously.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_record_schema_placement import (
    SECTION_TABLE,
    expand_edge_property,
    parse_placement,
)
from tortoise import cache_projection as cp
from tortoise import record_projection as rp
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    RESIDENCY_HOT,
    RESIDENCY_NOT_HOT,
    ResidencyDeclaration,
    apply_residency,
    mark_residency,
)
from tortoise.status_vocabulary import STATUS_DEGRADED, STATUS_EMPTY

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = Path(REPO_ROOT / "supabase" / "migrations")

#: Every (section, property, class) the placement table declares, §2.7 applied.
PLACEMENT_ROWS = parse_placement()

#: `{table: {column}}` and `{table: {refused props name}}`, from the SQL.
TABLE_COLUMNS = rp.table_columns(MIGRATIONS)
REFUSED = rp.refused_props_names(MIGRATIONS)

#: section → table, plus the Edges section's home.
SECTION_TABLE_ALL = {**SECTION_TABLE, "Edges": "relation"}


def _rows(cls: str):
    """Placement rows of one class, edge predicates EXPANDED to properties."""
    for section, prop, c in PLACEMENT_ROWS:
        if c != cls:
            continue
        for name in expand_edge_property(prop):
            yield SECTION_TABLE_ALL[section], name


def _view():
    nodes = [
        _node("Point", {"id": "p-1", "content": "c", "is_operator": False,
                        "point_kind": "claim", "props": {"openSetProp": 1}}),
        _node("Object", {"name": "acme", "props": {}}),
    ]
    rels = [cp.RecordRelation(
        rel_type="aboutObject", src_label="Point", src_key="p-1",
        tgt_label="Object", tgt_key="acme", idx=0, props={})]
    return cp.RecordView(nodes=tuple(nodes), relations=tuple(rels))


def _node(label: str, row: dict):
    table = cp.NODE_TABLES[label]
    key_column = rp.snake(cp.KEY_PROPERTY[label])
    full = {c: None for c in TABLE_COLUMNS[table]}
    full[key_column] = f"k-{label}"
    full.update(row)
    full.setdefault("props", {})
    return cp.node_from_row(label, full)


# ── 1. the read shape is total and lossless ────────────────────────────────


def test_every_primary_property_has_a_canonical_spelling_that_is_projected():
    """The record's PRIMARY field set survives, spelled the GRAPH's way.

    `pointKind` is the graph property; `point_kind` is the column. A projection
    that wrote the column name would rename the whole namespace, and the S1/S3
    field contract would be broken by a spelling.
    """
    unresolved = []
    for table, prop in _rows("P"):
        if table not in cp.placement_columns():
            continue  # a predicate-only row (no property, e.g. `mitigated_by.*`)
        canonical = cp.canonical_property(table, rp.snake(prop))
        if canonical not in cp.projected_properties(table):
            unresolved.append((table, prop, canonical))
    assert not unresolved, (
        "a PRIMARY property the placement table declares is NOT projected — "
        f"the cache would be poorer than the record: {unresolved}"
    )


def test_a_primary_property_round_trips_through_a_record_row():
    """Arrange a real row shape and assert the value lands under its own name."""
    for table, prop in _rows("P"):
        if table == "relation":
            continue  # exercised by the relation test below
        if rp.snake(prop) not in TABLE_COLUMNS.get(table, ()):
            continue
        label = next(k for k, v in cp.NODE_TABLES.items() if v == table)
        canonical = cp.canonical_property(table, rp.snake(prop))
        got = _node(label, {rp.snake(prop): "v"})
        assert got.props.get(canonical) == "v", (
            f"{table}.{rp.snake(prop)} did not project to {canonical!r} "
            f"(the placement table spells it {prop!r})"
        )


def test_no_derived_property_has_a_record_column():
    """DERIVED/VOLATILE ⇒ no column — the schema is the first line, stated.

    This is WHY the behavioural refusal below is about the `props` tail and not
    a column: `#7968`'s pin already fails if a DERIVED name gets a column, so a
    record row cannot carry one. A future schema that adds one reds THIS test
    and `test_record_schema_placement.py` together, rather than silently
    promoting a recomputed value to authority.
    """
    stray = []
    for table, prop in _rows("D"):
        if rp.snake(prop) in TABLE_COLUMNS.get(table, ()):
            stray.append((table, prop))
    assert not stray, f"a DERIVED property has a record column: {stray}"


def test_a_derived_name_in_the_props_tail_is_refused():
    """S3's contract clause at its one reachable door.

    A column cannot carry a DERIVED name (the test above). The `props` tail
    CAN, and the tail is written verbatim — so the projection refuses a name
    the schema's own CHECK refuses, loudly. This is the mutation target: delete
    the guard in `_props_from_row` and the cache acquires `confidence`, an EP
    scalar recomputed from the structure, as stored authority.
    """
    with pytest.raises(cp.CacheProjectionError):
        _node("Point", {"id": "p-1", "props": {"confidence": 0.9}})
    with pytest.raises(cp.CacheProjectionError):
        _node("Object", {"name": "acme", "props": {"embedding": [0.1]}})


def test_a_derived_column_on_a_record_row_projects_nothing():
    """A FORWARD GUARD — today no DERIVED row has a column, so it cannot fire.

    `test_no_derived_property_has_a_record_column` asserts the precondition:
    `#7968`'s schema pin makes a DERIVED/VOLATILE name with a record column
    impossible, so the column door is shut by construction. The `props`-tail
    door is the reachable one and is exercised (non-vacuously) by
    `test_a_derived_name_in_the_props_tail_is_refused`. This test is kept as the
    guard for the day a future schema gives a DERIVED name a column: it asserts
    that precondition explicitly, so it can never pass by the loop body simply
    never running.
    """
    with_column = [
        (table, prop) for table, prop in _rows("D")
        if rp.snake(prop) in TABLE_COLUMNS.get(table, ())
    ]
    assert with_column == [], (
        "a DERIVED property now HAS a record column — the column door is live "
        f"and this test's body must decide it: {with_column}"
    )
    leaked = []
    for table, prop in _rows("D"):
        column = rp.snake(prop)
        if column not in TABLE_COLUMNS.get(table, ()):
            continue  # no column exists — covered by the test above
        label = next((k for k, v in cp.NODE_TABLES.items() if v == table), None)
        if label is None:
            continue
        got = _node(label, {column: [0.1, 0.2]})
        if prop in got.props or column in got.props:
            leaked.append((table, prop))
    assert not leaked, (
        "a DERIVED/VOLATILE property is projected as if it had a PRIMARY claim: "
        f"{leaked}"
    )


def test_the_unclassified_column_set_is_exactly_the_declared_four():
    """A NEW record column reds this suite rather than being silently dropped.

    Four columns are neither in the placement table nor refused by the schema:
    §2.8's narrowed carve-out exception (`baseline_alpha`/`baseline_beta`) and
    the two Subject lifecycle fields. They are declared in
    `cache_projection.ADDITIONAL_PRIMARY_COLUMNS`. Anything else is a column
    nobody classified, which is a LOUD failure — never a silent drop.
    """
    declared = {
        (table, column)
        for table, cols in cp.ADDITIONAL_PRIMARY_COLUMNS.items()
        for column in cols
    }
    unclassified = set()
    for table, columns in TABLE_COLUMNS.items():
        if table not in cp.placement_columns():
            continue
        mapped = set(cp.placement_columns()[table])
        refused = set(REFUSED.get(table, ()))
        rest = (set(columns) - mapped - refused
                - cp.bookkeeping_columns(table) - declared)
        unclassified |= {(table, c) for c in rest}
    assert unclassified == set(), (
        "the record gained a column the cache projection has not classified — "
        f"{sorted(unclassified)}"
    )
    assert declared, "the declaration itself vanished"


def test_an_unclassified_column_fails_loudly():
    """The fail-closed direction of the test above."""
    row = {**{c: None for c in TABLE_COLUMNS["point"]},
           "id": "p-1", "invented_by_a_future_migration": 1, "props": {}}
    with pytest.raises(cp.UnknownRecordColumn):
        cp.node_from_row("Point", row)


def test_the_placement_tables_ambiguities_are_exactly_the_five_declared():
    """A column the table spells two ways cannot be named by the record alone.

    ⚠️ A FINDING, pinned so it cannot drift: the placement table enumerates
    FIVE columns in BOTH spellings — four on `public.source` (`session_id`,
    `event_id`, `source_path`, `document_kind`) and one on `public.event`
    (`session_id`) — because the graph's document writer emits the snake forms
    while the session-capture writer mints the camel forms
    (`projection/entities.py::_SOURCE_NODE_PROPS` says so itself). `snake()` is
    therefore **not injective** over the graph's namespace, so a record-sourced
    cache cannot be byte-identical to a journal-sourced one for these five
    fields until one spelling wins (#7955/#7968).
    """
    assert sorted(cp.ambiguities()) == [
        "event.session_id",
        "source.document_kind",
        "source.event_id",
        "source.session_id",
        "source.source_path",
    ]
    # ⛔ The CHOICE is pinned, not just the SET: `canonical_property` delegating
    # to `placement_columns` would otherwise make the obvious assertion
    # tautological, and flipping `_prefer` to snake-first would leave the suite
    # green while silently renaming five graph properties.
    expected_canonical = {
        "event.session_id": "sessionId",
        "source.document_kind": "documentKind",
        "source.event_id": "eventId",
        "source.session_id": "sessionId",
        "source.source_path": "sourcePath",
    }
    got = {
        column: cp.canonical_property(*column.split(".", 1))
        for column in cp.ambiguities()
    }
    assert got == expected_canonical, (
        "the canonical (chosen) spelling drifted from camelCase-first — a "
        f"record-sourced cache would rename the field: {got}"
    )


# ── 2. the keys are the contract's, read from the schema ────────────────────


def test_key_properties_are_the_interface_e_contract_read_from_the_ddl():
    """Read from `node_identity`'s generated CASE, never restated here."""
    expected = {
        "Point": "id",
        "Event": "eventId",
        "Subject": "name",
        "Object": "name",
        "Source": "url",
        "Session": "id",
    }
    assert expected == cp.KEY_PROPERTY
    sql = (MIGRATIONS / "20261012000001_record_event_stream.sql").read_text()
    assert "key_property" in sql, "the DDL moved — re-read the parser"


def test_a_node_key_is_the_labels_contract_key():
    assert _node("Subject", {"name": "acme"}).key == "acme"
    assert _node("Point", {"id": "1a-1"}).key == "1a-1"
    assert _node("Source", {"url": "https://x"}).key == "https://x"
    assert _node("Event", {"event_id": "ev-1"}).key == "ev-1"
    with pytest.raises(cp.UnprojectableRecord):
        _node("Point", {"id": None})


def test_the_key_property_is_projected_as_a_property_too():
    """`id`/`name`/`url` are PRIMARY properties, not only MERGE coordinates."""
    assert _node("Subject", {"name": "acme"}).props["name"] == "acme"
    assert _node("Source", {"url": "https://x"}).props["url"] == "https://x"


def test_the_props_tail_keeps_its_verbatim_property_names():
    """Open-set properties ride the tail unmapped — `SET n += $props`'s half."""
    got = _node("Point", {"id": "p-1",
                          "props": {"someOpenSetKey": 7, "another_one": "x"}})
    assert got.props["someOpenSetKey"] == 7
    assert got.props["another_one"] == "x"


# ── 3. one-way by construction ──────────────────────────────────────────────


def test_the_materializer_issues_no_cache_read():
    """No statement on this path may consult the cache to decide what to write.

    The projection builds its whole plan from the RECORD; the only read the
    plan contains is the endpoint `MATCH` inside a relation upsert, which
    resolves a record coordinate. A statement that RETURNed properties, or that
    read `:Meta`, would be the cache deciding — the defect §2.2 names.
    """
    view = _view()
    for stmt in cp.record_plan(view.nodes, view.relations):
        text = stmt.cypher
        assert ":Meta" not in text
        assert "RETURN" not in text, f"a read leaked into the plan: {text}"
        assert "MERGE" in text


def test_relation_endpoints_resolve_by_label_and_key_not_by_a_bare_id():
    """Interface E's rule, at the edge: a rebuilt edge binds by `(label, key)`.

    A resolver that matched `{id: $key}` would bind a `:Subject` by an opaque
    graph id and resolve to nothing on a rebuild (`#2489`/`#5086`).
    """
    rel = cp.RecordRelation(
        rel_type="aboutSubject", src_label="Point", src_key="p-1",
        tgt_label="Subject", tgt_key="acme", idx=0, props={})
    compact = cp.relation_statement(rel).cypher.replace(" ", "")
    assert "tgt:Subject{name:$tgt_key}" in compact, compact
    assert "src:Point{id:$src_key}" in compact, compact


def test_relation_props_ride_the_edge_and_the_predicate_is_the_rel_type():
    rel = cp.RecordRelation(
        rel_type="IMPL", src_label="Point", src_key="op-1",
        tgt_label="Point", tgt_key="in-1", idx=2,
        props={"label": "addresses", "anchor": "extractedFrom"})
    stmt = cp.relation_statement(rel)
    assert "MERGE (src)-[r:IMPL {idx:$idx}]->(tgt)" in stmt.cypher
    assert stmt.params["idx"] == 2
    assert stmt.params["props"]["anchor"] == "extractedFrom"


# ── 4. rebuild == record, and idempotent ────────────────────────────────────


def test_rebuild_equals_record_no_extra_node_and_no_extra_field():
    """The rebuild's field set is EXACTLY the record's — never a superset.

    This is S3's contract clause at the STATEMENT level: a superset would be the
    cache carrying state the record does not hold, i.e. the cache becoming a
    second authority. ⛔ The assertion runs over `plan.statements` — the thing
    that decides what is written — NOT over `plan.node_props()`, which returns
    `RecordNode.props` and would compare the record to itself (a mutation that
    made `node_statement` drop a field was invisible to the model-level check).
    """
    view = _view()
    plan = cp.record_plan(view.nodes, view.relations)
    assert plan.node_keys() == {(n.label, n.key) for n in view.nodes}
    for node in view.nodes:
        stmt = next(
            s for s in plan.statements
            if s.cypher.startswith(f"MERGE (n:{node.label} ")
            and s.params["key"] == node.key
        )
        written = set(stmt.params["props"])
        if "embedding" in stmt.params:
            # The vector is written as its own clause, not in the props map.
            written.add("embedding")
        assert written == set(node.props), (
            f"{node.label}/{node.key}: the cache would carry "
            f"{sorted(set(node.props) - written)} less than the record and "
            f"{sorted(written - set(node.props))} beyond it"
        )


def test_projection_is_idempotent():
    view = _view()
    first = cp.record_plan(view.nodes, view.relations)
    second = cp.record_plan(view.nodes, view.relations)
    assert [(s.cypher, s.params) for s in first] == \
        [(s.cypher, s.params) for s in second]


def test_a_point_embedding_is_written_with_the_vertex_form():
    got = _node("Point", {"id": "p-1", "embedding": [0.5, 0.25]})
    stmt = cp.node_statement(got)
    assert "vecf32($embedding)" in stmt.cypher
    assert stmt.params["embedding"] == [0.5, 0.25]


def test_a_verbatim_embedding_is_written_verbatim():
    """`embedding_verbatim` is the caller-owned vector's storage FORM."""
    got = _node("Point", {"id": "p-1", "embedding": [0.5],
                          "embedding_verbatim": True})
    stmt = cp.node_statement(got)
    assert "vecf32" not in stmt.cypher
    assert stmt.params["embedding"] == [0.5]
    # ⛔ The marker is PRIMARY and must survive WITH the vector, not only without
    # it: a record-sourced rebuild that dropped it leaves a caller-owned float64
    # vector unmarked, and the next journaled re-emit narrows it through
    # `vecf32` (#5004 — the `live != rebuild` class this flag prevents).
    assert stmt.params["props"]["embedding_verbatim"] is True


def test_a_verbatim_marker_without_a_vector_is_still_written():
    """The no-vector half of the same PRIMARY field."""
    got = _node("Point", {"id": "p-2", "embedding_verbatim": True})
    stmt = cp.node_statement(got)
    assert stmt.params["props"]["embedding_verbatim"] is True


def test_a_non_allowlisted_rel_type_is_refused_before_any_statement_is_built():
    """`rel_type` is interpolated into the query STRUCTURE — it must be allowed.

    `public.relation.rel_type` is `text` with no CHECK, so an arbitrary string
    reaches the interpolation. The allowlist is the same one every comparable
    site uses; refusing here means `record_plan` raises BEFORE the wipe, so a
    hostile row cannot build a plan that destroys the cache and then fails.
    """
    bad = cp.RecordRelation(rel_type="x]->(z) DETACH DELETE z //",
                            src_label="Point", src_key="a",
                            tgt_label="Point", tgt_key="b")
    with pytest.raises(cp.UnprojectableRecord):
        cp.relation_statement(bad)
    with pytest.raises(cp.UnprojectableRecord):
        cp.record_plan([], [bad])
    ok = cp.RecordRelation(rel_type="IMPL", src_label="Point", src_key="a",
                           tgt_label="Point", tgt_key="b")
    assert "r:IMPL" in cp.relation_statement(ok).cypher


def test_a_refused_record_row_leaves_the_cache_untouched():
    """A projection that refuses must not half-apply: the plan is built first."""
    bad = list(_view().nodes)
    bad.append({})  # not a RecordNode — the plan cannot be built at all
    with pytest.raises(AttributeError):
        cp.record_plan(bad)


# ── 5. the declaration: not-hot, never empty ────────────────────────────────


def test_an_unmaterialized_record_key_is_not_hot_and_never_empty():
    """The load-bearing consequence, end to end over the REAL marker.

    A record that holds two keys, a projection that materialised one: the
    unmaterialised key is NOT declared hot, so a zero-hit read for it is
    `not-hot`, and an `empty` status over it becomes `degraded` — never the
    `empty` that is indistinguishable from *"no such node"*.
    """
    record_keys = [("Point", "p-1"), ("Point", "p-2")]
    materialized = [("Point", "p-1")]
    decl = cp.declaration_for(record_keys, materialized)
    assert decl.coverage == COVERAGE_PARTIAL
    assert decl.covers(cp.declared_key("Point", "p-1"))
    assert not decl.covers(cp.declared_key("Point", "p-2"))

    marker = mark_residency(decl, hits=0, key=cp.declared_key("Point", "p-2"))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED
    # ...and the covered key still reads `hot`, so the marker did not simply
    # fire for everything.
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Point", "p-1")) == RESIDENCY_HOT


def test_a_homonymous_pair_across_labels_is_not_collapsed_to_one_hot_key():
    """⛔ THE IDENTITY IS `(label, key)`, NEVER THE KEY ALONE.

    `KEY_PROPERTY` gives `Subject` and `Object` the same key (`name`), so
    `Object/acme` and `Subject/acme` are TWO record nodes with ONE key string.
    A declaration that flattens them declares a **hot key that was never
    written**, and a zero-hit read over the OMITTED label then stands as
    `empty` — the §17.2 *"no such node"* false negative this module exists to
    prevent. Reachable through the documented `limit` mode: nodes are sorted by
    `(label, key)` and truncated, so a bound that cuts between two labels
    produces a partial write and (before this fix) a `complete` declaration.
    """
    record_keys = [("Object", "acme"), ("Subject", "acme")]
    materialized = [("Object", "acme")]
    decl = cp.declaration_for(record_keys, materialized)
    assert decl.coverage == COVERAGE_PARTIAL, (
        "the declaration claimed complete coverage for a label it did not write"
    )
    assert decl.hot_keys == frozenset({"Object:acme"})
    assert decl.covers(cp.declared_key("Object", "acme"))
    assert not decl.covers(cp.declared_key("Subject", "acme"))

    marker = mark_residency(decl, hits=0, key=cp.declared_key("Subject", "acme"))
    assert marker == RESIDENCY_NOT_HOT
    assert apply_residency(STATUS_EMPTY, marker) == STATUS_DEGRADED
    # The written label is still hot — the marker did not simply fire always.
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Object", "acme")) == RESIDENCY_HOT
    # ...and the soundness check sees the collapse too: a `complete` declaration
    # over this partial write is UNSOUND, which is what makes `declaration_for`
    # above non-optional rather than a nicety.
    assert cp.declaration_is_sound(record_keys, materialized, decl) is True
    lied = ResidencyDeclaration(coverage=COVERAGE_COMPLETE, source="liar")
    assert cp.declaration_is_sound(record_keys, materialized, lied) is False


def test_a_complete_projection_declares_complete_coverage():
    keys = [("Point", "p-1"), ("Object", "acme")]
    decl = cp.declaration_for(keys, keys)
    assert decl.coverage == COVERAGE_COMPLETE
    assert mark_residency(
        decl, hits=0, key=cp.declared_key("Point", "p-9")) == RESIDENCY_HOT


def test_declaring_a_key_hot_that_was_not_written_is_the_mutation_this_catches():
    """The mutation: a projection that claims complete coverage unconditionally.

    It is the one-line "fix" that would make the previous test pass vacuously —
    `coverage=complete` makes the marker unable to fire at all (§17.2: *with a
    complete cache the marker CANNOT fire*). Asserted here so the declaration
    cannot silently become a constant.
    """
    record_keys = [("Point", "p-1"), ("Point", "p-2")]
    decl = cp.declaration_for(record_keys, [("Point", "p-1")])
    assert decl.coverage != COVERAGE_COMPLETE, (
        "the declaration claimed complete coverage for a key it did not write"
    )
    lied = ResidencyDeclaration(coverage=COVERAGE_COMPLETE, source="liar")
    assert mark_residency(
        lied, hits=0, key=cp.declared_key("Point", "p-2")) == RESIDENCY_HOT
    assert cp.declaration_is_sound(
        record_keys, [("Point", "p-1")], lied) is False


def test_the_scope_prefixes_the_declared_keys():
    """Two graphs share one cache instance, so the hot set is scoped."""
    decl = cp.declaration_for([("Point", "p-1"), ("Point", "p-2")],
                              [("Point", "p-1")], scope="g_a")
    assert decl.hot_keys == frozenset({"g_a:Point:p-1"})
    assert decl.covers(cp.declared_key("Point", "p-1", scope="g_a"))
    assert not decl.covers(cp.declared_key("Point", "p-1"))


def test_an_empty_record_declares_partial_not_complete():
    """`complete` over an EMPTY scope would claim coverage nothing measured."""
    decl = cp.declaration_for([], [])
    assert decl.coverage == COVERAGE_PARTIAL


# ── 6. the record read shape, over a (fake) connection ──────────────────────


class _FakeCursor:
    def __init__(self, rows_by_table):
        self._rows = rows_by_table
        self._current: list[dict] = []

    def execute(self, sql, params=None):
        m = re.search(r"FROM public\.(\w+)", sql)
        self._current = self._rows.get(m.group(1), []) if m else []

    def fetchall(self):
        return list(self._current)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows_by_table):
        self._rows = rows_by_table

    def cursor(self):
        return _FakeCursor(self._rows)


def test_read_record_view_keys_by_the_contract_key():
    conn = _FakeConn({
        "point": [{"id": "p-1", "content": "c", "props": {}},
                  {"id": "p-2", "content": "d", "props": {}}],
        "object": [{"name": "acme", "props": {}}],
        "relation": [{"rel_type": "aboutObject", "src_label": "Point",
                      "src_key": "p-1", "tgt_label": "Object",
                      "tgt_key": "acme", "idx": 0, "props": {}}],
    })
    view = cp.read_record_view(conn, org_id="o", graph_id="g")
    assert {n.key for n in view.nodes} == {"p-1", "p-2", "acme"}
    assert [r.rel_type for r in view.relations] == ["aboutObject"]
    assert view.record_keys == (("Point", "p-1"), ("Point", "p-2"),
                                ("Object", "acme"))


def test_read_record_view_asks_for_the_scope_it_was_given():
    """A reader that forgot the tenancy predicate would project every tenant."""
    seen: list[str] = []

    class _Cursor(_FakeCursor):
        def execute(self, sql, params=None):
            seen.append(sql)
            super().execute(sql, params)

    class _Conn:
        def cursor(self):
            return _Cursor({})

    cp.read_record_view(_Conn(), org_id="o", graph_id="g")
    assert seen, "the reader issued no query at all"
    for sql in seen:
        assert "org_id = %s" in sql and "graph_id = %s" in sql
