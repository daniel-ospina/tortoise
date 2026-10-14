"""#6976 — an id predicate must stay BOUND across a query expansion.

FalkorDB 6.0.0 drops a predicate of the form `MATCH (v:Point) WHERE <pred on v>`
— including `n.id IN $ids`, the `=` spelling, and AND-fragments — when a LATER
**plain** `MATCH` at the same query level **re-binds** `v` as a pattern endpoint.
The query does not return zero rows; it returns **other nodes' rows**. A caller
that asked to *narrow* to a set of ids therefore receives arbitrary graph rows:
a filter **bypass**, not an empty read.

The pinned test engine (4.20.4) BINDS the shape, so a behavioural test cannot
reproduce the defect here. What this test does pin is the shape of the Cypher the
builders emit: a `WITH <var>` must separate the id predicate from the expanding
`MATCH`, and it must come BEFORE that MATCH — a barrier placed after the
re-binding MATCH leaves the predicate dropped.

Each assertion is discriminating — deleting the barrier at a site makes the
matching test fail (verified by mutation).

## Why there is deliberately NO whole-tree scanner here

A class-level scan (fold every Cypher string under `tortoise/`, assert zero
offenders) was attempted and **measurably failed in both directions**, so it was
removed rather than shipped:

* **Under-inclusive** — Cypher held in `return (...)`, containers or keyword args
  is unreachable, so 7 real `MATCH`-bearing strings were never scanned (every
  string in `tortoise/navigation.py`). The rail would have reported "0 offenders"
  over strings it never read, which is exactly how the previous guard (#7050)
  reported clean over a tree holding the live bug.
* **False-positive** — `bound`/`constrained` must reset at a `UNION` boundary;
  without that, the benign two-branch `UNION` query already in
  `tortoise/navigation.py::_hop_query` is reported as an offender. A guard that
  reddens honest, unrelated PRs is a liability, and a `WITH` placed to silence it
  would be cargo.

The durable fix is NOT another source-shape rail — it is a normaliser/refusal at
the client seam plus an engine-version probe, which makes the shape
impossible-by-construction (tracked with the full evidence in tortoise#7162).

## #7162 LANDED — the seam, and why the scanner above is now unnecessary

``tortoise.cypher_guard._predicate_drop_barrier`` inserts a ``WITH *`` before a
plain ``MATCH`` that re-binds a variable a preceding ``WHERE`` constrains, and
``_predicate_barrier_required`` gates that on the SERVER's own version
(``MODULE LIST`` -> the ``graph`` module's ``ver``), decided once per client.
The tests below pin the normaliser, the gate, and the handle-level dispatch.

Two corrections to the shape's first write-up, both MEASURED (2026-10-13):

* the barrier is ``WITH *``, NOT ``WITH <var>``. ``WITH <var>`` DROPS a variable
  bound before the predicate and still referenced later — measured on BOTH 4.20.4
  and 6.0.1, a legitimate query goes from rows to ``'a' not defined``. A guard
  that breaks an honest query is worse than the bug, so ``WITH *`` (which passes
  every in-scope name) is what ships.
* nested ``CALL { … }`` is MEASURED UNAFFECTED (same discriminating topology as
  the defect: correct rows, not foreign ones), so the normaliser deliberately
  does not descend into a new brace scope.
"""
from __future__ import annotations

import re
from typing import ClassVar

import pytest

from tortoise.audit import audit_graph
from tortoise.cypher_guard import (
    _engine_module_version,
    _engine_needs_predicate_barrier,
    _graph_module_version,
    _guard_query,
    _predicate_drop_barrier,
    guarded_client,
)
from tortoise.search_engine import (
    filter_by_relationship,
    filter_by_traversal_predicate,
)


class _Result:
    result_set: ClassVar[tuple] = ()


class _StubGraph:
    """Captures the Cypher emitted by a builder; returns an empty result set."""

    def __init__(self) -> None:
        self.cypher: list[str] = []

    def query(self, cypher, params=None):
        self.cypher.append(cypher)
        return _Result()


class _StubProj:
    def __init__(self) -> None:
        self.g = _StubGraph()


def _assert_barrier(cypher: str, var: str) -> None:
    """`WITH <var>` must sit between the id predicate and the re-binding MATCH.

    Both halves matter. Presence alone is not enough: a barrier placed AFTER the
    re-binding MATCH leaves the predicate dropped, so this also pins the ORDER.
    """
    where = cypher.find("WHERE")
    assert where != -1, f"no WHERE in the emitted query: {cypher!r}"

    # The re-binding MATCH — `MATCH (v)` or `MATCH path=(v)`, at any level after
    # the predicate. A later `OPTIONAL MATCH` is NOT the defect shape.
    rebind = re.search(rf"\bMATCH\s+(?:path\s*=\s*)?\(\s*{re.escape(var)}\b", cypher[where:])
    assert rebind is not None, (
        f"#6976: no plain MATCH re-binds `{var}` after the predicate, so this "
        f"assertion would be vacuous for this query: {cypher!r}"
    )
    rebind_at = where + rebind.start()

    with_at = cypher.find(f"WITH {var}", where)
    assert with_at != -1, (
        f"#6976: the id predicate is UNBOUND — no `WITH {var}` after WHERE, so on "
        f"FalkorDB 6.0.0 the predicate is dropped and FOREIGN rows are returned: {cypher!r}"
    )
    assert with_at < rebind_at, (
        f"#6976: `WITH {var}` appears at {with_at} but the re-binding MATCH is at "
        f"{rebind_at} — a barrier placed AFTER the re-binding MATCH does not bind the "
        f"predicate: {cypher!r}"
    )


def test_filter_by_relationship_keeps_the_id_predicate_bound() -> None:
    graph = _StubGraph()
    filter_by_relationship(graph, ["p1"], "causes", "t1")
    assert graph.cypher, "the builder emitted no query"
    _assert_barrier(graph.cypher[0], "n")


def test_filter_by_traversal_predicate_keeps_the_id_predicate_bound() -> None:
    graph = _StubGraph()
    filter_by_traversal_predicate(graph, ["p1"], "causes")
    assert graph.cypher, "the builder emitted no query"
    _assert_barrier(graph.cypher[0], "n")


def test_audit_superseded_active_edges_keeps_its_predicate_bound() -> None:
    proj = _StubProj()
    audit_graph(proj)
    sup_queries = [q for q in proj.g.cypher if "sup" in q and "(sup:Point)" in q]
    assert sup_queries, "the superseded_active_edges check emitted no query"
    for q in sup_queries:
        _assert_barrier(q, "sup")


# ── #7162: the handle-level seam ─────────────────────────────────────────────
#
# Every test below is DISCRIMINATING: deleting the normaliser call (or the
# gate, or the dispatch of the rewritten statement) makes it fail. They are the
# runtime equivalent of the whole-tree scan the class cannot support — a new
# offender is barred on the way to the engine whatever builder emitted it.

#: The defect shape: `n` is filtered by the WHERE and then re-bound as a pattern
#: endpoint by a PLAIN `MATCH`.
_OFFENDER = "MATCH (n:Point) WHERE n.id IN $ids MATCH (n)-[:REL]->(m:Point) RETURN n.id"


def test_the_offender_shape_gains_a_barrier_before_the_rebinding_match() -> None:
    out = _predicate_drop_barrier(_OFFENDER)
    assert out != _OFFENDER, "the #6976 shape was not normalised"
    assert out.count("WITH *") == 1, out
    assert out.index("WITH *") < out.index("MATCH (n)-[:REL]"), out
    assert _OFFENDER.replace("MATCH (n)-[:REL]", "WITH * MATCH (n)-[:REL]") == out


def test_the_barrier_is_with_star_not_the_variable() -> None:
    """MEASURED: `WITH <var>` DROPS a variable bound before the predicate.

    On both 4.20.4 and 6.0.1, taking a legitimate two-variable query and
    inserting `WITH n` turns it into `'a' not defined`. `WITH *` passes every
    in-scope name through, so it cannot break such a query. Value that makes
    this fail: emitting `WITH n` (or `WITH n, m`) instead of `WITH *`.
    """
    multi = (
        "MATCH (a:Point {id:'z1'}) MATCH (n:Point) WHERE n.id IN $ids "
        "MATCH (n)-[:REL]->(m:Point) RETURN a.id, n.id"
    )
    out = _predicate_drop_barrier(multi)
    assert out != multi
    assert "WITH *" in out, out
    assert re.search(r"\bWITH\s+(?!\*)\w", out) is None, out


def test_the_barrier_is_idempotent() -> None:
    once = _predicate_drop_barrier(_OFFENDER)
    assert _predicate_drop_barrier(once) == once, "a second pass stacked a barrier"


@pytest.mark.parametrize("statement", [
    # already barred by #7155 — a `WITH` (not a plain MATCH) follows the WHERE
    "MATCH (n:Point) WHERE n.id IN $ids WITH n MATCH (n)-[:REL]->(m) RETURN n.id",
    # OPTIONAL MATCH re-binds the same var: MEASURED UNAFFECTED
    "MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[:REL]->(m) RETURN n.id",
    # a DIFFERENT variable is re-bound: nothing the WHERE constrains changes
    "MATCH (n:Point) WHERE n.id IN $ids MATCH (m:Point)-[:REL]->(z) RETURN n.id",
    # nested scope: `CALL { … }` is MEASURED UNAFFECTED, and has its own scope
    "MATCH (n:Point) WHERE n.id IN $ids CALL { MATCH (n)-[:REL]->(m) RETURN m } "
    "RETURN n.id",
    # no WHERE at all
    "MATCH (n:Point) MATCH (n)-[:REL]->(m) RETURN n.id",
    # a predicate on a DIFFERENT name than the one re-bound
    "MATCH (n:Point) WHERE n.kind = 'x' MATCH (m:Point)-[:REL]->(o:Point) RETURN n.id",
])
def test_legitimate_statements_are_left_byte_identical(statement: str) -> None:
    """The false-positive rail: a query with no defect shape is NOT rewritten."""
    assert _predicate_drop_barrier(statement) == statement


def test_the_shape_inside_a_quoted_literal_is_data_not_syntax() -> None:
    statement = (
        "MATCH (n:Point) WHERE n.name = 'MATCH (n) MATCH (n)' "
        "MATCH (n)-[:R]->(m) RETURN n"
    )
    out = _predicate_drop_barrier(statement)
    # n IS re-bound by the real second MATCH, so a barrier belongs — but it must
    # sit before the REAL one, never inside the literal.
    assert out.count("WITH *") == 1, out
    assert "'MATCH (n) MATCH (n)'" in out, "the literal was rewritten"
    assert out.index("WITH *") > out.index("'MATCH (n) MATCH (n)'")


def test_the_benign_union_query_the_source_scanner_false_positived_on() -> None:
    """#7050's FALSE POSITIVE, pinned: `bound` was not reset at a UNION.

    ``navigation._hop_query`` emits a two-branch UNION whose branches each end
    in ``RETURN`` before the next ``UNION`` — no plain ``MATCH`` re-binds the
    filtered name, so no barrier is owed. The source-shape scanner reported it
    as an offender (a `WITH` added to silence it would have been cargo), which is
    half the reason that approach was abandoned. Value that makes this fail: any
    rewrite of this statement.
    """
    from tortoise.navigation import _hop_query

    statement = _hop_query("Point")
    assert statement is not None
    assert "UNION" in statement
    assert _predicate_drop_barrier(statement) == statement, (
        f"the benign UNION query was rewritten: {_predicate_drop_barrier(statement)!r}"
    )


def test_an_interpolated_predicate_is_seen_at_runtime() -> None:
    """#7050's blind spot: a whole interpolated predicate (`WHERE {fragment}`).

    The source scan could not see the fragment's variables, so it reported 0
    offenders over a tree holding the live defect. At this seam the fragment is
    already resolved, so the re-bound name is visible and the barrier lands.
    """
    fragment = "(sup.status IN ['superseded'] OR sup.outdated = true)"
    statement = (
        f"MATCH (sup:Point) WHERE {fragment}\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active:Point)\n"
        "RETURN count(DISTINCT r)"
    )
    out = _predicate_drop_barrier(statement)
    assert out != statement
    assert "WITH *" in out, out


# ── permissive doubles: the probe must never raise out of the guard (#7162 P2) ─

def test_a_permissive_double_never_raises_out_of_the_guard() -> None:
    """A duck-typed client answers EVERY name, so the version cache read and the
    version comparison must both be type-checked.

    Before the fix, a bare ``getattr`` returned the double's own ``MagicMock``
    for the cache marker, which was then subscripted / compared against a tuple:
    ``TypeError: 'Mock' object is not subscriptable`` and ``'>=' not supported
    between instances of 'MagicMock' and 'tuple'``. That raise escaped the guard
    (``_engine_module_version`` is not the refusal path), so a guarded query was
    refused BEFORE dispatch instead of being barred. Value that makes this fail:
    reverting to the bare ``getattr`` + ``cached[0]`` / ``version >=`` form.
    """
    from unittest import mock

    for double in (mock.Mock(), mock.MagicMock()):
        assert _engine_module_version(double) is None
        assert _engine_needs_predicate_barrier(double) is True


def test_a_probe_that_answers_a_non_list_is_unknown_not_a_crash() -> None:
    """A double can answer ``MODULE LIST`` with an arbitrary object. Iterating
    it would raise out of the probe; the shape must decode to UNKNOWN (barrier)
    instead. Value that makes this fail: the unguarded ``for row in rows``."""
    from unittest import mock

    assert _graph_module_version(mock.Mock()) is None


def test_a_forged_version_cache_is_discarded_and_the_probe_runs() -> None:
    """A permissive ``__getattr__`` FORGES the cache; the probe must still run.

    The forged value is not the exact 1-tuple this module writes, so it is
    discarded rather than used, and the real (6, 0, 1) answer decides. Value
    that makes this fail: trusting any non-``None`` cache attribute.
    """

    class _Forged:
        def __getattr__(self, name):
            return "not-a-version"

        def execute_command(self, *args):
            return [[b"name", b"graph", b"ver", 60001]]

    assert _engine_module_version(_Forged()) == (6, 0, 1)
    assert _engine_needs_predicate_barrier(_Forged()) is True


def test_a_permissive_handle_still_reaches_dispatch_with_the_barrier() -> None:
    """The seam's contract: an offending query is BARRED, never a TypeError.

    The handle's ``.client`` is a permissive double (a ``MagicMock``, as a test
    stub or a proxied client can be). Before the fix this raised ``TypeError``
    out of :func:`_guard_query` and the statement was not dispatched at all —
    the failure `tests/test_projection.py::test_server_projection_receives_the_
    bounded_timeouts` hit. Assert the RETURNED statement is the barred one.
    """
    from unittest import mock

    class _Handle:
        client = mock.MagicMock()

    out = _guard_query(_OFFENDER, {"ids": ["p1"]}, handle=_Handle())
    assert "WITH *" in out, out
    assert out != _OFFENDER, out


# ── identifier-position keywords are labels/types, not clauses (#7162 P1) ─────
#
# `_CLAUSE_STARTER_RE` matches on WORD BOUNDARIES, so a keyword-named label or
# relationship type (`(u:Return)`, `-[:USE]->`) matched as a clause. That
# truncated the re-binding pattern at the label/type, so a variable bound LATER
# in the same pattern was never seen and NO barrier was inserted — the silent
# filter bypass this rail exists to stop. The scan is now position-aware.

#: Keywords `_CLAUSE_STARTER_RE` recognises that are ALSO legal rel types.
_KEYWORD_REL_TYPES = [
    "USE", "SET", "CALL", "MERGE", "DELETE", "CREATE", "UNWIND",
    "WITH", "RETURN", "FOREACH", "UNION", "MATCH", "REMOVE",
]
#: The same for labels (case is irrelevant to the identifier match).
_KEYWORD_LABELS = ["SET", "Return", "Match", "Merge", "Call", "Unwind", "Use"]


@pytest.mark.parametrize("keyword", _KEYWORD_REL_TYPES)
def test_a_keyword_named_relationship_type_does_not_truncate_the_scan(
    keyword: str,
) -> None:
    """`[:USE]` is an IDENTIFIER; the re-bound `n` after it must still be seen."""
    statement = (
        f"MATCH (n:Point) WHERE n.id IN $ids "
        f"MATCH (u)-[:{keyword}]->(n) RETURN n.id"
    )
    out = _predicate_drop_barrier(statement)
    assert out != statement, (
        f"a keyword-named relationship type `[:{keyword}]` truncated the pattern "
        f"scan, so the predicate stays unbarred: {out!r}"
    )
    assert "WITH *" in out, out


@pytest.mark.parametrize("keyword", _KEYWORD_LABELS)
def test_a_keyword_named_label_does_not_truncate_the_scan(keyword: str) -> None:
    """`(u:Return)` is an IDENTIFIER; the re-bound `n` after it must still be seen."""
    statement = (
        f"MATCH (n:Point) WHERE n.id IN $ids "
        f"MATCH (u:{keyword})-[:R]->(n) RETURN n.id"
    )
    out = _predicate_drop_barrier(statement)
    assert out != statement, (
        f"a keyword-named label `:{keyword}` truncated the pattern scan: {out!r}"
    )
    assert "WITH *" in out, out


@pytest.mark.parametrize("keyword", ["USE", "Return", "Set", "With"])
def test_an_identifier_position_keyword_does_not_make_the_scan_fire(
    keyword: str,
) -> None:
    """The false-positive rail for the position fix: a keyword-named label/type
    whose pattern does NOT re-bind the filtered variable stays byte-identical."""
    statement = (
        f"MATCH (n:Point) WHERE n.id IN $ids "
        f"MATCH (u:{keyword})-[:R]->(m) RETURN n.id"
    )
    assert _predicate_drop_barrier(statement) == statement


# ── the relationship-type ALTERNATION: `|` is identifier position too ────────
#
# A type list `-[:A|B]->` writes its SECOND and later types after `|`, which the
# position set did not cover. Two consequences (MEASURED):
#
#   * fail-OPEN — a keyword type in the 2nd slot is read as a clause, the pattern
#     is truncated, the re-bound name is never seen and the shape reaches a 6.x
#     engine UNBARRED (a silent filter bypass);
#   * invalid Cypher — with `MATCH` as the type the barrier was injected INSIDE
#     the pattern (`-[:A|WITH * MATCH]->`), breaking an honest query.


def _alternation_rebind(keyword: str) -> str:
    return f"MATCH (a)-[:R|{keyword}]->(n)"


@pytest.mark.parametrize("keyword", _KEYWORD_REL_TYPES)
def test_a_keyword_type_in_an_alternation_does_not_truncate_the_scan(
    keyword: str,
) -> None:
    """`[:A|SET]` — the type after `|` is an IDENTIFIER to the clause scan.

    The barrier must land BEFORE the re-binding `MATCH` (so the statement is
    valid Cypher), never inside the bracketed type list.
    """
    rebind = _alternation_rebind(keyword)
    statement = f"MATCH (n:Point) WHERE n.id IN $ids {rebind} RETURN n.id"
    out = _predicate_drop_barrier(statement)
    assert out != statement, (
        f"a keyword type `|{keyword}` in an alternation truncated the pattern "
        f"scan, so the predicate stays unbarred: {out!r}"
    )
    assert out == statement.replace(rebind, f"WITH * {rebind}", 1), out
    assert "|WITH *" not in out, (
        f"the barrier was injected INSIDE the pattern (invalid Cypher): {out!r}"
    )


@pytest.mark.parametrize("keyword", ["SET", "USE", "RETURN"])
def test_a_keyword_type_in_the_where_own_pattern_does_not_lose_the_where(
    keyword: str,
) -> None:
    """The bogus clause was also picked as the `WHERE`'s PRECEDING clause, so the
    `WHERE` was skipped entirely and the shape stayed unbarred (fail-open)."""
    rebind = "MATCH (n)-[:REL]->(m)"
    statement = (
        f"MATCH (n:Point)-[:R|{keyword}]->(k) WHERE n.id IN $ids "
        f"{rebind} RETURN n.id"
    )
    out = _predicate_drop_barrier(statement)
    assert out == statement.replace(rebind, f"WITH * {rebind}", 1), out


@pytest.mark.parametrize("keyword", ["SET", "USE", "MATCH", "RETURN"])
def test_a_keyword_type_in_an_alternation_that_binds_nothing_stays_identical(
    keyword: str,
) -> None:
    """The false-positive rail, alternation spelling: a keyword-named type whose
    pattern does NOT re-bind the filtered name stays byte-identical."""
    statement = (
        f"MATCH (n:Point) WHERE n.id IN $ids "
        f"MATCH (a)-[:R|{keyword}]->(m) RETURN n.id"
    )
    assert _predicate_drop_barrier(statement) == statement


# ── a PREDICATE OPERATOR's `WITH`, and a LATER re-bind, still bar the shape ──
#
# Two more shapes the engine drops (MEASURED on falkordb 6.0.1 -> foreign rows):
#
#   1. `STARTS WITH` / `ENDS WITH`. The `WITH` is the second word of the
#      OPERATOR, but the clause scan matched it as a clause, so the clause after
#      the `WHERE` read as `WITH`, the scan `continue`d and NO barrier was
#      inserted. `CONTAINS` carries no keyword, which is why the curated cases
#      missed the collision.
#   2. a LATER plain MATCH that re-binds the filtered name. The trigger was read
#      off the IMMEDIATELY FOLLOWING MATCH only, so an intervening
#      `MATCH (m:Target)` (binding only `m`) made the shape look safe while the
#      engine still dropped the predicate and returned foreign rows.
#
#: The re-binding MATCH every shape below must be barred in front of.
_REBINDING_MATCH = "MATCH (n)-[:REL]"
_OPERATOR_OFFENDERS = [
    "MATCH (n:Point) WHERE n.name STARTS WITH 'a' "
    "MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    "MATCH (n:Point) WHERE n.name ENDS WITH 'neer' "
    "MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    # the first following plain MATCH binds only an unrelated name
    "MATCH (n:Point) WHERE n.id IN $ids MATCH (m:Target) "
    "MATCH (n)-[:REL]->(m) RETURN n.id",
]


@pytest.mark.parametrize("statement", _OPERATOR_OFFENDERS)
def test_a_predicate_operator_or_a_later_rebind_still_gets_the_barrier(
    statement: str,
) -> None:
    """The barrier lands IMMEDIATELY before the MATCH that re-binds the filtered
    name — not before the intervening unrelated MATCH, and not nowhere at all.

    Value that makes this fail: (1) reading the operator `WITH` as a clause, or
    (2) requiring the FIRST following MATCH to intersect ``pred_vars``.
    """
    out = _predicate_drop_barrier(statement)
    assert out != statement, f"the shape was left unbarred: {out!r}"
    assert out.count("WITH *") == 1, out
    assert out == statement.replace(
        _REBINDING_MATCH, f"WITH * {_REBINDING_MATCH}", 1
    ), out


@pytest.mark.parametrize("statement", [
    # `STARTS WITH` where the later MATCH does NOT re-bind the filtered name
    "MATCH (n:Point) WHERE n.name STARTS WITH 'a' "
    "MATCH (m:Point)-[:REL]->(z) RETURN n.id",
    # `ENDS WITH` followed by a genuine `WITH` barrier — already safe
    "MATCH (n:Point) WHERE n.name ENDS WITH 'a' "
    "WITH n MATCH (n)-[:REL]->(m) RETURN n.id",
])
def test_a_predicate_operator_is_not_by_itself_a_reason_to_rewrite(
    statement: str,
) -> None:
    """The false-positive rail: the operator's `WITH` must not be rewritten, and
    rejecting it must not make a legitimate `WITH` barrier invisible either."""
    assert _predicate_drop_barrier(statement) == statement


# ── the version gate, decided from the SERVER's own version ──────────────────


def test_the_gate_opens_on_the_affected_engine_and_shuts_on_the_safe_one() -> None:
    """MEASURED range: 6.0.0/6.0.1 drop the predicate, 4.20.x bind it.

    The gate is the whole point of the seam: `WITH *` is a no-op on the engines
    that already bind for every shape the normaliser still rewrites, so the
    version decision is made ONCE per client rather than by 27 hand-written
    literals that cannot know what they run on. (The one shape `WITH *` does NOT
    survive on 4.20.x is excluded by the normaliser, not by this gate — see the
    named-endpoint section below.)
    """

    class _Engine:
        def __init__(self, ver):
            self._ver = ver

        def execute_command(self, *args):
            return [[b"name", b"graph", b"ver", self._ver]]

    assert _engine_needs_predicate_barrier(_Engine(60001)) is True   # 6.0.1
    assert _engine_needs_predicate_barrier(_Engine(60000)) is True   # 6.0.0
    assert _engine_needs_predicate_barrier(_Engine(42004)) is False  # 4.20.4
    assert _engine_needs_predicate_barrier(_Engine(42006)) is False  # 4.20.6


def test_an_unreadable_version_gets_the_barrier_not_a_free_pass() -> None:
    """A probe that cannot answer must never become a silent filter bypass."""

    class _Refuses:
        def execute_command(self, *args):
            raise RuntimeError("MODULE LIST is denied here")

    assert _engine_needs_predicate_barrier(_Refuses()) is True
    assert _engine_needs_predicate_barrier(None) is True

    class _NoGraphModule:
        def execute_command(self, *args):
            return [[b"name", b"vectorset", b"ver", 1]]

    assert _engine_needs_predicate_barrier(_NoGraphModule()) is True


def test_the_module_version_is_decoded_from_the_raw_ver_integer() -> None:
    """`ver` is `MAJOR*10000 + MINOR*100 + PATCH` (measured on real containers)."""

    class _Engine:
        def __init__(self, rows):
            self._rows = rows

        def execute_command(self, *args):
            return self._rows

    assert _graph_module_version(
        _Engine([[b"name", b"graph", b"ver", 60001]])) == (6, 0, 1)
    assert _graph_module_version(
        _Engine([[b"name", b"vectorset", b"ver", 1],
                 [b"name", b"graph", b"ver", 42004]])) == (4, 20, 4)
    assert _graph_module_version(_Engine([])) is None


# ── handle-level: the seam covers every builder, not just the 27 patched ones ─


class _VersionedWire:
    """A connection that answers the version probe and records the wire."""

    def __init__(self, ver):
        self.ver = ver
        self.commands: list[tuple] = []

    def execute_command(self, *args, **kwargs):
        self.commands.append(args)
        if args and str(args[0]).upper() == "MODULE":
            if self.ver is None:
                return []
            return [[b"name", b"graph", b"ver", self.ver]]
        return [[], [], []]


class _RealGraphClient:
    """A client whose ``select_graph`` yields the REAL vendor graph class."""

    def __init__(self, wire):
        self._wire = wire

    def select_graph(self, graph_id):
        from falkordb import Graph as VendorGraph

        return VendorGraph(self._wire, graph_id)


def _handle_on(ver) -> tuple[object, _VersionedWire]:
    wire = _VersionedWire(ver)
    return guarded_client(_RealGraphClient, wire).select_graph("g"), wire


def _sent(wire: _VersionedWire) -> list[str]:
    return [c[2] for c in wire.commands if str(c[0]).upper().startswith("GRAPH.")]


def test_a_new_unbarriered_site_cannot_reach_a_6x_engine() -> None:
    """The class rail: an offender emitted by ANY builder is barred on the wire.

    This is the property a whole-tree scan cannot provide and #7050 proved it:
    a source regex is blind to interpolation, so it reports 0 over a live bug.
    Here the statement is already assembled, so the offender cannot ship green
    no matter which of the ~150 handle call sites produced it.
    """
    handle, wire = _handle_on(60001)
    handle.query(_OFFENDER, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" in sent[0], f"the offender reached a 6.0.1 engine: {sent[0]!r}"


def test_the_same_statement_is_untouched_on_a_binding_engine() -> None:
    """Reversibility: on 4.20.x the barrier is not needed, so no text changes.

    The vendor inlines its own param header (``CYPHER `ids`=[…] ``) into the
    statement, so the assertion is about the STATEMENT — the caller's Cypher
    reaches the engine byte-identical, with no barrier inserted.
    """
    handle, wire = _handle_on(42004)
    handle.query(_OFFENDER, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" not in sent[0], f"a binding engine had a barrier inserted: {sent[0]!r}"
    assert sent[0].endswith(_OFFENDER), sent[0]


@pytest.mark.parametrize("statement", _OPERATOR_OFFENDERS)
def test_an_operator_or_later_rebind_cannot_reach_a_6x_engine(statement: str) -> None:
    """The class rail for the operator/later-rebind shapes.

    These are the shapes a bare clause scan let through: the operator `WITH`
    truncated the clause after the `WHERE`, and the re-bind trigger was read off
    only the NEXT MATCH. Both must be barred on the wire to a 6.0.1 engine, not
    merely in the unit call — the rail's whole point is that no builder can emit
    an unbarred offender.
    """
    handle, wire = _handle_on(60001)
    handle.query(statement, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" in sent[0], (
        f"an unbarred shape reached a 6.0.1 engine: {sent[0]!r}"
    )


@pytest.mark.parametrize("keyword", ["SET", "USE", "MATCH", "RETURN"])
def test_an_alternation_keyword_type_cannot_reach_a_6x_engine(keyword: str) -> None:
    """The class rail for the alternation spelling: whatever builder assembles a
    `[:A|<keyword>]` type list, the barred statement must be the one on the wire
    — and it must be valid Cypher (the barrier before the MATCH, not inside the
    type list)."""
    statement = (
        f"MATCH (n:Point) WHERE n.id IN $ids "
        f"MATCH (a)-[:R|{keyword}]->(n) RETURN n.id"
    )
    handle, wire = _handle_on(60001)
    handle.query(statement, {"ids": ["p1"]})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert "WITH *" in sent[0], (
        f"an unbarred alternation reached a 6.0.1 engine: {sent[0]!r}"
    )
    assert "|WITH *" not in sent[0], (
        f"the barrier was injected inside the type list: {sent[0]!r}"
    )


# ── #7162 P2: a NAMED-endpoint PATTERN predicate must NOT be barred ──────────
#
# MEASURED on throwaway containers (falkordb v4.20.4 / server v4.20.6, and 6.0.1)
# with a topology where the correct answer and the defect answer differ:
#
#   4.20.x  no barrier : OK          -> the predicate is applied
#   4.20.x  `WITH *`   : ERROR       -> Unable to resolve filtered alias 'a'
#   6.0.1   no barrier : OK (correct rows) OR a LOUD error when the endpoint is
#                        not bound — never a silent bypass
#   6.0.1   `WITH *`   : OK, and on some spellings it MULTIPLIES rows
#
# The 4.20.x row is a FALSE REFUSAL of a legitimate query — P1-class by this
# module's own standard ("a guard that refuses a legitimate query is worse than
# the bug") — and it is REACHABLE: the repo's own per-graph ACL recipe
# (`acl_graph_users._GRAPH_COMMANDS`) grants a tenant user no `MODULE LIST`, so
# the probe is unreadable and the gate opens on a 4.20.x engine. The normaliser
# must therefore exclude the shape; the gate cannot (it only knows the version).
#
# The exclusion is NARROW: only a variable the predicate's OWN pattern INTRODUCES
# (`a` here — not bound by the preceding MATCH) is dropped from the re-bind
# trigger. A variable the predicate CONSTRAINS and a preceding MATCH bound (the
# `n.id IN $ids` case) still bars, so the #6976 rail is intact.

#: A `WHERE` whose predicate is itself a relationship pattern with a NAMED
#: endpoint (`a`), re-bound by a later plain `MATCH`.
_NAMED_ENDPOINT_PREDICATE = (
    "MATCH (n:Point) WHERE (n)<-[:REL]-(a) MATCH (a) RETURN n.id"
)


def test_a_named_endpoint_pattern_predicate_is_left_byte_identical() -> None:
    """Value that makes this fail: dropping only ``_predicate_vars`` as before."""
    assert _predicate_drop_barrier(_NAMED_ENDPOINT_PREDICATE) == (
        _NAMED_ENDPOINT_PREDICATE
    )


def test_the_false_refusal_is_gone_on_a_4x_engine_with_an_unreadable_probe() -> None:
    """The ACL reachability, pinned end-to-end: an unreadable probe on a 4.20.x
    engine must NOT put a barrier in front of this shape.

    Before the fix the gate opened (unreadable) and the normaliser rewrote the
    statement, so the engine refused a query that works unbarred. Value that
    makes this fail: the previous (unconditional) ``pred_vars`` trigger.
    """
    handle, wire = _handle_on(None)  # a 4.20.x engine the probe cannot read
    handle.query(_NAMED_ENDPOINT_PREDICATE, {})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert sent[0].endswith(_NAMED_ENDPOINT_PREDICATE), sent[0]
    assert "WITH *" not in sent[0], (
        f"a false refusal was dispatched to the engine: {sent[0]!r}"
    )


def test_the_same_shape_reaches_a_6x_engine_unbarred_and_therefore_loud() -> None:
    """No SILENT BYPASS is re-introduced: the shape is not rewritten on 6.x either.

    MEASURED on 6.0.1: unbarred this shape either returns the CORRECT rows or
    errors LOUDLY (``'a' not defined``); it is never the #6976 foreign-row
    bypass. The gate itself still WANTS a barrier on 6.x — the exclusion lives in
    the normaliser, which is deliberate, so assert both halves. Value that makes
    this fail: moving the exclusion into the version gate (which would then also
    suppress a shape the engine really does drop).
    """

    class _Engine:
        def execute_command(self, *args):
            return [[b"name", b"graph", b"ver", 60001]]

    assert _engine_needs_predicate_barrier(_Engine()) is True, (
        "the gate must still be open on 6.x; the exclusion belongs to the normaliser"
    )
    handle, wire = _handle_on(60001)
    handle.query(_NAMED_ENDPOINT_PREDICATE, {})
    sent = _sent(wire)
    assert len(sent) == 1, wire.commands
    assert sent[0].endswith(_NAMED_ENDPOINT_PREDICATE), sent[0]
    assert "WITH *" not in sent[0], sent[0]


#: The shapes the reviewer measured as FINE (barrier is a no-op, correct rows) on
#: 4.20.4 — the exclusion must not over-reach into any of them.
_FINE_WHEN_BARRED_ON_4X = [
    # id predicate + later re-bind (the base #6976 shape)
    "MATCH (n:Point) WHERE n.id IN $ids MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    # OR predicate
    "MATCH (n:Point) WHERE n.id = 'p1' OR n.id = 'p2' "
    "MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    # named path
    "MATCH p = (n:Point) WHERE n.id IN $ids "
    "MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    # STARTS WITH (the operator's `WITH`)
    "MATCH (n:Point) WHERE n.name STARTS WITH 'a' "
    "MATCH (n)-[:REL]->(m:Point) RETURN n.id",
    # ANONYMOUS pattern predicate — names no endpoint, so nothing is introduced
    "MATCH (n:Point) WHERE (n)<-[:REL]-({id:'p2'}) MATCH (n) RETURN n.id",
    # two barriered shapes in one statement
    "MATCH (n:Point) WHERE n.id IN $ids MATCH (n)-[:REL]->(m:Point) "
    "WHERE m.id IN $ids MATCH (m)-[:NEXT]->(z) RETURN n.id",
]


@pytest.mark.parametrize("statement", _FINE_WHEN_BARRED_ON_4X)
def test_shapes_the_barrier_does_not_break_stay_barred(statement: str) -> None:
    """The over-exclusion rail: these six shapes are MEASURED fine when barred on
    4.20.4, so they must keep their barrier — including on an unreadable probe."""
    out = _predicate_drop_barrier(statement)
    assert out != statement, f"the shape was left unbarred: {out!r}"
    assert "WITH *" in out, out
    handle, wire = _handle_on(None)
    handle.query(statement, {"ids": ["p1"]})
    sent = _sent(wire)
    assert "WITH *" in sent[0], (
        f"a shape that is FINE when barred lost its barrier: {sent[0]!r}"
    )


def test_a_function_argument_is_not_an_introduced_pattern_variable() -> None:
    """`toLower(n.name)` puts `n` after a `(` — but that `(` is a CALL, not a
    pattern, so `n` is CONSTRAINED by the predicate and must keep its power to
    trigger a barrier. Value that makes this fail: an endpoint regex without the
    identifier lookbehind (``_PATTERN_VAR_RE`` used directly)."""
    statement = (
        "MATCH (n:Point) WHERE toLower(n.name) STARTS WITH 'a' "
        "MATCH (n)-[:REL]->(m:Point) RETURN n.id"
    )
    out = _predicate_drop_barrier(statement)
    assert out != statement, f"the shape was left unbarred: {out!r}"
    assert "WITH *" in out, out


# ── #7162 P2 (the cheap half): an UNREADABLE verdict must not be permanent ────


def test_an_unreadable_probe_is_re_probed_after_the_ttl(monkeypatch) -> None:
    """One transient `MODULE LIST` failure must not taint a long-lived client.

    The verdict is cached — bounded re-probes, never one per query — but only
    until the TTL, after which a recovered server shuts the gate it never needed.
    Value that makes this fail: caching the unreadable verdict permanently (the
    second/third assertions see a stale ``True`` and ``calls`` stays at 1).
    """
    from tortoise import cypher_guard

    clock = {"t": 1000.0}
    monkeypatch.setattr(
        cypher_guard, "time", type("_Clock", (), {"monotonic": staticmethod(
            lambda: clock["t"])})()
    )

    class _Flaky:
        def __init__(self):
            self.calls = 0
            self.recovered = False

        def execute_command(self, *args):
            self.calls += 1
            if not self.recovered:
                raise RuntimeError("MODULE LIST is denied here")
            return [[b"name", b"graph", b"ver", 42004]]

    client = _Flaky()
    assert _engine_needs_predicate_barrier(client) is True
    # within the TTL the verdict is CACHED — no per-query probe
    assert _engine_needs_predicate_barrier(client) is True
    assert client.calls == 1, client.calls
    client.recovered = True
    clock["t"] += cypher_guard._ENGINE_VERSION_RETRY_SECONDS + 1.0
    assert _engine_needs_predicate_barrier(client) is False, (
        "a recovered 4.20.4 client is still barred after the TTL"
    )
    assert client.calls == 2, client.calls
