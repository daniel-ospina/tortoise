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

import re
import subprocess
import sys
from decimal import Decimal
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


def test_the_doc_appendix_is_the_tools_own_output():
    """Appendix A is DERIVED from the tree, so the doc must carry the tool's output.

    ⛔ THIS ARM EXISTS BECAUSE THE PINS DO NOT REACH THE APPENDIX, and the gap is
    not theoretical — it cost this document the same defect twice in ONE PR
    (`#13223`). The 25 pins are SCALARS (`sites`, `shape_plain`, …): `--check`
    prints "25 pinned counts match" while §Appendix A's ~1,309 `file:line` rows
    name lines that no longer exist, because a later commit moved code inside a
    file that already carried sites. The counts do not move (an insertion is not a
    new statement) so EVERY gate in this suite stays green over a table whose
    whole purpose is to be a resolvable index.

    Measured twice on `#13223`: 644 of 1,309 rows wrong after `#13116`
    (`hosted_api.py` +131, `sdk.py` net +72), and 6 more after `#13214` moved
    `tortoise/metering.py` by +7 and `tortoise/postgres_bytes.py` by +4 — while
    `--check` was RC=0 both times.

    Why a byte comparison rather than sampling: the appendix is `--markdown`'s
    output verbatim (`tools/cypher_inventory.py`, one row per site, sorted by
    `(path, line)`), so any difference at all is a row the tree no longer
    supports. Sampling cannot say that — a sample of 12 passed on the run that
    still carried the 6 stale rows.
    """
    marker = "## Appendix A — every statement site, by shape\n"
    doc = (ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md").read_text(
        encoding="utf-8")
    assert marker in doc, (
        "CYPHER-INVENTORY.md no longer carries Appendix A's heading — this arm\n"
        "reads the table from that heading, so re-point it rather than deleting it")
    appendix = doc[doc.index(marker) + len(marker) + 1:]
    result = census.census(("tortoise/",))
    expected = census.markdown_table(result["sites"]) + "\n"
    if appendix == expected:
        return
    head_rows = appendix.splitlines()
    tool_rows = expected.splitlines()
    stale = [(i, a, b) for i, (a, b) in enumerate(zip(head_rows, tool_rows, strict=False))
             if a != b]
    shown = "\n".join(
        f"  row {i}: doc  {a[:96]}\n          tool {b[:96]}" for i, a, b in stale[:10])
    if len(stale) > 10:
        shown += f"\n  … and {len(stale) - 10} more differing row(s)"
    if not shown:
        # No COMMON row differs, so the difference is in length (a truncation or a
        # tail insertion) or in trailing bytes only. ⛔ These three cases are stated
        # SEPARATELY: an earlier cut printed "same length, differs only in trailing
        # bytes" unconditionally, which contradicted the row counts printed directly
        # above it on a truncation (measured: doc=1310 tool=1313 printed that line).
        if len(head_rows) != len(tool_rows):
            shown = (f"  no shared row differs — the appendix is the wrong LENGTH "
                     f"(doc={len(head_rows)}, tool={len(tool_rows)} rows)")
        else:
            shown = "  same row count and every shared row matches — differs only in trailing bytes"
    raise AssertionError(
        "Appendix A is not the census tool's own output — it is DERIVED from the\n"
        "tree, so regenerate it at THIS head rather than editing rows by hand:\n"
        "    python tools/cypher_inventory.py --markdown\n"
        "then replace everything after the `## Appendix A` heading with that\n"
        "output. This failure is what a stale `file:line` index looks like — the\n"
        "pins cannot see it, because counts do not move when code moves within a\n"
        "file that already carries sites (measured on #13223, twice).\n"
        f"rows: doc={len(head_rows)} tool={len(tool_rows)}; "
        f"{len(stale)} row(s) differ.\n" + shown)


def test_the_doc_appendix_row_count_is_the_census_site_count():
    """The appendix's row count is §0's `sites` pin — one row per classified site.

    A cheap second angle on the same artifact: a truncated or duplicated appendix
    that happens to keep the surviving rows byte-identical still reds here, and
    the message names the number the document itself publishes.
    """
    marker = "## Appendix A — every statement site, by shape\n"
    doc = (ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md").read_text(
        encoding="utf-8")
    appendix = doc[doc.index(marker) + len(marker) + 1:]
    rows = [ln for ln in appendix.splitlines() if ln.startswith("| `")]
    result = census.census(("tortoise/",))
    assert len(rows) == len(result["sites"]), (
        f"Appendix A carries {len(rows)} site rows, the census classifies "
        f"{len(result['sites'])} — regenerate it with "
        "`python tools/cypher_inventory.py --markdown` rather than hand-editing")


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
    # ⛔ This second assertion was an IDENTITY and could never fail: both buckets
    # come from the SAME regex, so `regex − non-call` equals the regex's own
    # partition total by construction, and subtracting the AST count from it is
    # the assertion above rearranged. Its comment claimed a line regex and an AST
    # walk were "measured independently", which the arithmetic denies
    # (`#13098` review P2). What is asserted now is a PER-LINE cross-check of the
    # line regex against the AST's own door nodes — strictly stronger than the
    # sum, because a balanced pair of opposite errors (one line where the regex
    # sees two occurrences and the AST holds one node, another off in the other
    # direction) leaves the totals closing perfectly and is caught only here.
    # Non-vacuity MEASURED, and stated for exactly what it shows: putting a
    # docstring `.query(` on a line that already carries a real door call takes
    # this counter 0 -> 1. That mutant ALSO reddens the assertion above (the
    # occurrence leaves `door_occurrences_without_a_call`, so `regex − non-call`
    # grows by one) — so what it proves is that this counter can be non-zero, NOT
    # that it is the only arm that sees a divergence. What only this counter sees
    # is the BALANCED case: two lines that are wrong in opposite directions leave
    # every total above closing perfectly, and no assertion on those totals can
    # ever notice (`#13098` review P3 caught this comment claiming more than the
    # mutant showed).
    assert totals["lines_where_regex_and_ast_disagree"] == 0, (
        "the door regex and the AST disagree on how many door calls ONE LINE carries — the "
        "totals can still close over that, which is exactly why this arm exists: "
        f"{totals}"
    )


def test_the_census_pin_can_report_drift(tmp_path):
    """The pin's FAILURE path is asserted, not just its happy path.

    `test_the_doc_counts_match_the_census` only asserts `returncode == 0`, so
    making `check()` permanently unable to redden (its drift guard replaced by
    `if False:`) left this suite green — the vacuous-pin class the module's own
    docstring cites (`#11859`), and the `#13098` review's F1. Each arm below is a
    way the pin is supposed to fail.
    """
    result = census.census(("tortoise",))
    doc_text = (ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md").read_text(encoding="utf-8")
    assert census.check(ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md", result) == 0

    def write(text: str):
        path = tmp_path / "doc.md"
        path.write_text(text, encoding="utf-8")
        return path

    # (a) a pin one off — DRIFT
    drifted = write(doc_text.replace("<!-- cypher-inventory:sites = ", "<!-- cypher-inventory:sites = 1", 1))
    assert census.check(drifted, result) == 1
    # (b) a pin removed — MISSING
    missing_line = next(line for line in doc_text.splitlines()
                        if line.startswith("<!-- cypher-inventory:candidates ="))
    assert census.check(write(doc_text.replace(missing_line + "\n", "")), result) == 1
    # (c) a pin the tool no longer computes — STALE
    assert census.check(write(doc_text + "\n<!-- cypher-inventory:shape_retired = 1 -->\n"),
                        result) == 1
    # (d) no doc at all — could not run, which is 2 and never 0
    assert census.check(tmp_path / "absent.md", result) == 2


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
        # adjacency: the BRACKETLESS spellings. They have no brackets for the first
        # arm to see, so they fell through every shape arm to `plain`/`read` →
        # `candidate:SQL-shaped`, the one direction the verdict flips (`#13098`
        # review P3). There IS one live site in this form —
        # `tortoise/domain_validators.py:240`'s `AND NOT (n)--()`, which is a real
        # adjacency predicate — so this is not purely prophylactic.
        ("MATCH (a:Point {id:$a})-->(b:Point) RETURN b.id", "adjacency"),
        ("MATCH (a:Point {id:$a})<--(b:Point) RETURN a.id", "adjacency"),
        ("MATCH (a:Point {id:$a})--(b:Point) RETURN a.id", "adjacency"),
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


def test_a_sql_comment_is_not_a_bracketless_edge():
    """The bracketless arm is CONTEXT-ANCHORED, and this is why.

    `--` is also SQL's comment introducer, so a blanket `--` arm reports
    `SELECT * FROM public.point -- note` as an `adjacency` statement. In
    `enumerate_file` that is currently harmless because `SQL_VETO` runs first —
    but a classification that is correct only because of a caller's ordering is
    a latent false candidate, which is the class this fix removes. So an
    arrowhead (`-->`, `<--`) counts anywhere, and a bare `--` counts only BETWEEN
    two node patterns, which is where no SQL comment sits.
    """
    for sql in (
        "SELECT id FROM public.point -- the draft filter",
        "INSERT INTO public.point (id) VALUES ($1) -- note",
        "UPDATE public.point SET content = $1 WHERE id = $2 -- note",
    ):
        assert census.classify(sql)[0] != "adjacency", sql
    # …while the Cypher bare `--` between node patterns does.
    assert census.classify(
        "MATCH (n:Point {status:'draft'}) WHERE NOT (n)--() RETURN n.id")[0] == "adjacency"
    # The widening must not touch the SQL veto: `--` is NOT a node pattern, so a
    # SQL statement carrying a comment is still vetoed as non-Cypher.
    assert not census.NODE_PATTERN.search("SELECT id FROM public.point -- note")


def test_the_one_live_bracketless_site_is_an_adjacency_predicate():
    """`tortoise/domain_validators.py:240` — the site the widening actually moves.

    It left `candidate:SQL-shaped` for `measure:across shapes`, which is the
    correct direction: `AND NOT (n)--()` asks the graph a question about EDGES, so
    it is not a statement an SQL row could answer. Pinned by file:line so a later
    edit that removes the predicate also removes this expectation.
    """
    source = (census.ROOT / "tortoise/domain_validators.py").read_text()
    assert "AND NOT (n)--()" in source, (
        "the live bracketless site moved or was deleted; re-cut the census pins "
        "and this test together (§7 of the doc)"
    )


def test_a_postgres_statement_is_not_cypher():
    """`CREATE TABLE` through a shared `execute` door is not a Cypher statement."""
    assert not census.CYPHER_MARK.search("CREATE TABLE IF NOT EXISTS public.point (id text)")
    assert census.CYPHER_MARK.search("CREATE (n:Point {id:'x'})")


@pytest.mark.parametrize(
    "statement,shape",
    [
        # the write-clause shapes the first `WRITE_INLINE` arm could not see. Each
        # left role=`read` on a statement that mutates the graph, so a `plain`
        # one got verdict `candidate:SQL-shaped` — the one direction the verdict
        # flips, and therefore an inflated headline candidate count
        # (`#13098` review P2, measured on Appendix A's own output).
        ("MATCH (n:Point {id:$id}) SET n += $props RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n = $props RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n:Object RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) SET n.version = 1 RETURN count(n)", "plain"),
        # the SET target folded out of the literal: nothing follows the keyword
        ("MATCH (n:Point {id:$id}) SET {} RETURN n.id", "plain"),
        ("MATCH (n:Point {id:$id}) SET ??? RETURN count(n)", "plain"),
        # the verb is inline, so the `^`-anchored `WRITE_VERB` arm cannot see it
        ("MATCH (a:Point {id:$a}), (b:Point {id:$b}) MERGE (a)-[:CORR]->(b)", "adjacency"),
        ("MATCH (s:Point {id:$sid}), (t) WHERE ID(t) = $tid CREATE (s)-[:CONTAINS]->(t)",
         "adjacency"),
        ("MATCH ({} {id:$id}) REMOVE {}.embedding", "plain"),
        # a BACKTICKED target containing a space. The SET arm was widened for this
        # spelling and the DELETE/REMOVE/DROP arm was not, so a legal Cypher write
        # still classified role=`read` → `candidate:SQL-shaped` (`#13098` review P3).
        ("MATCH (n:Point {id:$id}) SET `my node`.`a b` = 1 RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) REMOVE `my node`.`a b` RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) DELETE `my node` RETURN count(n)", "plain"),
        ("MATCH (n:Point {id:$id}) DROP `my node` RETURN count(n)", "plain"),
    ],
)
def test_an_inline_write_clause_is_a_write_not_a_candidate(statement, shape):
    """A statement carrying a write clause gets role `write`, never `candidate`.

    Role is half the verdict (`VERDICTS[(shape, role)]`), so a write read as a
    read is a migration candidate for a statement that mutates the graph — or, on
    an adjacency shape, a `measure`. No test asserted `role` at all before this
    one, which is why the defect was unguarded (`#13098` review P2).
    """
    got_shape, role = census.classify(statement)
    assert got_shape == shape, f"premise: {statement!r} classifies by shape as {got_shape!r}"
    assert role == "write", f"{statement!r} is a write clause, classified role={role!r}"
    assert not census.VERDICTS[(shape, role)].startswith(("candidate", "measure"))


def test_a_call_whose_procedure_name_the_text_cannot_prove_is_not_a_candidate():
    """A `CALL` naming no provable procedure must not be published as a READ.

    ⛔ The live defect (`#13098` review P1): `tortoise/projection/__init__.py` loops
    over `("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex")` and calls
    `CALL {drop_proc}('Point')`. The resolver folds a loop variable to `{}`, so the
    NAME left the text — and a fulltext-index DROP was classified `plain`/`read`,
    which the verdict table maps to `candidate:SQL-shaped`: two graph WRITES inside
    the headline migration-candidate count. The un-folded spellings are the
    premise-check for this test: they classify `ddl`, so the loss was the folding,
    not the DDL arm.
    """
    for statement in ("CALL db.idx.fulltext.drop('Point')",
                      "CALL db.idx.fulltext.dropIndex('Event')"):
        assert census.classify(statement) == ("ddl", "ddl"), statement
    for statement in ("CALL {}('Point')",
                      "CALL ???('Point')",
                      # outside the engine's own namespace: read-vs-write is the
                      # plugin's business and is not visible in the statement
                      "CALL apoc.create.node(['X'], {}) YIELD node RETURN node"):
        assert census.classify(statement) == ("unresolved", "unresolved"), statement
    # …and the `db.*` procedures that ARE provable keep their own shapes: a blanket
    # "any CALL is unknown" would swallow every fulltext/vector READ.
    assert census.classify("CALL db.idx.fulltext.queryNodes('Point', 'x') YIELD node RETURN node") \
        == ("fulltext", "read")


def test_a_read_clause_is_still_a_read():
    """The other direction: the fix must not make every statement a write.

    A guard that fires on everything is as useless as one that fires on nothing,
    and it would deflate the candidate count instead of inflating it.
    """
    for statement in (
        "MATCH (n:Point {id:$id}) RETURN n.content",
        "MATCH (n:Point) WHERE n.createdAt IS NULL RETURN count(n)",
        "MATCH (m:Membership {user_id:$uid, role:'owner'}) RETURN m.role",
    ):
        assert census.classify(statement)[1] == "read", statement


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


def test_the_order_pass_uses_the_value_pass_equality_for_numbers():
    """A numeric column must not report `order` on two forms that agree.

    ⛔ The order pass compared `_canon` (a json serialisation) while the value
    pass compared `_norm`, and those disagree on `1` vs `1.0`: `public.point`
    carries `confidence` as `double precision`, so the record answers
    `float`/`Decimal` where FalkorDB answers the `int` the property was written
    with. Measured before the fix: identical rows in identical order reported
    `['order']` (`#13098` review P2) — so an ordered EP-shaped candidate could
    never return an empty diff. Both directions are pinned here: the numeric
    difference is NOT an order difference, and a real reorder still is.
    """
    candidate = harness.CANDIDATES["point-listing"]
    cols = ["id", "content", "created_at"]
    # same rows, same order; the record's numbers arrive as Decimal/float
    result = harness.diff(
        candidate, [("a", "x", 1), ("b", "y", 2)], cols,
        [("a", "x", Decimal("1")), ("b", "y", Decimal("2.0"))], cols,
        ["a", "b"],
    )
    assert result["equal"] is True, result["differences"]
    # …and the same values REORDERED is still the null-placement class
    reversed_rows = harness.diff(
        candidate, [("a", "x", 1), ("b", "y", 2)], cols,
        [("b", "y", Decimal("2.0")), ("a", "x", Decimal("1"))], cols,
        ["a", "b"],
    )
    assert reversed_rows["equal"] is False
    assert [d["kind"] for d in reversed_rows["differences"]] == ["order"]


def test_the_multiset_pass_pairs_rows_with_the_equality_it_compares_with():
    """A multiset that is equal must not report `value` differences.

    ⛔ The pass sorted by `_canon` (a json string) and compared by `_norm`: `1`
    serialises `"1"` and `1.0` as `"1.0"`, and `]` > `,` > `.`, so the two sides
    sorted into OPPOSITE orders and the positional pairing compared different rows
    — two `value` differences on multisets that are equal (`#13098` review P3).
    Reachable from the real stores for the same reason the order pass was: the
    record's numeric columns arrive as `float`/`Decimal`.
    """
    candidate = harness.Candidate(name="numeric", cypher="", sql="",
                                 columns=("id", "confidence"), ordered=False)
    cols = ["id", "confidence"]
    equal = harness.diff(candidate, [("a", 1), ("a", 10)], cols,
                        [("a", 1.0), ("a", 10.0)], cols, ["a"])
    assert equal["equal"] is True, equal["differences"]
    # …and a REAL value difference in the same shape is still reported
    different = harness.diff(candidate, [("a", 1), ("a", 10)], cols,
                            [("a", 1.0), ("a", 11.0)], cols, ["a"])
    assert different["equal"] is False
    assert [(d["kind"], d["cypher"], d["sql"]) for d in different["differences"]] == \
        [("value", 10, 11.0)]


def test_the_multiset_sort_key_agrees_with_the_equality_for_collections_too():
    """The sort key must be a total order that agrees with `_norm_row` — for lists
    and dicts, not only scalars.

    ⛔ `_norm` recurses into collections but a json rendering of its result does
    not, so `[1]` and `[1.0]` stayed different keys and the false `value`
    difference this file's `_sort_key` closes for a scalar column was still
    reproducible for a collection column (`#13098` review P3). The census lists
    plenty of `collect(…)`/`properties(…)` reads, so this is the shape a future
    candidate would hit.
    """
    candidate = harness.Candidate(name="collections", cypher="", sql="",
                                 columns=("id", "tags"), ordered=False)
    cols = ["id", "tags"]
    for cypher_rows, sql_rows in (
        ([("a", [1]), ("a", [10])], [("a", [1.0]), ("a", [10.0])]),
        ([("a", {"k": 1})], [("a", {"k": 1.0})]),
        ([("a", [1, [2]])], [("a", [1.0, [2.0]])]),
    ):
        result = harness.diff(candidate, cypher_rows, cols, sql_rows, cols, ["a"])
        assert result["equal"] is True, (cypher_rows, sql_rows, result["differences"])
    # …and the collection pass still reports a REAL difference
    different = harness.diff(candidate, [("a", [1])], cols, [("a", [2])], cols, ["a"])
    assert different["equal"] is False
    assert [d["kind"] for d in different["differences"]] == ["value"]


def test_a_type_family_inside_a_collection_is_still_a_difference():
    """`bool` vs `int` nested in a list/map/set must redden — it is a FAMILY change.

    ⛔ `_family` answered only for the TOP-LEVEL value, so two lists compared as
    `list` vs `list` and Python's own `[True] == [1]` then called them equal: the
    header's rule ("a type family change — int vs str, bool vs int — is a
    DIFFERENCE") was silently not applied inside a collection, which is a false
    PASS — the worst class this file can produce (`#13098` review P3).
    """
    candidate = harness.Candidate(name="families", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    pairs = (([True], [1]), ({"k": True}, {"k": 1}), ([1], ["1"]), ({True}, {1}),
             # ⛔ a SWAP between two positions: the SET of leaf families is identical
             # on both sides, so a set-based check skipped the gate and Python's own
             # `True == 1` then certified the row — a false PASS (`#13098` review P2)
             ([[True], [1]], [[1], [True]]),
             ({"flag": True, "count": 1}, {"flag": 1, "count": True}),
             # …and a null nested under a key is still three-way
             ({"k": None}, {"k": ""}))
    for cypher_val, sql_val in pairs:
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is False, (cypher_val, sql_val, result["differences"])
        assert [d["kind"] for d in result["differences"]] == ["family"], result["differences"]
    # …and a numeric spelling change inside a collection is NOT a difference
    numeric = harness.diff(candidate, [("a", [1])], cols, [("a", [1.0])], cols, ["a"])
    assert numeric["equal"] is True, numeric["differences"]


def test_a_shape_change_is_a_VALUE_difference_not_a_family_one():
    """Structure is not a family: arity, key names and emptiness must read `value`.

    ⛔ The first cut of the recursive check carried ARITY and KEY NAMES in the
    compared signature, so a list that merely grew an element was relabelled
    `kind: family` — and the `continue` then skipped the value comparison that used
    to name it. That is a diagnostic regression: `kind` is what the CLI prints and
    what §5 of the census doc documents (`#13098` review P3).
    """
    candidate = harness.Candidate(name="shape", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    pairs = (([1], [1, 2]), ([1, 2], [1, 2, 3]), ({"a": 1}, {"a": 1, "b": 2}),
             ({"a": 1}, {"b": 1}), ([], [1]), ({}, {"a": 1}), ([1], []),
             # ⛔ ONE LEVEL DOWN too: `_skeleton`'s entry arm dropped the value
             # subtree, so every map below the top level had one skeleton and a pure
             # structure change was labelled `family` (`#13098` review P3)
             ({"a": [1]}, {"a": [1, 2]}), ({"a": {"x": 1}}, {"a": {"x": 1, "y": 2}}),
             ({"a": {"x": 1}}, {"a": {"y": 1}}), ({"a": {}}, {"a": {"x": 1}}),
             ({"a": {"x": 1}}, {"a": [1]}))
    for cypher_val, sql_val in pairs:
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is False, (cypher_val, sql_val)
        assert [d["kind"] for d in result["differences"]] == ["value"], \
            (cypher_val, sql_val, result["differences"])


def test_the_same_nan_on_both_sides_is_not_a_difference():
    """A NaN must compare EQUAL to itself, at any depth.

    Python says `nan != nan`, so the same NaN on both sides reddened forever on a
    column that can legitimately hold one — and nested it depended on whether the
    driver handed back one object or two, i.e. on allocation rather than on data
    (`#13098` review P3).
    """
    candidate = harness.Candidate(name="nan", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    nan = float("nan")
    for cypher_val, sql_val in ((nan, float("nan")), ([nan], [float("nan")]),
                                ([nan], [nan]), ({"k": nan}, {"k": float("nan")})):
        result = harness.diff(candidate, [("a", cypher_val)], cols,
                             [("a", sql_val)], cols, ["a"])
        assert result["equal"] is True, (cypher_val, sql_val, result["differences"])
    # …but a NaN is NOT equal to a number, to null, or to a STRING that spells it.
    # ⛔ `"NaN"` is the case that matters: canonicalising a NaN to that token put an
    # ordinary string into the number's slot, so a row holding a real `"NaN"`
    # cancelled a NaN in another position (`#13098` review P2).
    for other in (0, 1, None, "nan", "NaN", "NAN"):
        result = harness.diff(candidate, [("a", nan)], cols, [("a", other)], cols, ["a"])
        assert result["equal"] is False, other
    swapped = harness.diff(candidate, [("a", [nan, "NaN"])], cols,
                          [("a", ["NaN", nan])], cols, ["a"])
    assert swapped["equal"] is False, swapped["differences"]
    # ⛔ and the FINGERPRINT must still tell them apart: a str token put the string
    # into the number's slot, so two different rows fingerprinted identically
    nan_fp = harness.diff(candidate, [("a", nan)], cols, [("a", nan)], cols, ["a"])
    str_fp = harness.diff(candidate, [("a", "NaN")], cols, [("a", "NaN")], cols, ["a"])
    assert nan_fp["fingerprint"] != str_fp["fingerprint"], "a NaN and the string 'NaN' render alike"


def test_a_numeric_spelling_change_is_one_number_but_a_rounded_one_is_not():
    """`Decimal`/`float`/`int` compare NUMERICALLY — and EXACTLY.

    ⛔ `_norm` converted a `Decimal` to `float`, which ROUNDS: two distinct numerics
    beyond the float mantissa became the same number and a real difference read
    EQUAL — a false PASS in the value pass (`#13098` review P3).
    """
    from decimal import Decimal

    candidate = harness.Candidate(name="numeric", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    for left, right in ((Decimal("1"), 1), (Decimal("1.0"), 1), (Decimal("1"), 1.0),
                        (Decimal("NaN"), float("nan"))):
        result = harness.diff(candidate, [("a", left)], cols, [("a", right)], cols, ["a"])
        assert result["equal"] is True, (left, right, result["differences"])
    # …and the two values `float()` folds together are NOT one number
    for left, right in ((Decimal("9007199254740993"), Decimal("9007199254740992")),
                        (Decimal("9007199254740993"), 9007199254740992)):
        result = harness.diff(candidate, [("a", left)], cols, [("a", right)], cols, ["a"])
        assert result["equal"] is False, (left, right)


def test_a_bytes_like_compares_and_renders_by_content_not_by_address():
    """`str(memoryview(b"x"))` is an ADDRESS, so rendering it reported a false `value`.

    psycopg2 returns `memoryview` for `bytea`, so this is the common bytea path, and
    the fingerprint of one row changed per object (`#13098` review P3).
    """
    candidate = harness.Candidate(name="bytes", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    same = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    assert same["equal"] is True, same["differences"]
    # the two driver spellings of one `bytea` are one family
    across = harness.diff(candidate, [("a", b"x")], cols, [("a", memoryview(b"x"))], cols, ["a"])
    assert across["equal"] is True, across["differences"]
    # …and different content is a difference, not a representation one
    differ = harness.diff(candidate, [("a", b"x")], cols, [("a", b"y")], cols, ["a"])
    assert differ["equal"] is False and [d["kind"] for d in differ["differences"]] == ["value"]
    # ⛔ TWO rows in BOTH orders: rendering the ADDRESS sorted the same data into two
    # different orders, so the multiset pass could pair the wrong rows and invent a
    # `value` difference on identical data.
    for rows in ([("a", memoryview(b"x")), ("b", memoryview(b"y"))],
                 [("b", memoryview(b"y")), ("a", memoryview(b"x"))]):
        same_rows = harness.diff(candidate, rows, cols, rows, cols, ["a"])
        assert same_rows["equal"] is True, same_rows["differences"]
    # ⛔ the FINGERPRINT is a function of the data, not of the object: at the parent
    # commit these two measurements of the identical row differed (`#13098` review P3)
    one = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    two = harness.diff(candidate, [("a", memoryview(b"x"))], cols,
                       [("a", memoryview(b"x"))], cols, ["a"])
    assert one["fingerprint"] == two["fingerprint"], "the fingerprint follows object identity"
    other = harness.diff(candidate, [("a", memoryview(b"y"))], cols,
                        [("a", memoryview(b"y"))], cols, ["a"])
    assert one["fingerprint"] != other["fingerprint"]


def test_a_non_string_dict_key_does_not_take_the_diff_down():
    """A map KEY of any type must not raise after every comparison has succeeded.

    `json.dumps` accepts only str/int/float/bool/None keys — a bare `_norm` dump
    raised there — and `_norm`'s own `sorted(value.items())` raised for a map mixing
    key TYPES. Either one is a `TypeError` out of `diff()` instead of a verdict
    (`#13098` review P3). Reachability is nil today (psycopg2's `jsonb` and
    FalkorDB's maps both yield str keys), which is why it needs a pin.
    """
    from decimal import Decimal

    candidate = harness.Candidate(name="keys", cypher="", sql="",
                                 columns=("id", "v"), ordered=False)
    cols = ["id", "v"]
    rows = [("a", {Decimal("1"): "x"}),
            ("a", {Decimal("1"): "x", "a": "y"}),   # two key TYPES
            ("a", {1: "x", "1": "y"})]              # two keys, one rendering
    for row in rows:
        result = harness.diff(candidate, [row], cols, [row], cols, ["a"])
        assert result["equal"] is True, (row, result["differences"])
        assert result["fingerprint"], "the fingerprint is computed after the sort — it must survive"
    # ⛔ and the fingerprint must still tell two such maps APART: a bare `str(k)`
    # collapsed `1` and `"1"` into one entry and dropped the other's value, so two
    # different rows fingerprinted identically (`#13098` review P3).
    left = harness.diff(candidate, [("a", {1: "x", "1": "y"})], cols,
                       [("a", {1: "x", "1": "y"})], cols, ["a"])
    right = harness.diff(candidate, [("a", {1: "x", "1": "z"})], cols,
                        [("a", {1: "x", "1": "z"})], cols, ["a"])
    assert left["fingerprint"] != right["fingerprint"], "a map key was dropped from the rendering"
    # ⛔ and a NON-JSON leaf (`date`, `bytes`) must not collide with the string that
    # spells it — the same identity loss, one line below the NaN arm
    import datetime

    for value, spelling in ((datetime.date(2020, 1, 1), "2020-01-01"), (b"x", "x")):
        left = harness.diff(candidate, [("a", value)], cols, [("a", value)], cols, ["a"])
        right = harness.diff(candidate, [("a", spelling)], cols, [("a", spelling)], cols, ["a"])
        assert left["fingerprint"] != right["fingerprint"], (value, spelling)

def test_a_projection_that_drifts_on_both_sides_still_reddens_against_the_declaration():
    """`Candidate.columns` is a CONTRACT, not a comment (`#13098` review P3).

    The cross-engine checks alone cannot see this: both stores dropping the same
    column agrees with itself, so only the declared list can catch it.
    """
    candidate = harness.CANDIDATES["point-by-id"]  # declares id/content/point_kind
    result = harness.diff(
        candidate, [("a", "x")], ["id", "content"],
        [("a", "x")], ["id", "content"],
        ["a"],
    )
    assert result["equal"] is False
    shape = [d for d in result["differences"] if d["kind"] == "shape"]
    assert shape, result["differences"]
    assert shape[0]["detail"] == "declared column list"
    assert shape[0]["declared"] == ["id", "content", "point_kind"]


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


def test_a_password_containing_an_at_sign_is_redacted_whole():
    """`@` is legal unencoded in a password, and the authority ends at the LAST one.

    The pre-fix `_redact` split on the FIRST `@`, so
    `postgresql://postgres:p@ssw0rd@host/db` printed `ssw0rd` — the tail of a
    working credential, in CI logs, on every run (`#13098` review). libpq and
    redis-py both split userinfo at the last `@`, so this is reachable with a
    real generated DSN.
    """
    leaked = harness._redact("postgresql://postgres:p@ssw0rd@127.0.0.1:15499/db")
    assert "ssw0rd" not in leaked, f"the password tail reached the output: {leaked}"
    assert "p@ssw0rd" not in leaked
    assert leaked == "postgresql://postgres:***@127.0.0.1:15499/db"


def test_the_benchmark_reports_no_verdict_and_still_checks_the_corpus(monkeypatch):
    """`--bench` is a measurement: it must not exit 0, and it must still refuse a
    corpus that lacks a declared edge.

    Exit 0 is this tool's word for "the two forms are the same answer". `--bench`
    diffed nothing and used to return 0 — before the corpus-edge check, too — so
    a caller keying on 0 read a timing run as agreement (`#13098` review).
    """
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})
    monkeypatch.setattr(harness, "run_cypher",
                        lambda *_a, **_k: ([("p-null-content",), ("p-retired",), ("p-null-created",)], ["id"]))
    monkeypatch.setattr(harness, "run_sql",
                        lambda *_a, **_k: ([("p-null-content",), ("p-retired",), ("p-null-created",)], ["id"]))
    monkeypatch.setattr(harness, "bench", lambda *_a, **_k: {"candidate": "point-listing"})
    argv = ["--graph-uri", "docker://:p@127.0.0.1:1",
            "--record-dsn", "postgresql://u:p@127.0.0.1/db",
            "--bench", "10"]

    # (a) the corpus edge is present -> the bench runs, and it does NOT report equal
    assert harness.main([*argv, "--candidate", "point-listing"]) == harness.EXIT_NO_VERDICT
    assert harness.EXIT_NO_VERDICT != 0

    # (b) the corpus edge is ABSENT -> refused with 2, and `bench` never ran
    monkeypatch.setattr(harness, "run_cypher", lambda *_a, **_k: ([], ["id"]))
    monkeypatch.setattr(harness, "run_sql", lambda *_a, **_k: ([], ["id"]))
    ran = []
    monkeypatch.setattr(harness, "bench", lambda *_a, **_k: ran.append(1) or {})
    assert harness.main([*argv, "--candidate", "point-listing"]) == 2
    assert ran == [], "a corpus missing a declared edge must be refused BEFORE it is measured"


def test_an_unexpected_failure_is_could_not_run_not_a_difference(monkeypatch, capsys):
    """Exit **1** means "the diff is NON-EMPTY" — so a crash must not be able to reach it.

    `bench()` was called outside every inner `try` in `main()`, and `main()` had no
    outer boundary, so any exception there escaped as the interpreter's own exit 1:
    this tool's word for a FINDING. That is not cosmetic — the CI mismatch step
    (`cypher-diff.yml`) passes only on `rc == 1`, because 1 is the evidence that the
    harness CAN fail, so a crash satisfied the assertion the job exists for
    (`#13098` review P2).

    Arm (a) is the real reproducer, not a synthetic raise: `--bench 10 --bench-iters 0`
    reaches `statistics.median([])` inside `bench()` and raises `StatisticsError`.
    Arms (b)/(c) keep the boundary honest: it is not exception-type-specific, and it
    does not swallow a genuine verdict.
    """
    import statistics

    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})
    ids = [("p-null-content",), ("p-retired",), ("p-null-created",)]
    monkeypatch.setattr(harness, "run_cypher", lambda *_a, **_k: (ids, ["id"]))
    monkeypatch.setattr(harness, "run_sql", lambda *_a, **_k: (ids, ["id"]))
    argv = ["--graph-uri", "docker://:p@127.0.0.1:1",
            "--record-dsn", "postgresql://u:p@127.0.0.1/db"]
    bench_argv = [*argv, "--bench", "10", "--bench-iters", "0"]

    # (a) the real defect: median([]) inside bench() — must be 2 (could not run), never 1
    def median_of_nothing(*_a, **_k):
        return statistics.median([])

    monkeypatch.setattr(harness, "bench", median_of_nothing)
    assert harness.main([*bench_argv, "--candidate", "point-listing"]) == 2
    err = capsys.readouterr().err
    assert "could not run" in err, err
    assert "StatisticsError" in err, err
    assert 2 != 1, "the whole finding: a crash must not share the code that means 'different'"

    # (b) the boundary is not exception-type-specific
    def boom(*_a, **_k):
        raise RuntimeError("the driver changed its mind")

    monkeypatch.setattr(harness, "bench", boom)
    assert harness.main([*bench_argv, "--candidate", "point-listing"]) == 2
    assert "RuntimeError" in capsys.readouterr().err

    # (c) …and it does NOT absorb a verdict: a real difference is still 1
    monkeypatch.setattr(harness, "diff", lambda *_a, **_k: {
        "equal": False, "differences": [{"kind": "row-count"}],
        "columns": {"cypher": ["id"], "sql": ["id"]}, "fingerprint": "f"})
    assert harness.main([*argv, "--candidate", "point-listing", "--json"]) == 1


def test_a_listing_is_not_a_verdict(capsys):
    """`--list` prints candidates and compares nothing, so it is not exit 0 either."""
    assert harness.main(["--list"]) == harness.EXIT_NO_VERDICT
    assert "point-by-id" in capsys.readouterr().out


def test_every_declared_corpus_edge_exists_in_the_shared_corpus():
    """A `requires` name that no corpus row provides would refuse EVERY run."""
    ids = {row["id"] for row in harness.CORPUS}
    for name, candidate in harness.CANDIDATES.items():
        for edge in candidate.requires:
            assert edge in ids, f"{name} requires {edge!r}, which no corpus row provides"


def test_the_empty_result_is_compared_by_a_candidate_not_merely_declared(monkeypatch):
    """The EMPTY result must be an edge the harness actually asks both stores for.

    The docstring used to declare an empty-tenant edge that no candidate
    queried, so a port that turned an empty answer into an error or `None`
    would not have been caught (`#13098` review F2).
    """
    candidate = harness.CANDIDATES["point-by-absent-id"]
    # no `requires` entry can assert an ABSENCE, so the candidate's honesty rests
    # on the id being absent from every fixture — including a future corpus edit
    assert candidate.requires == ()
    assert candidate.params == {"id": harness.ABSENT_ID}
    assert harness.ABSENT_ID not in {row["id"] for row in harness.CORPUS}
    # zero rows on both sides, same columns: that IS the same answer
    result = harness.diff(candidate, [], ["id", "content", "point_kind"],
                          [], ["id", "content", "point_kind"], [])
    assert result["equal"] is True and result["row_count"] == {"cypher": 0, "sql": 0}
    # ⛔ an empty answer whose projection is MISSING is not the same answer — the
    # shape a port returning `None`/a bare empty instead of no-rows would have
    assert harness.diff(candidate, [], [], [],
                        ["id", "content", "point_kind"], [])["equal"] is False
    # and the CLI path reaches that verdict end to end: empty stores -> exit 0
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 0})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 0})
    monkeypatch.setattr(harness, "run_cypher",
                        lambda *_a, **_k: ([], ["id", "content", "point_kind"]))
    monkeypatch.setattr(harness, "run_sql",
                        lambda *_a, **_k: ([], ["id", "content", "point_kind"]))
    assert harness.main(["--graph-uri", "docker://:p@127.0.0.1:1",
                         "--record-dsn", "postgresql://u:p@127.0.0.1/db",
                         "--candidate", "point-by-absent-id"]) == 0


def test_a_missing_psql_is_could_not_run_not_a_difference(monkeypatch, capsys):
    """A setup failure must never be reported as `1` = "the two forms DIFFER".

    `bootstrap_record` called `subprocess.run(["psql", …])` outside any `try`, so
    a box without the client raised `FileNotFoundError`, escaped `main()`, and
    the interpreter exited **1** — this tool's code for a FINDING. The harness
    would report its own missing dependency as a porting defect (`#13098`
    review).
    """
    def _missing(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory", "psql")

    monkeypatch.setattr(harness.subprocess, "run", _missing)
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 2
    assert "could not run" in capsys.readouterr().err


def test_bootstrap_refuses_when_the_record_schema_never_materialises(monkeypatch, capsys):
    """`public.point` absent after the migrations is 2, never 0.

    The function used to `return 0` unconditionally, so `bootstrapped 0/85
    migrations` reported success and the caller's `rc != 0` guard was dead code.
    A partial apply IS tolerated (the record builds on the control-plane schema),
    but only while the schema the corpus needs exists.
    """
    class _Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    monkeypatch.setattr(harness.subprocess, "run", lambda *_a, **_k: _Proc())
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 2
    assert "no public.point" in capsys.readouterr().err


def test_bootstrap_tolerates_a_partial_apply_that_does_build_the_record(monkeypatch):
    """A file that fails does not stop the record from existing — and that is 0."""
    class _Proc:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    calls = {"n": 0}

    def _run(argv, **_k):
        calls["n"] += 1
        if "-tAc" in argv:                       # the to_regclass probe
            return _Proc(0, "public.point\n")
        if "-f" in argv and "0001_user_teams" in argv[-1]:
            return _Proc(1, "", "psql:…: ERROR: schema \"auth\" does not exist")
        return _Proc()

    monkeypatch.setattr(harness.subprocess, "run", _run)
    assert harness.bootstrap_record("postgresql://u:p@127.0.0.1/db") == 0
    assert calls["n"] > 1


def test_the_files_scanned_count_is_pinned_too(tmp_path):
    """§0 publishes "295 files scanned", which `files` (the files WITH a site) is not.

    `check()` compared only the keys it computed, so the doc's scanned count could
    drift by any amount while `--check` stayed green (`#13098` review P3).
    """
    result = census.census(("tortoise",))
    doc_text = (ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md").read_text(encoding="utf-8")
    assert "<!-- cypher-inventory:files_scanned = " in doc_text
    path = tmp_path / "doc.md"
    path.write_text(doc_text.replace(
        f"<!-- cypher-inventory:files_scanned = {result['files_scanned']} -->",
        f"<!-- cypher-inventory:files_scanned = {result['files_scanned'] + 1} -->", 1),
        encoding="utf-8")
    assert census.check(path, result) == 1
    # ⛔ AND it is reported as a COUNT MISMATCH, not as a pin the tool stopped
    # computing: dropping the key from `check()`'s `actual` still reddens through
    # the STALE arm, so `== 1` alone cannot tell the two apart (measured — that
    # mutant survived this assertion).
    import contextlib
    import io

    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        census.check(path, result)
    assert "DRIFT files_scanned" in captured.getvalue(), captured.getvalue()


def test_the_published_decomposition_names_the_measured_operands():
    """§0/§1 reconcile ARITHMETICALLY over operands, and the operands are the claim.

    ⛔ The failure this refuses is the exact shape `test_the_census_reconciles_
    arithmetically` was written against, one layer up: at the parent commit §0 read
    `= 1310 classified Cypher statements + 257 non-Cypher door calls`, and the
    census measured `1311` statement sites and `256` non-Cypher door calls. Both
    terms were off by ONE in OPPOSITE directions, so the line's own total still
    closed at 1,567 and every pin stayed green (`#13249` review round 13 F1, round
    14). Nothing could see it: `check()` reads only the HTML pins, and the
    reconciliation test recomputes from MEASURED values, never from this prose.

    The narrative in §0 and the `--all-surface` comparison table in §1 publish the
    same two quantities, so they are pinned by the same test — they were wrong at
    the same commit and for the same reason (the pins were re-cut, the prose was
    not). A re-cut that moves any of these numbers must move this line with it.
    """
    doc_text = (ROOT / "docs" / "architecture" / "CYPHER-INVENTORY.md").read_text(encoding="utf-8")
    result = census.census(("tortoise",))
    sites = len(result["sites"])
    non_cypher = result["non_cypher_door_calls"]
    candidates = sum(1 for s in result["sites"] if s.verdict.startswith("candidate"))

    # (a) §0's decomposition: `= N classified Cypher statements + M non-Cypher door calls`
    decomposition = re.search(
        r"^= ([\d,]+) +classified Cypher statements \+ ([\d,]+) non-Cypher door calls$",
        doc_text, re.MULTILINE)
    assert decomposition, "§0's decomposition line moved or changed shape; re-cut this pin with it"
    doc_sites = int(decomposition.group(1).replace(",", ""))
    doc_non_cypher = int(decomposition.group(2).replace(",", ""))
    assert (doc_sites, doc_non_cypher) == (sites, non_cypher), (
        "§0's decomposition states the deltas it explains, and it disagrees with the census: "
        f"doc={doc_sites}+{doc_non_cypher} measured={sites}+{non_cypher}")

    # (b) §0's narrative: `N of M product statements (P %)` — N is the candidate
    # count (plain READS), M is the statement sites, and P is DERIVED from them.
    narrative = re.search(
        r"([\d,]+) of ([\d,]+) product statements \(([\d.]+) %\)", doc_text)
    assert narrative, "§0's 'N of M product statements (P %)' line moved; re-cut this pin with it"
    doc_candidates = int(narrative.group(1).replace(",", ""))
    doc_statement_sites = int(narrative.group(2).replace(",", ""))
    doc_percent = float(narrative.group(3))
    assert (doc_candidates, doc_statement_sites) == (candidates, sites), (
        "§0's narrative count disagrees with the census: "
        f"doc={doc_candidates} of {doc_statement_sites} measured={candidates} of {sites}")
    assert doc_percent == round(candidates / sites * 100, 1), (
        f"§0's percentage is {doc_percent}, but {candidates}/{sites} is "
        f"{round(candidates / sites * 100, 1)}")

    # (c) §1's comparison table: the product-surface row, and the files carrying a site.
    table_row = re.search(
        r"^\| product surface \(this census\) \| \*\*(\d+)\*\* \| \*\*(\d+)\*\* \|",
        doc_text, re.MULTILINE)
    assert table_row, "§1's product-surface table row moved; re-cut this pin with it"
    assert (int(table_row.group(1)), int(table_row.group(2))) == (sites, result["files_with_sites"]), (
        "§1's comparison table disagrees with the census: "
        f"doc={table_row.group(1)} sites / {table_row.group(2)} files, "
        f"measured={sites} / {result['files_with_sites']}")


def test_a_corpus_row_that_lost_its_edge_property_is_refused(monkeypatch, capsys):
    """Presence is not the pin: the NULL that makes `p-null-created` an edge case is.

    A seeding change that keeps the row in both stores but gives it a
    `created_at` disarms the `null-order` mutation — the run still exits 1, for
    the wrong reason — while the presence pin stays green (`#13098` review P3).
    """
    ids = [row["id"] for row in harness.CORPUS]
    monkeypatch.setattr(harness, "seed_graph", lambda *_a, **_k: {"graph": "g", "points": 6})
    monkeypatch.setattr(harness, "seed_record", lambda *_a, **_k: {"points": 6})

    def _run(statement, *_a, **_k):
        # every presence question is answered YES; every PROPERTY question is
        # answered NO — exactly the shape a stripped property produces
        if "WHERE n." in statement or " IS NULL" in statement or " IS NOT NULL" in statement:
            return [], ["id"]
        return [(i,) for i in ids], ["id"]

    monkeypatch.setattr(harness, "run_cypher", lambda _u, _g, cypher, params: _run(cypher))
    monkeypatch.setattr(harness, "run_sql", lambda _d, sql, params: _run(sql))
    rc = harness.main(["--graph-uri", "docker://:p@127.0.0.1:1",
                       "--record-dsn", "postgresql://u:p@127.0.0.1/db",
                       "--candidate", "point-listing"])
    assert rc == 2
    assert "PROPERTY" in capsys.readouterr().err


class _FakeGraph:
    """A graph that reports its labels and its Point ids — nothing else is read."""

    def __init__(self, labels, ids):
        self.labels, self.ids = labels, ids

    def query(self, statement):
        from types import SimpleNamespace

        if "UNWIND labels" in statement:
            return SimpleNamespace(result_set=[(label,) for label in self.labels])
        return SimpleNamespace(result_set=[(i,) for i in self.ids])


def test_a_graph_this_harness_did_not_build_is_refused_not_wiped():
    """The seed DETACH DELETEs every `:Point` in its target, so the target is checked.

    Two refusals, both raising (which `main` turns into exit 2): a node label the
    harness never writes, and a Point id it never wrote — the shapes a mistyped
    `--graph-name`, or a target inherited from `TORTOISE_DB_URI`, would produce
    (`#13098` review P2-3).
    """
    with pytest.raises(RuntimeError, match="Operator"):
        harness._refuse_a_foreign_graph(_FakeGraph({"Point", "Operator"}, []), "real-graph")
    with pytest.raises(RuntimeError, match="never wrote"):
        harness._refuse_a_foreign_graph(_FakeGraph({"Point"}, ["a-real-point-id"]), "real-graph")
    # its own corpus graph, and its own bench padding, is the one shape it accepts
    harness._refuse_a_foreign_graph(
        _FakeGraph({"Point"}, [row["id"] for row in harness.CORPUS] + ["p-pad-00001"]),
        "cypher-diff",
    )


@pytest.mark.parametrize("knob", [None, "none", "0"],
                         ids=["unset", "opt-out-none", "opt-out-zero"])
def test_the_seed_refuses_a_foreign_graph_before_it_deletes(monkeypatch, knob):
    """The guard is WIRED into `seed_graph`, not merely available.

    ⛔ Calling `_refuse_a_foreign_graph` directly (the test above) stays green if
    the call site is deleted — measured: replacing it with `pass` survived. This
    drives `seed_graph` through a stand-in `falkordb` so a missing call REDDENS.

    ⛔ The stand-in `from_url` takes `**kwargs` because the construction now
    carries the product's read bound (#13211 governs this fix; #13140 is the
    defect's origin) — and it ASSERTS the bound rather than accepting it
    blindly: a stub that swallowed `**kwargs` would leave the seed's socket
    policy unpinned here, and `socket_timeout` absent from the received kwargs
    reds this test (the shape the seed had before the fix).

    ⛔ IT ASSERTS THE BOUND WAS *PASSED*, NOT THE VALUE IT RESOLVED TO (#13212
    review P2-1). The first cut asserted `seen["socket_timeout"] is not None`,
    which is the RESOLVED value: `_bounded_server_socket_kwargs()` returns
    `socket_timeout=None` for every spelling of the DOCUMENTED opt-out
    (`TORTOISE_DB_SOCKET_TIMEOUT` = `none`/`off`/`false`/`0`/any value ≤ 0, see
    `tortoise/projection/__init__.py`), a configuration the sibling suite pins
    as CORRECT — so an operator shell that exported the opt-out reddened this
    row on a tree that obeyed #3065, accusing the seed of a defect it did not
    have. The presence of the kwarg is the thing this pin is about; its VALUE is
    the operator's business, asserted below only for the two documented shapes.
    """
    from types import SimpleNamespace

    # The operator's own shell must not decide this row's verdict — the repo's
    # established shape for that (`tests/test_cache_eviction_census.py`, and
    # `_graph_viz_connection_module` in `test_bounded_raw_clients_3065.py`).
    monkeypatch.delenv("TORTOISE_DB_SOCKET_TIMEOUT", raising=False)
    monkeypatch.delenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", raising=False)
    if knob is not None:
        monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", knob)

    client = SimpleNamespace(select_graph=lambda name: _FakeGraph({"Point", "Operator"}, []))
    seen: dict = {}

    def _from_url(url, **kwargs):
        seen.update(kwargs)
        return client

    monkeypatch.setitem(sys.modules, "falkordb",
                        SimpleNamespace(FalkorDB=SimpleNamespace(from_url=_from_url)))
    with pytest.raises(RuntimeError, match="Operator"):
        harness.seed_graph("docker://:p@127.0.0.1:1", "real-graph")
    assert "socket_timeout" in seen, (
        "the seed's client was constructed with NO read bound (#3065/#8118): "
        f"from_url received {sorted(seen)}")
    if knob is None:
        assert seen["socket_timeout"] > 0, (
            "the seed's client received `socket_timeout=None` with no opt-out "
            f"exported (#3065/#8118): from_url received {seen}")
    else:
        assert seen["socket_timeout"] is None, (
            f"TORTOISE_DB_SOCKET_TIMEOUT={knob} is the documented unbounded "
            "opt-out and the resolver's answer must reach the seed too — this "
            f"arm exists so the pin above cannot accuse correct code: {seen}")


@pytest.mark.parametrize("axis", [None, "--graph-uri", "--record-dsn"],
                         ids=["both-omitted", "graph-uri", "record-dsn"])
def test_the_store_targets_are_named_never_inherited_from_the_environment(monkeypatch, capsys, axis):
    """⛔ No ambient `TORTOISE_DB_URI` / `TORTOISE_RECORD_DSN` default — PER AXIS.

    Both targets defaulted to the PRODUCT's own stores while the seed DELETEs the
    target graph's `:Point` rows and rewrites its tenant's `public.point` rows, so
    a bare invocation in a developer shell could wipe the store that shell pointed
    at (`#13098` review P2-3). They are command-line only now — including when the
    environment DOES name a store.

    ⛔ WHY THIS IS PARAMETRISED AND NOT A SINGLE `main([...])` CALL. The guard it
    exercises is `if not args.graph_uri or not args.record_dsn`, so with BOTH
    arguments omitted it fires on whichever axis is still empty — and a regression
    that restores the OTHER axis from the environment is then invisible to the
    assertion (`#13098` review F13; measured: reintroducing the ambient
    `--graph-uri` default left this test GREEN, because the empty `--record-dsn`
    triggered the refusal first). Each axis is therefore tested with the OTHER
    target supplied EXPLICITLY, so the only thing that can stop the seed is the
    axis under test. The `both-omitted` case is kept because it is the guard's own
    contract and it costs nothing.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@127.0.0.1:6379")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://postgres:x@127.0.0.1:5432/db")
    # ⛔ the refusal must happen BEFORE any store is touched: restoring EITHER
    # ambient default is invisible to an exit-code-only assertion (the seed fails
    # on an unreachable lane and returns 2 as well — measured, that mutant
    # survived), so the stubs below fail loudly instead.
    monkeypatch.setattr(harness, "seed_graph",
                        lambda *_a, **_k: pytest.fail("must not seed from an ambient target"))
    monkeypatch.setattr(harness, "seed_record",
                        lambda *_a, **_k: pytest.fail("must not seed from an ambient target"))
    # The axis NOT under test is supplied explicitly, so the sibling arm of the
    # `or` cannot answer for it.
    other = {
        "--graph-uri": ["--record-dsn", "postgresql://u:p@127.0.0.1:5432/db"],
        "--record-dsn": ["--graph-uri", "docker://:p@127.0.0.1:1"],
    }.get(axis, [])
    assert harness.main(["--candidate", "point-by-id", *other]) == 2
    assert "could not run" in capsys.readouterr().err
