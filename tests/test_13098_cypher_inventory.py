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
    # ⛔ The THIRD reconciliation line the report prints and the doc claims —
    # `occurrences sharing a line with a CALL` = regex occurrences on call lines
    # MINUS the AST's own node count. Both sides are measured independently (a
    # line regex vs an AST walk), so this can fail: a comment mentioning a door
    # beside a real call reddens it. The assertion this replaced was
    # `result["ast_door_calls"] == ast_door_calls`, which compared a dict field
    # with the exact expression that produced it — dead by construction
    # (`#13098` review P2-4).
    assert (totals["lines_door_calls"] - totals["door_occurrences_without_a_call"]
            - ast_door_calls) == 0


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


def test_the_seed_refuses_a_foreign_graph_before_it_deletes(monkeypatch):
    """The guard is WIRED into `seed_graph`, not merely available.

    ⛔ Calling `_refuse_a_foreign_graph` directly (the test above) stays green if
    the call site is deleted — measured: replacing it with `pass` survived. This
    drives `seed_graph` through a stand-in `falkordb` so a missing call REDDENS.
    """
    from types import SimpleNamespace

    client = SimpleNamespace(select_graph=lambda name: _FakeGraph({"Point", "Operator"}, []))
    monkeypatch.setitem(sys.modules, "falkordb",
                        SimpleNamespace(FalkorDB=SimpleNamespace(from_url=lambda url: client)))
    with pytest.raises(RuntimeError, match="Operator"):
        harness.seed_graph("docker://:p@127.0.0.1:1", "real-graph")


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
