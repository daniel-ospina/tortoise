"""#4997 — the vector leg's served label set, and the store's indexed label set.

Two independent facts this issue makes knowable:

1. **What label does the vector leg serve?**  One declaration
   (``tortoise.security.ENTITY_TYPE_LABELS`` / ``entity_label``) that
   ``run_vector_query`` READS, proven by provenance (a value-equality assertion
   cannot distinguish "reads the declaration" from "re-derives the same
   strings").

2. **What does the store actually have indexed?**  A fail-open measurement of
   ``CALL db.indexes()`` taken once per store setup, with the gap between the
   served set and the measured set reported rather than inferred.

Neither half creates an index: ``#4997`` is V1-neutral by design. See
``docs/plans/2026-09-25-4997-vector-index-parity.md``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tortoise.security import (
    ENTITY_TYPE_LABELS,
    VALID_ENTITY_TYPES,
    entity_label,
)


def _legacy(et: str) -> str:
    """The exact pre-#4997 derivation in ``run_vector_query``.

    Kept here as the parity oracle: the declaration must reproduce this
    byte-for-byte, including the ``document -> Source`` (D10) and
    ``operator -> Point`` (#172) exceptions.
    """
    if et == "document":
        return "Source"
    return "Point" if et == "operator" else et.capitalize()


# ── Task 1: the declaration ────────────────────────────────────────────────


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_declaration_reproduces_the_legacy_derivation(et):
    assert entity_label(et) == _legacy(et)


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_declaration_declares_every_valid_entity_type(et):
    assert et in ENTITY_TYPE_LABELS


def test_declaration_has_no_extra_keys():
    """Reverse direction: the declaration may not serve a label the
    vocabulary does not admit. #5407 records this consistency assertion as
    absent from all six mapping sites today."""
    assert set(ENTITY_TYPE_LABELS) == set(VALID_ENTITY_TYPES)


def test_document_is_a_source_label():
    """D10 (ONTOLOGY v3.15 §4.4): a document IS a :Source — there is no
    :Document label. This is the mapping M2 mutates."""
    assert entity_label("document") == "Source"
    assert entity_label("source") == "Source"


def test_operator_is_a_point_label():
    """#172: operators are Points with is_operator=true."""
    assert entity_label("operator") == "Point"
    assert entity_label("point") == "Point"


def test_unknown_str_keeps_the_legacy_fallback():
    assert entity_label("widget") == "Widget"


@pytest.mark.parametrize("bad", [None, ["point"], {"point": 1}])
def test_non_str_raises_the_same_exception_type_as_today(bad):
    """A bare ``ENTITY_TYPE_LABELS.get(bad)`` would raise TypeError for an
    unhashable input, silently changing the failure mode callers see. The
    historical behaviour is AttributeError from ``.capitalize()``."""
    with pytest.raises(AttributeError):
        entity_label(bad)


def test_security_stays_a_stdlib_only_leaf():
    """The reason the declaration can live here at all: ``security.py``
    imports nothing from ``tortoise``, so the edge is acyclic in both
    directions."""
    src = Path(__file__).resolve().parent.parent.joinpath(
        "tortoise", "security.py"
    ).read_text()
    for banned in (
        "from .projection",
        "from tortoise.projection",
        "from .search_engine",
        "from tortoise.search_engine",
    ):
        assert banned not in src, f"{banned} would break the leaf invariant"


# ── Task 2: the query path READS the declaration (provenance, not value) ────
#
# The fixtures below do not exist elsewhere in the repo: `tests/
# test_4999_vector_mechanism.py`'s fakes return a `result_set` but record no
# emitted Cypher, and the artifact this task asserts on is the emitted Cypher.


_UNSET = object()


class _MockResult:
    def __init__(self, rows=_UNSET):
        # `None` is a MEANINGFUL result_set here (not measured) — it must not
        # collapse to [] (measured and empty). Hence the sentinel.
        self.result_set = [] if rows is _UNSET else rows


class _RecordingGraph:
    """Captures every emitted Cypher in ``.calls``.

    Never raises for the vector query itself, so the signature-B index branch
    stays live — a raise would fall through to the brute-force scan and the
    sentinel label would never appear in the captured Cypher.

    For ``db.idx.vector.createNodeIndex`` it DOES raise, mirroring both
    ``tests/test_falkordb_compat.py``'s ``_EngineGraph`` and the measured engine
    behaviour (the procedure is "not registered" on the docker image), which is
    exactly why ``_ensure_indexes`` carries the ``CREATE VECTOR INDEX``
    fallback. A fake that returned ``[]`` here would make the procedure
    "succeed", set ``_vector_index_api='procedure'``, and the fallback Cypher
    would never be emitted.
    """

    def __init__(self, index_rows=None, raise_on_indexes=False):
        self.calls: list[str] = []
        self._index_rows = index_rows if index_rows is not None else []
        self._raise_on_indexes = raise_on_indexes

    def query(self, cypher, params=None, timeout=None):
        self.calls.append(cypher)
        low = cypher.lower()
        if "db.indexes()" in low:
            if self._raise_on_indexes:
                raise RuntimeError("db.indexes() unavailable")
            return _MockResult(self._index_rows)
        if "db.idx.vector.querynodes" in low:
            return _MockResult([("near-1", 0.95)])
        if "db.idx.vector.createnodeindex" in low:
            raise RuntimeError(
                "Procedure `db.idx.vector.createNodeIndex` is not registered"
            )
        return _MockResult([])


@pytest.fixture
def recording_graph():
    return _RecordingGraph()


def _vec(recording_graph, et):
    from tortoise.search_engine import run_vector_query

    run_vector_query(
        recording_graph,
        [0.1] * 384,
        limit=3,
        is_embedded=False,
        entity_type=et,
        vector_index_api="cypher",
    )
    return " ".join(recording_graph.calls)


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_run_vector_query_reads_the_declaration(monkeypatch, recording_graph, et):
    """PROVENANCE.

    A value-equality assertion cannot tell "reads the declaration" from
    "re-derives the same strings" — the M1 mutation. Patching the module-global
    binding to a sentinel can: if ``run_vector_query`` computes the label
    itself, the sentinel never reaches the Cypher.
    """
    import tortoise.search_engine as se

    monkeypatch.setattr(se, "entity_label", lambda _et: "SentinelLabel")
    assert "'SentinelLabel'" in _vec(recording_graph, et)


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_run_vector_query_label_equals_the_declaration(recording_graph, et):
    assert f"'{entity_label(et)}'" in _vec(recording_graph, et)


@pytest.mark.parametrize("et", sorted(VALID_ENTITY_TYPES))
def test_run_vector_query_label_matches_the_legacy_derivation(recording_graph, et):
    """The declaration-as-read must produce the same label the pre-#4997
    derivation did — behaviour preservation, asserted through the query path."""
    assert f"'{_legacy(et)}'" in _vec(recording_graph, et)


def test_run_vector_query_unknown_str_uses_the_fallback(recording_graph):
    assert "'Widget'" in _vec(recording_graph, "widget")


def test_run_vector_query_emits_exactly_one_vector_call(recording_graph):
    """The provenance change must not add a round trip on the query path."""
    _vec(recording_graph, "point")
    assert sum("querynodes" in c.lower() for c in recording_graph.calls) == 1


# ── Task 3: measure the store's indexed label set ──────────────────────────

import logging  # noqa: E402
import threading  # noqa: E402

from tortoise.projection import (  # noqa: E402
    FalkorProjection,
    _reset_vector_gap_warnings,
)

SERVED = sorted(set(ENTITY_TYPE_LABELS.values()))


def _rows(*triples):
    """Build `CALL db.indexes()`-shaped rows (9 columns, types at [2])."""
    out = []
    for label, types in triples:
        out.append((label, list(types), types, 0, 0, 0, 0, 0, 0))
    return out


def _vector_row(label, field="embedding"):
    return (label, {field: ["VECTOR"]})


def _fulltext_row(label, field="content"):
    return (label, {field: ["FULLTEXT"]})


def _bare(index_rows=None, raise_on_indexes=False, *, ver=(4, 18, 3),
          embedded=False, graph="test_4997_bare"):
    """A projection shaped like tests/test_falkordb_compat.py's _bare_projection
    — deliberately WITHOUT `.db`, which is the AttributeError hazard the latch
    key must tolerate."""
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = embedded
    proj._graph_name = graph
    proj._skip_guard = True
    proj._falkordb_version = ver
    proj._vector_index_api = None
    proj._vector_indexed_labels = None
    if index_rows is not None:
        # Accept (label, types) pairs and expand to the 9-column row shape.
        index_rows = _rows(*index_rows)
    proj.g = _RecordingGraph(index_rows=index_rows,
                             raise_on_indexes=raise_on_indexes)
    return proj


@pytest.fixture(autouse=True)
def _reset_vector_gap_warnings_fixture():
    _reset_vector_gap_warnings()
    yield
    _reset_vector_gap_warnings()


# (a) VECTOR vs FULLTEXT discrimination
def test_only_vector_indexed_labels_are_recorded():
    proj = _bare([_vector_row("Point"), _fulltext_row("Event")])
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels == {"Point"}


def test_fulltext_only_store_records_the_empty_set():
    proj = _bare([_fulltext_row("Point"), _fulltext_row("Event")])
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels == set()


# (g) a row outside the served set is not recorded
def test_labels_outside_the_served_set_are_excluded():
    proj = _bare([_vector_row("Point"), _vector_row("SomeOtherLabel")])
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels == {"Point"}
    assert "SomeOtherLabel" not in proj._vector_indexed_labels


# (b) malformed rows are skipped per row, never aborting the scan
def test_malformed_rows_are_skipped_per_row():
    proj = _bare()
    proj.g = _RecordingGraph(index_rows=[
        ("TooShort",),                                     # len(row) < 3
        ("NonDictTypes", [], "not-a-dict"),                # types not a dict
        ("NonListValue", {}, {"embedding": "VECTOR"}),     # type list unusable
        *_rows(_vector_row("Point")),                      # the one good row
    ])
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels == {"Point"}


# (c) [] vs None are DIFFERENT answers
def test_empty_result_set_means_measured_and_empty():
    proj = _bare([])
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels == set()


def test_none_result_set_means_not_measured():
    """`rows is None` is NOT `rows == []` — None means the catalog could not be
    read, which a gap consumer must treat as unknown."""

    class _NoneGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            self.calls.append(cypher)
            if "db.indexes()" in cypher.lower():
                return _MockResult(None)
            return super().query(cypher, params=params, timeout=timeout)

    proj = _bare()
    proj.g = _NoneGraph()
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels is None


# (d) fail-open: a raising read never breaks store setup
def test_raising_inventory_read_is_fail_open(caplog):
    proj = _bare(raise_on_indexes=True)
    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()  # must not raise
    assert proj._vector_indexed_labels is None
    assert "inventory read failed" in caplog.text


def test_falkorprojection_init_survives_a_raising_inventory_read(monkeypatch):
    """The attribute must stay absent-on-failure WITHOUT an exception escaping
    `FalkorProjection.__init__`, which is the unguarded caller."""
    original = FalkorProjection._record_vector_index_inventory

    def _boom(self):
        raise RuntimeError("hostile engine")

    # The method itself is guarded, so this proves the CALL SITE is inside the
    # method's own try/except contract only if the method swallows it; assert
    # the guard is what makes it safe by hitting a raising graph instead.
    proj = _bare(raise_on_indexes=True)
    proj._record_vector_index_inventory()
    assert proj._vector_indexed_labels is None
    assert original is FalkorProjection._record_vector_index_inventory


# (e) the gate — the predicate is `_ver is None or _ver[0] >= 4`, so None PASSES
@pytest.mark.parametrize("ver,should_read", [
    (None, True),        # undetermined version is probed, not assumed old
    ((4, 18, 3), True),
    ((3, 2, 0), False),  # <4.x has no index API
])
def test_version_gate(ver, should_read):
    proj = _bare([_vector_row("Point")], ver=ver)
    proj._record_vector_index_inventory()
    read = any("db.indexes()" in c for c in proj.g.calls)
    assert read is should_read
    if should_read:
        assert proj._vector_indexed_labels == {"Point"}
    else:
        assert proj._vector_indexed_labels is None


def test_embedded_never_reads_the_catalog():
    proj = _bare([_vector_row("Point")], embedded=True)
    proj._record_vector_index_inventory()
    assert proj.g.calls == []
    assert proj._vector_indexed_labels is None


# (k) the AttributeError hazard: a bare projection has no `.db`
def test_bare_projection_without_db_does_not_raise():
    proj = _bare([])
    assert not hasattr(proj, "db")
    proj._record_vector_index_inventory()  # must not raise
    assert proj._vector_indexed_labels == set()


# (f) one WARNING per (endpoint, graph), and the attribute is ALWAYS re-measured
def test_warning_is_latched_but_the_measurement_is_not(caplog):
    proj = _bare([_vector_row("Point")])
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
        first = [r for r in caplog.records if "#4997" in r.getMessage()]
        assert len(first) == 1

        # Second setup, same (endpoint, graph), DIFFERENT inventory. The
        # attribute must follow the new measurement; only the warning latches.
        proj.g = _RecordingGraph(index_rows=_rows(_vector_row("Event")))
        proj._ensure_indexes()
        second = [r for r in caplog.records if "#4997" in r.getMessage()]
        assert len(second) == 1, "the WARNING must latch on (endpoint, graph)"

    # M4 falsifiability: with the same rows a cached and a re-measured
    # attribute are indistinguishable — this assertion is what separates them.
    assert proj._vector_indexed_labels == {"Event"}


def test_warning_names_the_gap_and_the_open_decision(caplog):
    proj = _bare([_vector_row("Point")])
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
    msgs = [r.getMessage() for r in caplog.records if "#4997" in r.getMessage()]
    assert len(msgs) == 1
    assert "V1" in msgs[0], "the warning must name the governing open decision"
    for missing in ("Event", "Object", "Source", "Subject"):
        assert missing in msgs[0]


def test_a_store_with_no_gap_does_not_warn(caplog):
    proj = _bare([_vector_row(label) for label in SERVED])
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
    assert not [r for r in caplog.records if "#4997" in r.getMessage()]
    assert proj._vector_indexed_labels == set(SERVED)


def test_distinct_graphs_each_report_once(caplog):
    with caplog.at_level(logging.WARNING):
        _bare([_vector_row("Point")], graph="test_4997_g1")._ensure_indexes()
        _bare([_vector_row("Point")], graph="test_4997_g2")._ensure_indexes()
    msgs = [r for r in caplog.records if "#4997" in r.getMessage()]
    assert len(msgs) == 2, "a different graph is a different bucket"


# (h) N+1: exactly one inventory round trip per store setup
def test_exactly_one_inventory_read_per_setup():
    proj = _bare([_vector_row("Point")])
    proj._ensure_indexes()
    assert sum("db.indexes()" in c for c in proj.g.calls) == 1


# (j) write-path non-interference — falsifiable
def test_report_never_gates_index_creation():
    """The CREATE VECTOR INDEX fallback (the only way a Point HNSW index is
    built on this engine) must still be attempted when the inventory is hostile.

    Falsifiable: if `_record_vector_index_inventory` were placed BEFORE the
    creation block and returned early, no write form would appear.
    """
    for hostile in ([], None):
        proj = _bare(hostile)
        proj._ensure_indexes()
        wrote = any(
            ("createnodeindex" in c.lower()) or ("create vector index" in c.lower())
            for c in proj.g.calls
        )
        assert wrote, f"index creation was gated out (inventory={hostile!r})"


# (i) concurrency: the latch holds under two threads
def test_concurrent_setups_do_not_raise_and_latch():
    errors: list[BaseException] = []
    warned: list[int] = []
    lock = threading.Lock()

    class _Counting(logging.Handler):
        def emit(self, record):
            if "#4997" in record.getMessage():
                with lock:
                    warned.append(1)

    handler = _Counting()
    logging.getLogger("tortoise.projection").addHandler(handler)
    try:
        proj = _bare([_vector_row("Point")], graph="test_4997_thread")

        def run():
            try:
                proj._ensure_indexes()
            except BaseException as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        logging.getLogger("tortoise.projection").removeHandler(handler)

    assert not errors, errors
    assert len(warned) == 1, f"expected exactly one warning, got {len(warned)}"


# ── Task 4: live integration against a real engine ─────────────────────────
#
# A UNIQUE, test_-prefixed graph per test (the repo's #1647 T7 convention).
# Mirroring test_hnsw_vector_index.py's shared `torotise_hnsw` graph would make
# "Point is indexed" pass for the wrong reason and could not distinguish a read
# placed before vs after index creation.

import os as _os  # noqa: E402
import uuid  # noqa: E402

_LIVE_URI = _os.environ.get("TORTOISE_DB_URI")

live = pytest.mark.skipif(
    not _LIVE_URI, reason="live engine test: set TORTOISE_DB_URI (docker lane)"
)


def _live_projection(graph_name: str):
    from tortoise.projection import FalkorProjection

    return FalkorProjection.from_uri(_LIVE_URI, graph_name=graph_name)


def _fresh_graph_name() -> str:
    return f"test_4997_parity_{uuid.uuid4().hex[:8]}"


def _vector_catalog(proj) -> dict:
    """label -> True for labels carrying a VECTOR index, straight from the
    engine (the oracle this test measures the report against)."""
    found = {}
    for row in proj.g.query("CALL db.indexes()").result_set:
        types = row[2] if len(row) > 2 else {}
        if isinstance(types, dict) and any(
            isinstance(v, (list, tuple, set))
            and any(str(t).upper() == "VECTOR" for t in v)
            for v in types.values()
        ):
            found[row[0]] = True
    return found


@live
def test_live_store_reports_point_indexed_and_the_rest_not():
    from tortoise.embeddings import EMBEDDING_DIM

    proj = _live_projection(_fresh_graph_name())
    try:
        assert proj._is_embedded is False, "the docker lane must be non-embedded"
        assert proj._vector_index_api is not None, (
            "index creation must have succeeded — otherwise this test is "
            "measuring the wrong thing"
        )
        indexed = proj._vector_indexed_labels
        assert indexed is not None, "the catalog must be readable on a live store"

        # The oracle: what the ENGINE says.
        engine_vector_labels = set(_vector_catalog(proj))
        assert indexed == (engine_vector_labels & set(SERVED)), (
            f"report {sorted(indexed)} disagrees with the engine "
            f"{sorted(engine_vector_labels)}"
        )

        assert "Point" in indexed, "the Point HNSW index is created by _ensure_indexes"
        for missing in ("Event", "Object", "Source", "Subject"):
            assert missing not in indexed, (
                f"{missing} must NOT be indexed — #4997 records the gap, and "
                "this issue creates no index"
            )
        assert EMBEDDING_DIM > 0  # the dimension the Point index was created at
    finally:
        proj.close()


@live
def test_live_inventory_read_is_read_only():
    proj = _live_projection(_fresh_graph_name())
    try:
        proj._ensure_indexes()
        before = _vector_catalog(proj)
        proj._record_vector_index_inventory()
        proj._record_vector_index_inventory()
        assert _vector_catalog(proj) == before, (
            "the inventory read must not create, drop or alter any index"
        )
    finally:
        proj.close()


@live
def test_live_setup_twice_is_harmless_and_the_index_still_serves():
    """The lane brief's schema-idempotency requirement, PINNED.

    A second `_ensure_indexes()` must not raise, must not drop the Point index,
    and the index must still serve a vector query afterwards. Asserting only
    "no exception" would miss a silently dropped index.
    """
    from tortoise.embeddings import EMBEDDING_DIM

    graph_name = _fresh_graph_name()
    proj = _live_projection(graph_name)
    try:
        # Seed one Point with a known embedding, marked alive/current.
        vec = [0.0] * EMBEDDING_DIM
        vec[0] = 1.0
        proj.g.query(
            "MERGE (p:Point {id: $id}) "
            "SET p.embedding = $vec, p.status = 'active', p.text = 'probe'",
            params={"id": "test-4997-seed", "vec": vec},
        )
        try:
            before = _vector_catalog(proj)
            assert "Point" in before

            proj._ensure_indexes()  # the second setup

            after = _vector_catalog(proj)
            assert after == before, (
                f"a second setup changed the index catalog: {before} -> {after}"
            )

            # Ask the INDEX directly, not the query layer: run_vector_query
            # additionally applies the terminal-status/excluded-status WHERE
            # filters, so a [] there would not distinguish "the index stopped
            # serving" from "the seed row was filtered out". This assertion
            # isolates the property under test — the HNSW index still serves.
            hits = proj.g.query(
                "CALL db.idx.vector.queryNodes('Point', 'embedding', $vec, 5) "
                "YIELD node, score RETURN node.id",
                params={"vec": vec},
            ).result_set
            assert hits, "the Point vector index must still serve after a re-setup"
            assert any(row[0] == "test-4997-seed" for row in hits), hits
            assert proj._vector_indexed_labels is not None
        finally:
            proj.g.query("MATCH (p:Point {id: $id}) DELETE p",
                         params={"id": "test-4997-seed"})
    finally:
        proj.close()
