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
"""
from __future__ import annotations

import re
from typing import ClassVar

from tortoise.audit import audit_graph
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
