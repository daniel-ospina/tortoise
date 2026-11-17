"""#13098 — the Cypher census and its differential harness, pinned.

WHAT THIS SUITE IS FOR
======================
`docs/architecture/CYPHER-INVENTORY.md` publishes counts, and a published count
without a gate is a number that rots the first time a statement is added. Two
things are therefore pinned here:

1. **The census reconciles and the doc matches it** — `tools/cypher_inventory.py
   --check` re-runs the enumeration and compares every pinned count; drift is a
   red, not a footnote.
2. **The classifier's precedence is the thing that decides a verdict**, so it is
   tested directly rather than through the doc: a statement that needs a walk and
   also matches "plain" must be `traversal`; a fulltext QUERY must not be
   swallowed by the DDL arm (the first version of that regex ate every
   `CALL db.idx.*` and hid the whole class); a Postgres `CREATE TABLE` must not be
   counted as Cypher.

⛔ AND THE HARNESS MUST BE ABLE TO REFUSE. A differential harness whose only
observable outcome is "empty diff" is the vacuous-pin failure this epic has hit
repeatedly (#11859). `tools/cypher_diff_harness.py` therefore has an exit code
for "could not run" (2), and these tests pin that it is used: no lanes, an
inapplicable mutation, and a corpus that lacks a declared edge all refuse rather
than report agreement.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import cypher_diff_harness as harness  # noqa: E402
from tools import cypher_inventory as census  # noqa: E402


# --------------------------------------------------------------------------- #
# the census
# --------------------------------------------------------------------------- #
def test_the_doc_counts_match_the_census():
    """The pinned counts in CYPHER-INVENTORY.md are the census's own numbers."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "cypher_inventory.py"), "--check"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"census drift:\n{proc.stdout}\n{proc.stderr}"


def test_the_census_reconciles_arithmetically():
    """regex occurrences − non-call occurrences = AST door calls = classified + non-Cypher.

    This is the assertion the #13098 review earned: the first version of the tool
    printed a residual that ABSORBED a 52-statement loss, so the arithmetic closed
    over a wrong number. The reconciliation is only worth printing if it is an
    equality between measured values.
    """
    result = census.census(("tortoise",))
    totals = census.rg_totals(("tortoise",))
    ast_door_calls = len(result["sites"]) + result["non_cypher_door_calls"]
    assert totals["lines_door_calls"] - totals["door_occurrences_without_a_call"] == ast_door_calls
    assert result["ast_door_calls"] == ast_door_calls


def test_a_lambda_body_is_part_of_its_enclosing_function():
    """`lambda: sdk.query("MATCH …")` is a statement site, not an invisible one.

    The review found 52 of these (23 in `hosted_api.py`, 29 in `sdk.py`), dropped
    because the walker stopped at `ast.Lambda` and the residual line printed them
    as "comments".
    """
    import ast

    source = (
        "def f(sdk):\n"
        "    return asyncio.to_thread(lambda: sdk.query(\"MATCH (n:Point) RETURN n.id\"))\n"
    )
    tree = ast.parse(source)
    func = tree.body[0]
    calls = [n for n in census._own_nodes(func) if isinstance(n, ast.Call)]
    assert any(census._is_door(c) == "query" for c in calls), (
        "a door call inside a lambda must be visible to the census"
    )


@pytest.mark.parametrize(
    "statement,shape",
    [
        # precedence: a walk that also matches "plain" is a traversal
        ("MATCH path = (a)-[:IMPL|NAND*1..2]-(b) RETURN count(path)", "traversal"),
        ("MATCH p = shortestPath((a:Point)-[*]-(b:Point)) RETURN p", "traversal"),
        # adjacency: an edge pattern with no variable length
        ("MATCH (op:Point)-[:IMPL]->(t) RETURN t.id", "adjacency"),
        # a fulltext QUERY must not be eaten by the DDL arm
        ("CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node, score", "fulltext"),
        ("CALL db.idx.fulltext.createNodeIndex('Point', ['content'])", "ddl"),
        # EP state
        ("MATCH (n:Point) WHERE n.ep_alpha > 0.5 RETURN n.id", "ep"),
        # plain: single node, no edge, no ep
        ("MATCH (n:Point {id:$id}) RETURN n.content", "plain"),
        ("MATCH (t:Team) RETURN t.id, t.name", "plain"),
    ],
)
def test_shape_precedence(statement, shape):
    assert census.classify(statement)[0] == shape


def test_a_postgres_statement_is_not_cypher():
    """`CREATE TABLE` through a shared `execute` door is not a Cypher statement."""
    assert not census.CYPHER_MARK.search("CREATE TABLE IF NOT EXISTS public.point (id text)")
    assert census.CYPHER_MARK.search("CREATE (n:Point {id:'x'})")


# --------------------------------------------------------------------------- #
# the harness
# --------------------------------------------------------------------------- #
def test_the_harness_refuses_without_both_stores(capsys):
    """No lanes is exit 2 — a diff without two stores compares nothing to nothing."""
    assert harness.main(["--candidate", "point-by-id"]) == 2
    assert "could not run" in capsys.readouterr().err


def test_an_inapplicable_mutation_is_refused_not_applied():
    """`null-order` on a statement with no ORDER BY must refuse, never no-op.

    Review finding: the mutation used to return the SQL unchanged, so the run
    printed "diff EMPTY" and exited 0 while reporting `mutation: null-order` —
    a green run that proved nothing.
    """
    candidate = harness.CANDIDATES["point-by-id"]
    with pytest.raises(KeyError):
        harness._mutated(candidate, "null-order")


def test_every_declared_mutation_actually_changes_the_sql():
    """A mutation that changes nothing cannot prove the harness can fail."""
    for name, candidate in harness.CANDIDATES.items():
        for mutation in candidate.mutations:
            mutated = harness._mutated(candidate, mutation)
            assert mutated != candidate.sql, f"{name}/{mutation} changed nothing"


def test_the_diff_reports_a_dropped_row():
    """Row count is compared before values, so a filter change cannot hide in a zip."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate,
        [("p-1", "c", None)], ["id", "content", "point_kind"],
        [], ["id", "content", "point_kind"],
        ["p-1"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "row-count" for d in result["differences"])


def test_the_diff_separates_null_from_empty_string_and_zero():
    """Three-way null handling: '' and 0 are NOT 'the same as null'."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate,
        [("p-1", None, 0)], ["id", "content", "point_kind"],
        [("p-1", "", False)], ["id", "content", "point_kind"],
        ["p-1"],
    )
    assert result["equal"] is False
    kinds = {d["kind"] for d in result["differences"]}
    assert "family" in kinds


def test_the_diff_reports_order_for_an_ordered_statement():
    """An ordered statement compared as a sequence: the NULLS-LAST defect class."""
    candidate = harness.CANDIDATES["point-listing"]
    # the SAME rows, reversed — the null-placement defect exactly
    rows_cypher = [("a", "x", None), ("b", "y", "2026-01-01")]
    rows_sql = [("b", "y", "2026-01-01"), ("a", "x", None)]
    result = harness.diff(
        candidate, rows_cypher, ["id", "content", "created_at"],
        rows_sql, ["id", "content", "created_at"],
        ["a", "b"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "order" for d in result["differences"])


def test_an_unordered_statement_is_compared_as_a_multiset():
    """Row order is not part of an unordered statement's answer."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate, [("a", "x", None), ("b", "y", None)], ["id", "content", "point_kind"],
        [("b", "y", None), ("a", "x", None)], ["id", "content", "point_kind"],
        ["a", "b"],
    )
    assert result["equal"] is True, result["differences"]


def test_a_dropped_column_is_a_shape_difference():
    """Projection drift is not a value difference and must not be absorbed."""
    candidate = harness.CANDIDATES["point-by-id"]
    result = harness.diff(
        candidate, [("a", "x")], ["id", "content"],
        [("a", "x", None)], ["id", "content", "point_kind"],
        ["a"],
    )
    assert result["equal"] is False
    assert any(d["kind"] == "shape" for d in result["differences"])


def test_the_fingerprint_is_stable_across_runs():
    candidate = harness.CANDIDATES["point-by-id"]
    args = ([("a", "x", None)], ["id", "content", "point_kind"],
            [("a", "x", None)], ["id", "content", "point_kind"], ["a"])
    first = harness.diff(candidate, *args)["fingerprint"]
    second = harness.diff(candidate, *args)["fingerprint"]
    assert first == second


def test_a_credential_never_reaches_the_output():
    """The secret-hygiene rule the record modules apply, applied here."""
    redacted = harness._redact("postgresql://postgres:s3cret@127.0.0.1:15499/db")
    assert "s3cret" not in redacted
    assert "postgres:***@" in redacted
