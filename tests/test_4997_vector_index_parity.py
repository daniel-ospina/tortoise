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


class _NoneCatalogueGraph(_RecordingGraph):
    """A catalogue read that ANSWERS `None` — ``result_set is None``, which is
    NOT the same thing as an empty catalogue (see the attribute's docstring).
    A separate class, not a flag on ``_RecordingGraph``, so a reader cannot
    mistake "measured and empty" for "not measured"."""

    def query(self, cypher, params=None, timeout=None):
        # No `self.calls.append` on the delegating path: `_RecordingGraph.query`
        # records the call itself, and appending on both sides double-counted
        # every non-catalogue query (#11087 review).
        if "db.indexes()" in cypher.lower():
            self.calls.append(cypher)
            return _MockResult(None)
        return super().query(cypher, params=params, timeout=timeout)


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


def test_run_vector_query_rejects_unknown_str(recording_graph):
    """#5404 supersedes #4997's runner-level fallback.

    Before #5404 this runner accepted an unknown ``entity_type`` and the label
    fell back to ``entity_type.capitalize()`` (``"widget"`` -> ``'Widget'``).
    That fallback IS the hole #5404 closes: the value is caller-controlled and
    the label it produced is query structure. ``entity_label`` keeps the
    fallback for a direct caller; the RUNNERS no longer accept it.
    """
    with pytest.raises(ValueError, match="Invalid entity_type"):
        _vec(recording_graph, "widget")


def test_run_vector_query_emits_exactly_one_vector_call(recording_graph):
    """The provenance change must not add a round trip on the query path."""
    _vec(recording_graph, "point")
    assert sum("querynodes" in c.lower() for c in recording_graph.calls) == 1


# ── Task 3: measure the store's indexed label set ──────────────────────────

import logging  # noqa: E402
import threading  # noqa: E402
from types import SimpleNamespace as _SimpleNamespace  # noqa: E402

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


class _ServerClient:
    """A redis-py client in the shape the PRODUCTION server lane has.

    ⛔ No `.host`, `.port` or `._host` of its own: the endpoint lives in
    `connection_pool.connection_kwargs` (measured on the pinned `redis 7.4.1`,
    and the reason `FalkorProjection.__init__` records `_host` on the
    PROJECTION instead of reading it off the client). Until #11328 that also
    made `_falkordb_version_cache_key()` answer `None` here, so every hosted
    per-request projection took `_stable_endpoint()`'s fallback lane; the key
    now resolves from these pool kwargs, so a PRODUCTION-shaped client no
    longer reaches that lane. The lanes remain reachable by the shapes that
    still resolve no endpoint: a no-pool mock, and a client whose endpoint is
    only half-resolved (see
    `test_the_register_identity_never_mixes_sources`, which drives the address
    lane with a pool-carrying client).

    A fake that carried `.port` directly would pin a client that does not
    exist and would stay green through the port-dropping defect (#11311).
    """

    def __init__(self, host, port):
        self.connection_pool = _SimpleNamespace(
            connection_kwargs={"host": host, "port": port})


def _bare(index_rows=None, raise_on_indexes=False, *, ver=(4, 18, 3),
          embedded=False, graph="test_4997_bare", port=None):
    """A projection shaped like tests/test_falkordb_compat.py's _bare_projection
    — deliberately WITHOUT `.db`, which is the AttributeError hazard the latch
    key must tolerate — unless `port` is given, which installs the PRODUCTION
    server client (`_ServerClient`) so the store-identity key is exercised on
    the shape a hosted request actually builds."""
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = embedded
    proj._graph_name = graph
    proj._skip_guard = True
    # The endpoint identity a real server projection carries (`_host`, set by
    # `FalkorProjection.__init__`). Present here so the #11087 pair register's
    # key is the PRODUCTION shape — stable across two projections of one store —
    # instead of falling back to the instance address (#11311 review P1/P2).
    proj._host = "test-host"
    if port is not None:
        proj.db = _SimpleNamespace(
            connection=_ServerClient(proj._host, port))
    proj._falkordb_version = ver
    proj._vector_index_api = None
    proj._vector_indexed_labels = None
    proj._vector_index_served_labels = None
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
    # #11087: the served half is None too, so the pair stays coherent.
    assert proj._vector_index_served_labels is None
    assert "inventory read failed" in caplog.text


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


def test_warning_names_the_gap_and_the_open_question(caplog):
    proj = _bare([_vector_row("Point")])
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
    msgs = [r.getMessage() for r in caplog.records if "#4997" in r.getMessage()]
    assert len(msgs) == 1
    # The warning must name the question that is STILL OPEN. Until V1 was
    # decided (2026-11-07) that question was V1 (where the index lives); it is
    # now §14.3 V3 (whether the other served labels carry one), and V1 appears
    # only as SETTLED. Pinning the bare token `V1` passed on that incidental
    # mention, so the guard no longer asserted anything about the open
    # question it is named for — retargeted at the V3 attribution itself.
    assert "§14.3 V3" in msgs[0], (
        "the warning must name the OPEN question (§14.3 V3); V1 is settled and "
        "must not be presented as governing it"
    )
    for missing in ("Event", "Object", "Source", "Subject"):
        assert missing in msgs[0]


def test_a_store_with_no_gap_does_not_warn(caplog):
    proj = _bare([_vector_row(label) for label in SERVED])
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
    assert not [r for r in caplog.records if "#4997" in r.getMessage()]
    assert proj._vector_indexed_labels == set(SERVED)


# ── #11087 (S4): the contract is reported POSITIVELY ──────────────────────
#
# The pin above is deliberately UNCHANGED (plan §6.1): it captures at
# WARNING, so "no gap ⇒ no warning" stays true and the warning semantics a
# caller may match on are preserved. What #11087 ends is the SILENCE — the
# same store must now report the serves/indexes pair at INFO, and so must a
# store that HAS a gap (which also still warns).

def _pair_records(caplog):
    return [r for r in caplog.records if "#11087" in r.getMessage()]


def test_a_store_with_no_gap_reports_the_serves_indexes_pair(caplog):
    """Plan §7 D5 — today this store says nothing at all.

    Falsifiable: before #11087 the report lived behind `if gap:`, so a
    fully-indexed store emitted no record and `_pair_records` is empty.
    """
    proj = _bare([_vector_row(label) for label in SERVED])
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()

    pairs = _pair_records(caplog)
    assert len(pairs) == 1, (
        "a fully-indexed store must report the pair, exactly once per setup"
    )
    assert pairs[0].levelno == logging.INFO
    # The record's payload is asserted POSITIONALLY on the call's args, not on
    # `repr()` of a format string (plan §7, "Brittleness avoided"): both halves,
    # and the graph it attributes itself to, are read off the record itself.
    graph_seen, serves_seen, indexes_seen = pairs[0].args
    assert graph_seen == proj._graph_name, (
        "a contract line that drops the graph is unusable in a multi-graph store"
    )
    assert serves_seen == SERVED, serves_seen
    assert indexes_seen == SERVED, indexes_seen
    assert not [r for r in caplog.records
                if "#4997" in r.getMessage() and r.levelno >= logging.WARNING]
    assert proj._vector_index_served_labels == set(SERVED)
    assert proj._vector_indexed_labels == set(SERVED)


def test_a_gap_reports_the_pair_and_still_warns(caplog):
    """Plan §7 D6 — the pair is TWO halves: serves is every declared label,
    indexes is the measured subset. A store with a gap gets both records."""
    proj = _bare([_vector_row("Point")])
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()

    pairs = _pair_records(caplog)
    assert len(pairs) == 1
    assert pairs[0].levelno == logging.INFO
    # ⛔ THE ONLY CASE WHERE THE TWO HALVES DIFFER, so the only case where a
    # swapped or duplicated argument is observable at all. A record reading
    # `serves=['Point'] indexes=[all five]` on a store whose own warning says
    # `(indexed: ['Point'])` tells an operator the OPPOSITE of the truth, and
    # every attribute-level assertion still passes. Hence: the record's args.
    _graph_seen, serves_seen, indexes_seen = pairs[0].args
    assert serves_seen == SERVED, serves_seen  # every declared label
    assert indexes_seen == ["Point"], indexes_seen  # the measured subset
    assert proj._vector_indexed_labels == {"Point"}
    warnings = [r for r in caplog.records
                if "#4997" in r.getMessage() and r.levelno >= logging.WARNING]
    assert len(warnings) == 1, "the gap must still WARN — #11087 adds a report only"


def test_the_reported_served_set_is_the_one_declaration(monkeypatch, caplog):
    """Plan §7 D8 — one label declaration, no third vocabulary.

    The reported served set must BE the `ENTITY_TYPE_LABELS` binding that
    `tortoise.projection` imports from `security`, not a copy re-derived from
    anything else. Patching that binding proves provenance the way
    `test_run_vector_query_reads_the_declaration` does: a re-derived literal
    would ignore the sentinel. `monkeypatch` (not a hand-rolled try/finally) so
    the binding is restored even if the test fails mid-way (#11087 review).
    """
    import tortoise.projection as projection_mod
    from tortoise.security import ENTITY_TYPE_LABELS as declared

    # The binding under test must BE the declaration's: patching it proves
    # provenance only if the module reads that one name, so assert the identity
    # first — a re-declaration inside `tortoise.projection` would satisfy the
    # sentinel while having quietly created the second vocabulary D8 forbids.
    assert projection_mod.ENTITY_TYPE_LABELS is declared, (
        "`tortoise.projection` must import the ONE declaration, not copy it"
    )

    monkeypatch.setattr(projection_mod, "ENTITY_TYPE_LABELS",
                        {"widget": "WidgetLabel"})
    proj = _bare([_vector_row("WidgetLabel")])
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()
    assert proj._vector_index_served_labels == {"WidgetLabel"}, (
        "the served set must be READ from the declaration, not re-derived"
    )
    # Provenance is a claim about what was EMITTED, so this stays on the record —
    # the sentinel reaches it only through the declaration — but it names the
    # label rather than pinning `repr()` (plan §7).
    assert "WidgetLabel" in _pair_records(caplog)[0].getMessage()
    assert proj._vector_indexed_labels == {"WidgetLabel"}


def test_unmeasured_pair_is_none_in_both_halves(caplog):
    """#11087: the pair is never half-populated — `None` means NOT MEASURED
    for both halves, so a caller cannot read `served is None` + `indexed ==
    set()` as "serves nothing, indexes nothing". (Plan §7 D7's fail-open lane.)"""
    proj = _bare(raise_on_indexes=True)
    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()  # must not raise
        assert proj._vector_indexed_labels is None
        assert proj._vector_index_served_labels is None
        assert not _pair_records(caplog), (
            "a FAILED read must not be reported as a pair"
        )
        # ⛔ The lane must have RUN. On a VIRGIN projection (`_bare()` leaves
        # both halves None) "is None afterwards" is ALSO satisfied by a read
        # that never entered — so without this the test passes even on the
        # pre-#11087 module and is no guard at all. The branch's own evidence
        # is what makes it able to fail (same shape as
        # `test_raising_inventory_read_is_fail_open`).
        assert "inventory read failed" in caplog.text

    proj2 = _bare()
    proj2.g = _NoneCatalogueGraph()
    # ⛔ The capture must WRAP the call and the negative assertion must follow
    # it: the not-measured lane is exactly where a regression would report
    # `serves=[] indexes=[]` (the misreading warned of above), and an assertion
    # taken before the call cannot see that record at all. DEBUG (not INFO) so
    # the branch's own evidence below is captured too.
    with caplog.at_level(logging.DEBUG):
        proj2._ensure_indexes()
        assert proj2._vector_indexed_labels is None
        assert proj2._vector_index_served_labels is None
        assert not _pair_records(caplog), (
            "an unmeasured pair must not be reported"
        )
        assert "inventory unavailable (empty response)" in caplog.text, (
            "this must be the answered-None lane, not a read that never ran"
        )


def test_a_re_read_that_cannot_measure_clears_an_already_populated_pair(caplog):
    """#11087: the two `= None` resets are LOAD-BEARING — only this test sees a
    missing one.

    Every negative above starts from a VIRGIN projection: `_bare()` leaves both
    halves `None`, so "is None afterwards" there cannot distinguish "the failed
    read CLEARED the pair" from "the pair was never populated". That is a
    false-PASS shape — a refactor could drop either reset and keep the suite
    green while the pair ships half-populated.

    The re-entry is real, not hypothetical: `rebuild_from_record` runs
    `_ensure_indexes()` on an ALREADY-measured projection
    (``tortoise/projection/__init__.py``, the `rebuild_from_record` call site),
    so a store that measured once and then meets an unreadable catalogue would
    otherwise keep `served = all five labels` beside `indexed = None` — which a
    caller reads as "5 labels served, 0 indexed" instead of "unknown".
    """
    proj = _bare([_vector_row(label) for label in SERVED])
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()
    assert proj._vector_index_served_labels == set(SERVED)
    assert proj._vector_indexed_labels == set(SERVED)

    # Second read RAISES — the `except` lane must clear BOTH halves.
    proj.g = _RecordingGraph(raise_on_indexes=True)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory()  # must not raise
        assert proj._vector_index_served_labels is None, (
            "a failed re-read must CLEAR the served half, never keep the old set"
        )
        assert proj._vector_indexed_labels is None
        assert not _pair_records(caplog), (
            "a failed re-read must not report the pair"
        )

    # Re-populate, then answer `None` — the `rows is None` lane must clear both.
    # ⛔ The rows must be the 9-COLUMN shape `_bare()` builds: a bare
    # `_vector_row` is a 2-tuple, which the scan skips at `len(row) < 3`, so a
    # leg built from those re-populates the SERVED half only, leaves
    # `_vector_indexed_labels == set()`, and reports a gap warning it never
    # asserts. That is not "an already-populated pair" and the comment below
    # would be untrue.
    proj.g = _RecordingGraph(_rows(*[_vector_row(label) for label in SERVED]))
    caplog.clear()
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory()
    assert proj._vector_index_served_labels == set(SERVED)
    assert proj._vector_indexed_labels == set(SERVED), (
        "the re-populated pair must be whole — served AND indexed"
    )
    assert len(_pair_records(caplog)) == 1, "the re-populated pair is reported"
    proj.g = _NoneCatalogueGraph()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory()
        assert proj._vector_index_served_labels is None, (
            "an answered-None catalogue must CLEAR the served half too"
        )
        assert proj._vector_indexed_labels is None
        assert not _pair_records(caplog)

    # ⛔ …and the same lane must DROP the pair-register entry, not only the
    # attributes (#11311 review P3). The answered-`None` pop had no test: only
    # the RAISING lane's pop was pinned above, so deleting this one kept the
    # suite green. Drive measured(A) → answered-None → measured(A): the third
    # read is news again, because an unmeasured store has no contract to have
    # already reported. Without the pop the pair VALUE is unchanged, the
    # register still holds it, and nothing is emitted.
    proj.g = _RecordingGraph(_rows(*[_vector_row(label) for label in SERVED]))
    caplog.clear()
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory()
    assert len(_pair_records(caplog)) == 1, (
        "a re-measure after an answered-None read must REPORT again — the "
        "register entry has to be dropped on that lane, not just on the raising one"
    )


def test_a_wholly_unparseable_catalogue_is_reported_as_measured_and_empty(caplog):
    """#11087: the MALFORMED-ROW lane is **not** the NOT-MEASURED lane.

    Pre-existing semantics (#4997, pinned by
    `test_malformed_rows_are_skipped_per_row`): rows are parsed per row and an
    unparseable row is skipped. A catalogue that is RETURNED but parses to
    nothing therefore yields `indexes=[]` — "measured and empty" — and the gap
    warning fires; it does NOT yield `None` ("unknown").

    This is pinned here because #11087 makes that lane SAY so out loud (the
    positive report), and because an earlier draft of this PR claimed the
    opposite — that a malformed catalogue leaves BOTH halves `None`. It does
    not, and the difference is user-visible: `indexes=[]` tells a caller
    "nothing is indexed" when the store merely could not parse the answer.
    Separating the two is a behaviour change to the pinned half and is
    deliberately out of #11087's scope; the honest thing it owes is this pin.
    """
    proj = _bare(graph="test_11087_malformed")
    proj.g = _RecordingGraph(index_rows=[
        ("TooShort",),                                 # len(row) < 3
        ("NonDictTypes", [], "not-a-dict"),            # types not a dict
    ])
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory()

    pairs = _pair_records(caplog)
    assert len(pairs) == 1, (
        "a returned-but-unparseable catalogue is still a MEASURED lane"
    )
    assert pairs[0].levelno == logging.INFO
    # The CONTENT is read POSITIONALLY off `record.args` — structured content,
    # not `repr()` of a list nor the format string's wording (the sibling tests'
    # convention, plan §7 "Brittleness avoided").
    _graph_seen, serves_seen, indexes_seen = pairs[0].args
    assert serves_seen == SERVED, serves_seen
    assert indexes_seen == [], (
        "documented consequence: unparseable rows read as 'nothing indexed'"
    )
    assert proj._vector_index_served_labels == set(SERVED)
    assert proj._vector_indexed_labels == set(), (
        "documented consequence: unparseable rows read as 'nothing indexed'"
    )
    assert [r for r in caplog.records
            if "#4997" in r.getMessage() and r.levelno >= logging.WARNING], (
        "the gap warning must still fire on this lane"
    )


def test_a_changed_pair_is_reported_and_a_repaired_store_announces_it(caplog):
    """#11087: the report is latched on the pair VALUE, so a CHANGE is always news.

    Deduplication is the obvious "dedupe this log spam" refactor (setup runs
    from `__init__` and from the hosted per-request construction path), and it is
    correct for a REPEATED pair — see `test_an_unchanged_pair_is_not_reported_twice`.
    What it must never do is delete the property the report exists for: a store
    that GAPPED once and was then repaired could never announce the repair. So
    the latch keys on WHAT was reported, not on whether the graph was reported:
    two successive measurements on ONE `(endpoint, graph)` whose pairs DIFFER
    must both report, and the second must name the repaired pair while the
    #4997 WARNING stays latched on the key.

    Falsifiable: with a key-only latch (the `#4997` shape, or a dedicated key),
    `len(pairs)` is 1 and this fails.
    """
    proj = _bare([_vector_row("Point")])          # a gapped store
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()                    # read 1: pair + WARNING
        proj.g = _RecordingGraph(
            _rows(*[_vector_row(label) for label in SERVED])
        )                                         # repaired: every label indexed
        proj._record_vector_index_inventory()     # read 2: pair, no warning

    pairs = _pair_records(caplog)
    assert len(pairs) == 2, (
        "a CHANGED pair must report again — a key-only latch would hide a "
        f"repaired store forever (got {len(pairs)} record(s))"
    )
    assert pairs[1].levelno == logging.INFO
    assert proj._graph_name in pairs[1].getMessage(), (
        "each report must name the graph it describes"
    )
    assert proj._vector_index_served_labels == set(SERVED)
    assert proj._vector_indexed_labels == set(SERVED), (
        "the second report must describe the REPAIRED pair, not the old gap"
    )
    warnings = [r for r in caplog.records
                if "#4997" in r.getMessage() and r.levelno >= logging.WARNING]
    assert len(warnings) == 1, (
        "the WARNING stays latched on the key — #11087 dedups the REPORT on the "
        "pair VALUE instead"
    )


def test_an_unchanged_pair_is_not_reported_twice(caplog):
    """#11087: the INFO is deduplicated on the PAIR VALUE.

    `_ensure_indexes` runs on the hosted PER-REQUEST path, so a line per call
    would put two label lists on the INFO stream at full request rate — the same
    reason the `#4997` WARNING above is latched. But the dedup must key on what
    was REPORTED, not on whether this graph was reported at all: the sibling
    test above pins the other half (a repaired store must announce it), and a
    key-only latch fails there.

    Falsifiable both ways: with no latch at all `len(pairs)` is 2 here; with a
    key-only latch the repaired-store test is the one that reds.
    """
    proj = _bare([_vector_row("Point")], graph="test_11087_pair_dedup")
    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()
        proj._record_vector_index_inventory()   # same catalogue, same pair

    pairs = _pair_records(caplog)
    assert len(pairs) == 1, (
        f"an unchanged pair must be reported once, not per setup (got {len(pairs)})"
    )


def test_a_second_projection_of_the_same_store_does_not_repeat_the_pair(caplog):
    """#11087/#11311: the register's key is STABLE across projections.

    This is the property the register exists for and the one a same-instance
    test cannot see: the hosted path builds a NEW projection per request, so
    "an unchanged pair reports once" is a claim about two DIFFERENT objects for
    one (endpoint, graph).

    ⛔ Falsifiable only because `_bare()` carries a `_host` and (with `port=`) a
    pool-backed server client: keyed on `id(self)` — which is what a bare
    `_falkordb_version_cache_key() or id(self)` yields for a mock with no
    endpoint identity — the two projections get two keys and this records 2
    (measured on #11311's first cut). `port=` puts the two projections on the
    PRODUCTION client shape, where the key now resolves from the pool kwargs
    (`_falkordb_version_cache_key`, #11328); the fallback lanes remain reachable
    by a client that resolves no endpoint — a no-pool mock, or one that resolves
    only half of it.
    """
    rows_for_both = [_vector_row("Point")]
    first = _bare(rows_for_both, graph="test_11087_pair_stable", port=6379)
    with caplog.at_level(logging.INFO):
        first._ensure_indexes()
        second = _bare(rows_for_both, graph="test_11087_pair_stable", port=6379)
        second._ensure_indexes()      # a per-request rebuild, same catalogue

    pairs = _pair_records(caplog)
    assert len(pairs) == 1, (
        "one store, one contract: a rebuild must not re-report the same pair "
        f"(got {len(pairs)} record(s) from two projections)"
    )


def test_the_register_identity_never_mixes_sources():
    """Round-2 review P3: `_stable_endpoint()` must never build its two fields
    from two different sources.

    A client that resolves only PART of an endpoint (a port in the pool, a host
    on the projection) used to fall through to `(projection._host, pool port)` —
    a tuple that matches NEITHER source and can equal a real endpoint of a
    DIFFERENT store, so two stores would share one `#4997` warning latch and one
    `#11087` pair register: one store's WARNING and report are suppressed and its
    gap goes unreported. Reachable only through a mis-built client (redis-py
    resolves both fields or neither), but silent when it happens.

    ⛔ Falsifiable: the assertion below is the measured mixed tuple
    `('server', 'conn-only-host', 6399)` under the old fallback.
    """
    proj = _bare()
    proj._host = "conn-only-host"
    proj.db = _SimpleNamespace(connection=_SimpleNamespace(
        connection_pool=_SimpleNamespace(
            connection_kwargs={"port": 6399})))   # port, no host
    endpoint = proj._stable_endpoint()
    assert endpoint == ("instance", id(proj)), (
        "a half-resolved client must have NO endpoint identity, never a mixed "
        f"tuple that can match another store (got {endpoint!r})"
    )


def test_an_unparseable_port_keeps_the_stable_bucket():
    """Round-3 review P3: an unparseable port is NO PORT, not \"a port is
    present\".

    Gating the host-only lane on `raw_port is None` dropped it for a port that
    exists but cannot be read, sending two projections of ONE store to
    `("instance", id(self))` — one address-keyed register entry per projection
    object, never evicted (the leak the pair register's own comment names) — for
    no safety gain: a `('server', host, None)` bucket can never equal a real
    `('server', host, port)` endpoint, so it cannot collide with a store whose
    endpoint RESOLVED. (Round-4 review P3: the earlier wording said "cannot
    collide with a different store", which is false — two DIFFERENT stores on one
    host whose ports are both unreadable do share this bucket, and their `#4997`
    latch and `#11087` register merge. That is the accepted degraded-but-stable
    identity this lane and its parent both take for the portless shape, not a
    claim that the bucket is unique.) Both projections must share the bucket.
    """
    def _proj_with_pool(raw_port):
        proj = _bare()
        proj._host = "h"
        proj.db = _SimpleNamespace(connection=_SimpleNamespace(
            connection_pool=_SimpleNamespace(
                connection_kwargs={"host": "h", "port": raw_port})))
        return proj

    first, second = _proj_with_pool("nope"), _proj_with_pool("nope")
    assert first._stable_endpoint() == second._stable_endpoint() == (
        "server", "h", None), (
        "an unreadable port must leave the STABLE host-only bucket intact, not "
        f"scatter to per-object addresses (got {first._stable_endpoint()!r} / "
        f"{second._stable_endpoint()!r})"
    )


def test_two_ports_on_one_host_are_two_stores(caplog):
    """#11311: the store identity carries the PORT, not just the host.

    This repo's own environment runs two FalkorDB servers on loopback —
    `localhost:6379` (the test matrix) and `localhost:16379` (canonical/dev) —
    both on the default graph name `tortoise`, which is exactly why
    `tortoise/event_store.py::_store_identity` records that *"the PORT is part
    of the identity, not decoration"*. The endpoint is not on the projection and
    not on the redis client: it is
    `connection_pool.connection_kwargs['port']`.

    ⛔ Falsifiable: keyed on the host alone — the shape this PR shipped at
    `b8f74e4aff`, where the fallback read `connection.port` off a client that
    has none, degrading to `("host", host, "None")` — both projections land in
    ONE bucket. The second store's report is then suppressed AND its gap
    WARNING is latched away, so a full-scan gap on a genuinely different store
    goes unreported: 1 record and 1 warning instead of 2 and 2.
    """
    rows = [_vector_row("Point")]            # a GAPPED store, so both must warn
    first = _bare(rows, graph="tortoise", port=6379)
    second = _bare(rows, graph="tortoise", port=16379)
    assert first._stable_endpoint() != second._stable_endpoint(), (
        "two ports on one host are two stores"
    )
    with caplog.at_level(logging.INFO):
        first._ensure_indexes()
        second._ensure_indexes()

    pairs = _pair_records(caplog)
    assert len(pairs) == 2, (
        "each store reports its own contract; a host-only key collapses them "
        f"(got {len(pairs)} record(s))"
    )
    warnings = [r for r in caplog.records
                if "#4997" in r.getMessage() and r.levelno >= logging.WARNING]
    assert len(warnings) == 2, (
        "a full-scan gap on a DIFFERENT store must not be latched away "
        f"(got {len(warnings)} warning(s))"
    )


def test_the_reused_catalogue_fast_path_still_reports_the_pair(caplog):
    """#11087: the report is not a full-sweep-only event.

    `_ensure_indexes` passes the rows `_schema_is_current` already read on the
    steady-state repeat sweep (#5434) — the path where the write path took the
    "already indexed" branch. Gating the report on `catalogue_rows is None`
    would silence exactly the repeat setup an operator watches, and nothing else
    in the file reaches this call with rows supplied.
    """
    proj = _bare([_vector_row("Point")])
    rows = _rows(*[_vector_row(label) for label in SERVED])
    with caplog.at_level(logging.INFO):
        proj._record_vector_index_inventory(rows)

    pairs = _pair_records(caplog)
    assert len(pairs) == 1, "a reused-rows read must still report the pair"
    _graph_seen, serves_seen, indexes_seen = pairs[0].args
    assert serves_seen == SERVED, serves_seen
    assert indexes_seen == SERVED, indexes_seen
    assert proj.g.calls == [], (
        "the supplied rows must be REUSED — no second catalogue read (#5434)"
    )


def test_an_unorderable_served_set_fails_open_instead_of_raising(caplog):
    """#11087: the report's `sorted()` must live INSIDE the fail-open guard.

    `sorted()` runs the labels' own ordering, and `measured & served` does not
    sanitise a label's type, so a label without a total order reached the
    report and made it raise OUT of a method whose contract is "never raises" —
    `_ensure_indexes` is reached unguarded from `FalkorProjection.__init__`, so
    that breaks store construction. Sorts now happen inside the `try`; this pins
    that they stay there.

    ⛔ TWO labels, not one: `sorted()` on a ONE-element sequence never calls
    `__lt__`, so a single-label probe passes on the unfixed code and proves
    nothing (measured — that is how the first version of this check lied).
    """
    import tortoise.projection as projection_mod

    original = projection_mod.ENTITY_TYPE_LABELS
    try:
        projection_mod.ENTITY_TYPE_LABELS = {"a": object(), "b": object()}
        proj = _bare()
        with caplog.at_level(logging.DEBUG):
            proj._ensure_indexes()  # must not raise
            assert proj._vector_index_served_labels is None
            assert proj._vector_indexed_labels is None
            assert not _pair_records(caplog), (
                "a report that cannot be built must not be emitted"
            )
            assert "inventory read failed" in caplog.text, (
                "the lane must be the fail-open one, not a silent skip"
            )
    finally:
        projection_mod.ENTITY_TYPE_LABELS = original


def test_distinct_graphs_each_report_once(caplog):
    with caplog.at_level(logging.WARNING):
        _bare([_vector_row("Point")], graph="test_4997_g1")._ensure_indexes()
        _bare([_vector_row("Point")], graph="test_4997_g2")._ensure_indexes()
    msgs = [r for r in caplog.records if "#4997" in r.getMessage()]
    assert len(msgs) == 2, "a different graph is a different bucket"


# (h) N+1: exactly one INVENTORY round trip per store setup
#
# #4465 (landed after this test was written) gave `_ensure_indexes` a
# `_schema_is_current()` fast-path probe that ALSO issues `CALL db.indexes()`,
# so a raw call count is now 2. Disable that probe — monkeypatching the fast
# path to False — rather than loosening the count, so this still pins the
# property it owns: the #4997 inventory read adds exactly ONE round trip.
def test_exactly_one_inventory_read_per_setup(monkeypatch):
    proj = _bare([_vector_row("Point")])
    monkeypatch.setattr(proj, "_schema_is_current", lambda: False)
    proj._ensure_indexes()
    assert sum("db.indexes()" in c for c in proj.g.calls) == 1


# (h2) the #4465 fast path must ALSO measure — the steady-state path
#
# A store whose schema is already current returns from `_ensure_indexes` after
# the fast-path probe, never reaching the full sweep. Before #4465 that sweep
# was the only place the vector-index block ran, so pinning the measurement to
# it left `_vector_indexed_labels` at None on every already-indexed graph.
# This test fails (attribute None) if the measurement is not reached on the
# fast path.
def test_fast_path_still_measures_the_catalog(caplog):
    proj = _bare([_vector_row("Point")])
    proj._schema_is_current = lambda: True  # the steady state
    with caplog.at_level(logging.WARNING):
        proj._ensure_indexes()
    assert proj._vector_indexed_labels == {"Point"}, (
        "#4465's fast path returned before the inventory was measured"
    )
    assert [r for r in caplog.records if "#4997" in r.getMessage()], (
        "the gap must still be reported on the fast path"
    )


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
            except BaseException as e:
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
            "SET p.embedding = vecf32($vec), p.status = 'active', "
            "p.text = 'probe'",
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
                "CALL db.idx.vector.queryNodes('Point', 'embedding', 5, "
                "vecf32($vec)) YIELD node, score RETURN node.id",
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
