"""#2944 — the destructive-wipe guard must be STRUCTURAL, not name-based.

Before #2944 the safety of ``MATCH (n) DETACH DELETE n`` in ``rebuild_all``
rested on (a) a graph-NAME prefix check and (b) a mutable ``_skip_guard``
boolean. A graph renamed to look disposable passed (a) in server mode, and
embedded mode skipped the check entirely. This file pins the replacement:

  L1 (structural) — ``rebuild_all``/``rebuild`` refuse unless the CALLER
      passed ``confirm_destructive=True``. Default = refuse, for every
      caller, embedded included. Nothing can authorize a wipe by accident.
  L2 (defence in depth) — the disposable-graph name check STILL applies to
      every bulk DETACH DELETE that reaches the guarded handle, so a
      non-test server graph is refused even when L1 was satisfied.

Coverage map (per the issue's acceptance criteria):
  (a) a non-disposable graph is refused even if named ``test_...``-like, or
      when the old ``_skip_guard`` bypass would have allowed it
  (b) the legitimate disposable paths still work
  (c) ``_skip_guard`` (or its replacement) cannot be flipped from ordinary
      production code — pinned at four depths: the identifier is gone from
      both query paths (bytecode), the token is keyword-only with a refusing
      default, every production call site passes the literal ``True`` (AST),
      and the L1 assertion BODY itself refuses without it plus the
      ``_wipe_all_nodes`` chokepoint consults it before wiping (the two tests
      that red if enforcement is neutered).

The unit tests use a recording fake graph handle so they run in BOTH the
embedded carve-out lane and the docker lane without a live server. The
end-to-end allow tests use a real embedded FalkorDB in ``tmp_path``.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.log import EventLog
from tortoise.projection import (
    _SNAPSHOT_SECTIONS,
    FalkorProjection,
    WipePreconditionRefused,
    _GuardedGraph,
    _write_prewipe_snapshot,
    prewipe_snapshot_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# A path that must never be touched: L1 refusals fire before ANY graph or
# filesystem I/O, so tests that reach them deliberately use a bogus path to
# prove no work happened.
NO_IO_DIR = "/nonexistent/2944-destructive-wipe-guard"

# Graph names the issue names explicitly as "real-looking" — these must be
# refused even WITH the L1 token (L2 defence in depth). Bare "test" is here
# because the historical L2 check is `startswith(("test_", "tortoise_test"))`
# — "test" never passed it (see tests/test_wipe_server.py).
NON_DISPOSABLE_NAMES = [
    "prod_tortoise", "tortoise_prod", "team_x", "production", "test",
]

# Names that look disposable to L2 (the historical guard) — but a name alone
# must no longer authorize a wipe, so these are refused without the L1 token.
TEST_LOOKING_NAMES = ["test_prod_tortoise", "test_team_x", "tortoise_test",
                     "tortoise_test_delta"]


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _RecordingGraph:
    """Minimal fake FalkorDB graph handle: records queries, returns empty sets.

    Count queries return 0 so ``rebuild_all``'s trailing count reads work; the
    wipe is identified by its Cypher text, never executed.
    """

    def __init__(self):
        self.queries: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(cypher)
        if "COUNT(" in cypher.upper():
            return _Result([[0]])
        return _Result([])

    def wipes(self) -> list[str]:
        return [q for q in self.queries if "DETACH DELETE" in q.upper()]


class _EmptyLog:
    """A log with no events — lets guard tests reach the wipe without I/O."""

    def read_all(self):
        return []


def _bare_projection(*, graph_name: str, embedded: bool) -> FalkorProjection:
    """A FalkorProjection with no DB — enough to exercise the guard paths.

    The refusal tests assert the guard fires BEFORE any graph I/O, so no
    connection is needed. The allow test with a fake graph only reads counts.
    """
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = embedded
    proj._graph_name = graph_name
    proj.graph_name = graph_name
    # #2943 (rebased from main): rebuild_all derives the pre-wipe sidecar path
    # from `self._path` (embedded file) before the L2 guard fires, so a bare
    # projection must carry it — None = server/no file.
    proj._path = None
    proj.g = _RecordingGraph()
    return proj


# ── (c) the old bypass is dead: no attribute can authorize a wipe ─────────


def _referenced_names(fn) -> tuple[set, set]:
    """Names/literals the compiled function body actually references.

    Compiled metadata, not source text — so a comment mentioning the removed
    bypass cannot make this pin pass vacuously."""
    return set(fn.__code__.co_names), set(fn.__code__.co_consts)


def test_projection_has_no_skip_guard_field():
    """The mutable ``_skip_guard`` bypass was REMOVED (#2944), not defaulted.

    Pins the two guarded query paths at the bytecode level: neither may
    reference ``_skip_guard`` again. A reintroduced read would silently
    re-open the bypass the issue is about."""
    for fn in (FalkorProjection.query, _GuardedGraph.query):
        names, consts = _referenced_names(fn)
        assert "_skip_guard" not in names, (
            f"{fn.__qualname__} reads _skip_guard — the bypass was replaced by "
            f"the per-call confirm_destructive token (#2944)"
        )
        assert "_skip_guard" not in consts, (
            f"{fn.__qualname__} looks up _skip_guard — the bypass was replaced "
            f"by the per-call confirm_destructive token (#2944)"
        )


def test_setting_skip_guard_attribute_does_not_authorize_wipe():
    """(a)/(c) ``proj._skip_guard = True`` must have NO effect.

    The pre-#2944 bypass is exactly this assignment; it must not authorize a
    wipe on a test-looking graph, and must not let a raw bulk DETACH DELETE
    through the guarded handle on a non-test server graph."""
    proj = _bare_projection(graph_name="test_prod_tortoise", embedded=False)
    proj._skip_guard = True  # the old bypass — must be inert now
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)
    assert proj.g.wipes() == [], "no wipe may run while L1 refused"

    # Raw guarded-handle path: the attribute must not open L2 either.
    raw = _bare_projection(graph_name="prod_tortoise", embedded=False)
    raw._skip_guard = True
    guarded = _GuardedGraph(_RecordingGraph(), raw)
    with pytest.raises(RuntimeError, match="non-test graph"):
        guarded.query("MATCH (n) DETACH DELETE n")

    # Both query PATHS are pinned behaviourally, not just at the bytecode
    # level: `FalkorProjection.query` carries the same no-bypass contract.
    # The handle here is deliberately BARE (not `_GuardedGraph`) so the
    # assertion under test is this method's own, not the wrapper's.
    proj_query = _bare_projection(graph_name="prod_tortoise", embedded=False)
    proj_query._skip_guard = True
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj_query.query("MATCH (n) DETACH DELETE n")


def test_replacement_is_a_call_site_parameter_not_ambient_state():
    """(c) The only authorization is a per-call argument whose default is False.

    There is no module flag, env var, or constructor switch to flip: the
    signature itself is the contract."""
    for meth in (FalkorProjection.rebuild_all, FalkorProjection.rebuild):
        params = inspect.signature(meth).parameters
        assert "confirm_destructive" in params, f"{meth.__name__} has no token"
        p = params["confirm_destructive"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{meth.__name__}: token must be keyword-only (explicit at the call site)"
        )
        assert p.default is False, (
            f"{meth.__name__}: default must be False (refuse) — a default-allow "
            f"would let a new caller wipe a graph by forgetting the token"
        )


def _rebuild_call_nodes(tree) -> list:
    """Every ``*.rebuild(...)`` / ``*.rebuild_all(...)`` call node in a PARSED tree.

    Takes the tree rather than the path on purpose: a caller that builds a
    parent map from one parse and node identity-tests against another silently
    finds nothing (and would report every site as unenclosable)."""
    sites = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in ("rebuild", "rebuild_all"):
            sites.append(node)
    return sites


def _rebuild_call_sites(path: Path) -> list:
    """Every ``*.rebuild(...)`` / ``*.rebuild_all(...)`` call AST node in a file.

    An AST walk, not a substring scan: the token must be present on the CALL,
    so a comment or docstring mentioning ``confirm_destructive=True`` cannot
    make this pin pass vacuously while the call itself omits it."""
    return _rebuild_call_nodes(
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))


def test_production_entry_points_thread_the_token():
    """(c) Every production call site must pass the token — no silent default.

    A caller reaching ``rebuild_all``/``rebuild`` without opting in gets the
    refusal, so the shipped entry points must opt in explicitly at the call.
    Per-call-site AST check: dropping the argument (and thereby relying on the
    default) reds here even if the token text survives elsewhere in the
    file."""
    checks = {
        REPO_ROOT / "tortoise" / "__main__.py": "tortoise rebuild CLI",
        REPO_ROOT / "tortoise" / "migrate_db.py": "migrate_db",
        REPO_ROOT / "tortoise" / "consistency.py": "recover_from_log",
        REPO_ROOT / "validation" / "validate_tortoise_ep.py": "validation script",
    }
    for path, label in checks.items():
        sites = _rebuild_call_sites(path)
        assert sites, f"{label}: no rebuild/rebuild_all call found in {path.name}"
        for call in sites:
            token = next(
                (kw for kw in call.keywords if kw.arg == "confirm_destructive"),
                None,
            )
            assert token is not None, (
                f"{label}: {path.name}:{call.lineno} reaches the wipe without "
                f"the explicit confirm_destructive token"
            )
            assert isinstance(token.value, ast.Constant) and token.value.value is True, (
                f"{label}: {path.name}:{call.lineno}: confirm_destructive must be "
                f"the literal True (a variable could be flipped at runtime)"
            )


# Token-less call sites in tests/ that are correct BY DESIGN, keyed by
# (relative path, enclosing function) so the list survives edits above it — and
# carrying the EXPECTED count, so a second token-less call added to an
# already-listed function still reds. An entry with no matching call reds too, so
# the list cannot rot into silent coverage.
_TOKEN_LESS_BY_DESIGN = {
    ("tests/test_destructive_wipe_guard.py",
     "test_setting_skip_guard_attribute_does_not_authorize_wipe"): (
        1, "asserts the refusal: an instance attribute must not authorize a wipe"),
    ("tests/test_destructive_wipe_guard.py",
     "test_test_looking_name_alone_is_not_enough"): (
        1, "asserts the refusal: a test-looking graph name is not the gate"),
    ("tests/test_destructive_wipe_guard.py",
     "test_embedded_without_token_is_refused"): (
        1, "asserts the refusal on the embedded lane"),
    ("tests/test_ops_safety.py",
     "test_inmemory_rebuild_refuses_a_torn_removal_tail"): (
        2, "InMemoryProjection — no graph to wipe"),
    ("tests/test_projection.py",
     "test_inmemory_rebuild"): (
        1, "InMemoryProjection — no graph to wipe"),
}


def test_test_call_sites_are_token_threaded_or_allowlisted():
    """(f) The pin above stops at the production boundary.

    Main added ``tests/test_4503_edge_relationship_accounting.py`` (2c7646abe,
    issue #4503) after this branch's call-site sweep ran. Its
    ``rebuild_all`` call is on a graph-backed projection, so the
    production-only pin walked past it and CI would have gone red on a file
    nobody had looked at. This is the same AST rule applied across tests/,
    with one difference: a test may pass the literal ``False`` to assert the
    refusal, so what is required here is a literal boolean token — not merely
    the presence of an argument (a variable could be truthy at runtime)."""
    counts, unexplained = {}, []
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {child: node for node in ast.walk(tree)
                   for child in ast.iter_child_nodes(node)}
        for call in _rebuild_call_nodes(tree):
            token = next((kw for kw in call.keywords
                          if kw.arg == "confirm_destructive"), None)
            if (token is not None
                    and isinstance(token.value, ast.Constant)
                    and isinstance(token.value.value, bool)):
                continue
            name, node = "<module>", call
            while node is not None:
                node = parents.get(node)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = node.name
                    break
            key = (rel, name)
            if key in _TOKEN_LESS_BY_DESIGN:
                counts[key] = counts.get(key, 0) + 1
            else:
                unexplained.append(f"{rel}:{call.lineno}")
    assert not unexplained, (
        "these call sites are matched by name (rebuild/rebuild_all) and carry "
        "no literal boolean confirm_destructive token, and are not "
        "allowlisted: " + ", ".join(unexplained) + " — thread a literal "
        "True/False, or add them to _TOKEN_LESS_BY_DESIGN with the reason "
        "(including when the call is not a projection wipe at all)")
    wrong = [
        f"{rel}:{fn} expected {want} token-less call(s), found "
        f"{counts.get((rel, fn), 0)}"
        for (rel, fn), (want, _why) in sorted(_TOKEN_LESS_BY_DESIGN.items())
        if counts.get((rel, fn), 0) != want
    ]
    assert not wrong, (
        "these allowlist entries no longer describe what they claim (the call "
        "moved, gained the token, or a new one appeared): " + "; ".join(wrong))


# ── (a) non-disposable graphs are refused ─────────────────────────────────


@pytest.mark.parametrize("name", TEST_LOOKING_NAMES)
def test_test_looking_name_alone_is_not_enough(name):
    """(a) A name that passes the historical L2 check must STILL be refused
    when the caller did not opt in — names are no longer load-bearing."""
    proj = _bare_projection(graph_name=name, embedded=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR, confirm_destructive=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)  # default = refuse
    assert proj.g.queries == [], "guard must fire before any graph I/O"


@pytest.mark.parametrize("name", NON_DISPOSABLE_NAMES)
def test_non_disposable_name_refused_even_with_token(name, tmp_path):
    """(a) L2 still refuses a real-looking server graph even when L1 is
    satisfied — the guard was not weakened into a rubber stamp."""
    proj = _bare_projection(graph_name=name, embedded=False)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert proj.g.wipes() == []


def test_non_disposable_name_refused_on_rebuild_too():
    """(a) ``rebuild(log)`` shares the L1/L2 chokepoints — same refusal."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild(object(), confirm_destructive=False)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.rebuild(_EmptyLog(), confirm_destructive=True)
    assert proj.g.wipes() == []


def test_embedded_without_token_is_refused():
    """(a) The old bypass would have allowed this: embedded mode was exempt
    from the guard entirely. \"Embedded\" means isolated, not unsupervised."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj.rebuild_all(NO_IO_DIR)
    assert proj.g.wipes() == [], "embedded wipe must not run without the opt-in"


def test_raw_bulk_wipe_still_refused_on_non_test_server_graph():
    """L2 is unchanged for hand-written Cypher that never went through L1."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    proj.g = _GuardedGraph(_RecordingGraph(), proj)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.query("MATCH (n) DETACH DELETE n")
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.g.query("MATCH (n) DETACH DELETE n")


def test_targeted_delete_is_still_allowed():
    """No false positives: a targeted delete (property map) is not a bulk wipe."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n:Point {id:$id}) DETACH DELETE n", params={"id": "x"})
    assert len(guarded._g.queries) == 1


# ── #3037/#8223: the property-map exemption is anchored to a pattern element ─


@pytest.mark.parametrize("statement", [
    "MATCH (n) DETACH DELETE n",                              # the plain wipe
    "MATCH (n) DETACH DELETE n // {",                         # brace in a // comment
    "MATCH (n) DETACH DELETE n /* { */",                      # brace in a /* */ comment
    "CALL { MATCH (n) DETACH DELETE n }",                     # CALL subquery delimiter
    "MATCH (n) DETACH DELETE n WHERE id(n) = '{'",            # brace in a string
    "MATCH (n) DETACH DELETE n WHERE {",                      # stray trailing brace
    "MATCH (m) WITH {a:1} AS x DETACH DELETE m",              # top-level map literal
    "CALL { MATCH (n:Point {id:1}) RETURN n } MATCH (m) "
    "DETACH DELETE m",                                        # map inside a CALL block
    # #8223: an EXPRESSION map literal is not a pattern property map — the
    # bracket before the brace is a function call / list / grouping, not a
    # node/relationship pattern, so it must not exempt the wipe.
    "UNWIND [{a:1}] AS x MATCH (n) DETACH DELETE n",
    "MATCH (n) WITH n, properties({a:1}) AS p DETACH DELETE n",
    "MATCH (n) WITH n, [{a:1}] AS xs DETACH DELETE n",
    "MATCH (n) DETACH DELETE n RETURN keys({a:1})",
    "MATCH (n) WHERE ({a:1}).a = 1 DETACH DELETE n",
    "MATCH (n) WHERE size(keys({x:1})) > 0 DETACH DELETE n",
    "MATCH (n) DETACH DELETE n RETURN coalesce({a:1},{b:2})",
    "MATCH (n) DETACH DELETE n RETURN [x IN [{a:1}] | x]",
    # A map PROJECTION (`n {.a}`) is an expression, not a pattern property map.
    "MATCH (n) DETACH DELETE n RETURN [x {.a}]",
])
def test_non_property_map_brace_does_not_exempt_a_wipe(statement):
    """#3037/#8223: a `{` that is not a pattern property map must not make a
    whole-graph wipe look targeted. Before the fix, every brace-bearing
    statement here returned False ("targeted") because the classifier was a
    bare ``"{" in cypher`` test (the expression-map cases survived the first
    #3037 pass because its regex accepted ANY ``(``/``[`` before the brace), so
    on the raw-query lane — where L2 is the only layer — the wipe reached a
    production server graph unguarded. The first parameter (the plain wipe with
    no brace) is the non-discriminating control: it returned True pre-fix and
    must keep returning True."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is True, statement


@pytest.mark.parametrize("statement", [
    "MATCH (n) DETACH DELETE n WHERE x = 'foo({",            # unterminated '
    "MATCH (n) DETACH DELETE n WHERE x = \"foo({",          # unterminated "
    "MATCH (n) DETACH DELETE n WHERE x = 'foo(n {a:1})",     # pattern-looking map inside a stray string
    "MATCH (n) DETACH DELETE n /* (x { ",                      # unterminated /* block
])
def test_unterminated_literal_or_comment_fails_closed(statement):
    """#3037: masking removes only TERMINATED literals/comments, so a malformed
    statement keeps its stray quote and a `{` after it would still read as a
    pattern property map. An unreadable statement is not evidence of targeting:
    the classifier must fail closed (bulk) rather than exempt the wipe. All four
    returned False pre-fix (the stray bracket looked like a pattern)."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is True, statement


@pytest.mark.parametrize("statement", [
    "MATCH (n) DETACH DELETE n RETURN [x {.a}]",            # projection in a list
    "MATCH (n) WITH [x {.a}] AS xs DETACH DELETE n",         # projection in a WITH list
])
def test_map_projection_brace_is_not_a_property_map(statement):
    """A map projection (`n {.a}`) is an expression, so its brace is not a
    pattern property map. The list form matched the first-pass regex
    (``[x {``) and so returned False pre-fix."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is True, statement


@pytest.mark.parametrize("statement", [
    "MATCH (n:Point {id:$id}) DETACH DELETE n",
    "MATCH (n:Meta {key:$key}) DETACH DELETE n",
    "MATCH (n:Point) WHERE n.id = $id DETACH DELETE n",
    "MATCH (a)-[r:IMPL {idx:0}]->(b) DETACH DELETE r",
])
def test_pattern_property_map_still_exempts_a_targeted_delete(statement):
    """Indicator (3): the existing targeted-delete behaviour is unchanged."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is False, statement


# ── #3007: label/LIMIT-scoped and `IN [..]` deletes are NOT whole-graph wipes ─
#
# Pre-fix every statement in the first table returned True (bulk): the
# classifier only exempted a property map or a WHERE reference, and it did not
# recognize a label on the deleted variable, a LIMIT bound, or `IN [list]`. That
# false positive made ``event_store.purge_overflow`` raise on every non-test
# server graph, so the per-team event cap was a silent no-op on hosted graphs.

@pytest.mark.parametrize("statement", [
    # The product offender verbatim (event_store.purge_overflow) — label AND limit.
    "MATCH (n:GraphEvent) WITH n ORDER BY n.seq ASC LIMIT $overflow "
    "DETACH DELETE n RETURN count(*)",
    # The issue's indicator (2) form.
    "MATCH (n:Label) WITH n LIMIT 10 DETACH DELETE n",
    # Label alone, no limit.
    "MATCH (n:Label) DETACH DELETE n",
    # A label PREDICATE is the same scoping signal.
    "MATCH (n) WHERE n:Point DETACH DELETE n",
    # LIMIT alone, no label (the WITH must carry the deleted variable).
    "MATCH (n) WITH n ORDER BY n.seq ASC LIMIT $overflow DETACH DELETE n",
    # Relationship-type scoping — the INSTANTIATES migration in graph-scripts.
    "MATCH (e:Event)-[r:INSTANTIATES]->(o:Object) "
    "MERGE (e)-[:aboutObject]->(o) DETACH DELETE r",
    # #3007 additional case: the list form of IN, with non-dotted expressions.
    "MATCH (n) WHERE id(n) IN [1,2] DETACH DELETE n",
    "MATCH (n) WHERE labels(n)[0] IN ['Point','Meta'] DETACH DELETE n",
    # Every target scoped => scoped, even with several targets.
    "MATCH (a:Point), (b:Meta) DETACH DELETE a, b",
])
def test_scoped_delete_is_not_a_bulk_wipe(statement):
    """Indicator (2): a label/LIMIT-scoped or `IN [..]`-scoped delete is a
    subgraph maintenance delete, not a whole-graph wipe."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is False, statement


@pytest.mark.parametrize("statement", [
    # Genuine wipe — indicator (3): no targeting construct at all.
    "MATCH (n) DETACH DELETE n",
    "MATCH (n) DETACH DELETE n RETURN count(*)",
    # DISCRIMINATING: the scope is on a DIFFERENT variable. ``m``/``n`` is
    # bound by a bare MATCH after the label/limit and is deleted unfiltered, so
    # each deletes every node. A whole-statement substring test would wrongly
    # exempt both; the per-target check must not.
    "MATCH (x:Foo) WITH x MATCH (n) DETACH DELETE n",
    "MATCH (n) WITH n LIMIT 10 MATCH (m) DETACH DELETE m",
    # A label predicate on a DIFFERENT variable scopes nothing it deletes.
    "MATCH (x) WHERE x:Foo MATCH (n) DETACH DELETE n",
    # A scoped and an unscoped target in one clause: the unscoped ``m`` still
    # means every node. ``all``, never ``any``.
    "MATCH (n:Point), (m) DETACH DELETE n, m",
    "MATCH (n)-[r:X]->(m:Anything) DETACH DELETE n, m",
    # A label that lives only in TEXT is not a label (masked before the scan).
    "MATCH (n) DETACH DELETE n // (n:GraphEvent)",
    "MATCH (n) DETACH DELETE n RETURN '(n:GraphEvent)'",
    # A LIMIT AFTER the delete cannot bound the rows it deletes.
    "MATCH (n) DETACH DELETE n WITH n LIMIT 10",
    # No parseable target at all => fail closed.
    "MATCH (n) DETACH DELETE",
])
def test_genuine_or_wrongly_scoped_wipe_is_still_bulk(statement):
    """Indicator (3) + the adversarial edge: a label/LIMIT that does not scope
    the deleted variable must NOT exempt a whole-graph wipe."""
    from tortoise.projection import _is_bulk_wipe

    assert _is_bulk_wipe(statement) is True, statement


class _CountingGraph:
    """Fake FalkorDB handle for the retention purge: a count read returns
    ``total`` and the overflow delete reports ``deleted``, so ``purge_overflow``
    can run to completion without a live server."""

    def __init__(self, total: int, deleted: int):
        self.total = total
        self.deleted = deleted
        self.queries: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(cypher)
        if "DETACH DELETE" in cypher.upper():
            return _Result([[self.deleted]])
        if "COUNT(" in cypher.upper():
            return _Result([[self.total]])
        return _Result([])

    def wipes(self) -> list[str]:
        return [q for q in self.queries if "DETACH DELETE" in q.upper()]


@pytest.mark.parametrize("name", ["tortoise", "team_42", "prod_tortoise"])
def test_purge_overflow_runs_on_a_non_test_server_graph(name):
    """Indicator (1): the per-team event cap must actually run on a real
    server graph. Pre-fix ``purge_overflow``'s label-scoped delete was
    classified bulk, L2 refused it, and BOTH callers swallowed the exception
    (hosted sweeper at DEBUG, SDK hook at WARNING) — the cap was silently dead.
    """
    from tortoise.event_store import purge_overflow

    proj = _bare_projection(graph_name=name, embedded=False)
    proj.g = _GuardedGraph(_CountingGraph(total=5, deleted=3), proj)
    deleted = purge_overflow(proj, max_events=2)
    assert deleted == 3, "the overflow delete must execute and report its count"
    assert proj.g._g.wipes(), "the retention delete never reached the graph"


@pytest.mark.parametrize("statement", [
    "MATCH (n:GraphEvent) WITH n ORDER BY n.seq ASC LIMIT $overflow "
    "DETACH DELETE n RETURN count(*)",
    "MATCH (n:GraphEvent) WHERE n.ts < $cutoff "
    "WITH n LIMIT 10000 DETACH DELETE n RETURN count(*)",
    "MATCH (n) WHERE id(n) IN [1,2] DETACH DELETE n",
])
def test_scoped_delete_reaches_the_graph_on_a_non_test_server_graph(statement):
    """Indicator (1), through BOTH L2 call sites: the classifier verdict must
    reach ``_assert_test_graph`` — a scoped delete is not refused."""
    proj = _bare_projection(graph_name="team_x", embedded=False)
    proj.g = _GuardedGraph(_RecordingGraph(), proj)
    proj.query(statement)
    proj.g.query(statement)
    assert len(proj.g._g.queries) == 2, "a scoped delete must reach the graph"


def test_purge_overflow_is_still_refused_without_a_target_scope():
    """Indicator (3), at the L2 call sites: a genuine whole-graph wipe is still
    refused on a non-test server graph after the #3007 narrowing."""
    proj = _bare_projection(graph_name="team_x", embedded=False)
    proj.g = _GuardedGraph(_RecordingGraph(), proj)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.g.query("MATCH (n) DETACH DELETE n")
    assert proj.g._g.wipes() == [], "the refused wipe must not reach the graph"


@pytest.mark.parametrize("statement", [
    "MATCH (n) DETACH DELETE n // {",
    "CALL { MATCH (n) DETACH DELETE n }",
    "UNWIND [{a:1}] AS x MATCH (n) DETACH DELETE n",
    "MATCH (n) DETACH DELETE n RETURN keys({a:1})",
])
def test_spoofed_wipe_is_refused_by_the_guard(statement):
    """The issue's named forms AND the #8223 expression-map forms, end-to-end
    through BOTH L2 call sites on a non-test SERVER graph: the classifier
    verdict must reach ``_assert_test_graph``, not merely the unit classifier."""
    proj = _bare_projection(graph_name="prod_tortoise", embedded=False)
    proj.g = _GuardedGraph(_RecordingGraph(), proj)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.query(statement)
    with pytest.raises(RuntimeError, match="non-test graph"):
        proj.g.query(statement)
    assert proj.g.wipes() == [], "a refused wipe must not reach the graph"


# ── (b) the legitimate disposable paths still work ────────────────────────


@pytest.mark.parametrize("name", TEST_LOOKING_NAMES)
def test_server_test_graph_with_token_is_allowed(name, tmp_path):
    """(b) A disposable (test-named) server graph wipes with the token."""
    proj = _bare_projection(graph_name=name, embedded=False)
    counts = proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert proj.g.wipes() == ["MATCH (n) DETACH DELETE n"]
    assert counts["nodes"] == 0 and counts["events"] == 0


def test_embedded_graph_with_token_is_allowed(tmp_path, monkeypatch):
    """(b) The real embedded path (CLI / migrate target) still wipes + replays.

    This is the end-to-end legitimate case: a real FalkorDB in a temp dir, a
    real JSONL log, the token passed explicitly."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)  # force embedded
    db_path = str(tmp_path / "guard_allow.db")
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    log = EventLog(log_dir / "events.jsonl")
    for i in range(2):
        log.append({
            "type": "PointAdded",
            "point": {"id": f"guard-{i}", "content": f"c{i}", "context": "t"},
        })

    proj = FalkorProjection(db_path, graph_name="test_guard_allow",
                            skip_health_check=True)
    try:
        counts = proj.rebuild_all(str(log_dir), confirm_destructive=True)
    finally:
        proj.close()
    assert counts["nodes"] == 2, "a legitimate wipe+replay must still work"
    assert counts["events"] == 2


def test_embedded_rebuild_single_log_with_token(tmp_path, monkeypatch):
    """(b) ``rebuild(log)`` legitimate path still works with the token."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)  # force embedded
    db_path = str(tmp_path / "guard_rebuild_one.db")
    log = EventLog(tmp_path / "one.jsonl")
    log.append({"type": "PointAdded",
                "point": {"id": "one", "content": "x", "context": "t"}})
    proj = FalkorProjection(db_path, graph_name="test_guard_one",
                            skip_health_check=True)
    try:
        proj.rebuild(log, confirm_destructive=True)
        rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
    finally:
        proj.close()
    assert int(rows[0][0]) == 1


def test_rebuild_parses_before_wipe(tmp_path):
    """A log that cannot be read must raise BEFORE the wipe (no data loss).

    `rebuild()` mirrors `rebuild_all()`'s WIPE-AFTER-PARSE ordering: a corrupt
    MID-FILE line raises out of `read_all()` while the graph is still intact.
    Parsing after the wipe would leave a wiped, empty graph behind."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    log = EventLog(tmp_path / "corrupt.jsonl")
    log.append({"type": "PointAdded",
                "point": {"id": "first", "content": "y", "context": "t"}})
    with open(log.path, "a", encoding="utf-8") as fh:
        fh.write("{not valid json\n")          # mid-file corruption
        # Written RAW, not via log.append: `EventLog.append` seals a
        # terminated-malformed LAST line as a torn fragment before appending
        # (#5917), which would turn this deliberately mid-file corruption into
        # a tolerated tear and move the refusal to #3316. This test is about
        # the parse-before-wipe ordering for a line that is mid-file, so the
        # second record is written directly to keep it mid-file.
        fh.write(json.dumps({"type": "PointAdded",
                             "point": {"id": "second", "content": "z",
                                       "context": "t"}}) + "\n")
    with pytest.raises(ValueError, match="mid-file corruption"):
        proj.rebuild(log, confirm_destructive=True)
    assert proj.g.wipes() == [], "no wipe may run when the log cannot be read"


def test_embedded_raw_wipe_on_test_graph_still_allowed():
    """(b) L2's embedded exemption is unchanged — no regression for the raw
    ``MATCH (n) DETACH DELETE n`` reset sites in the suite."""
    proj = _bare_projection(graph_name="tortoise", embedded=True)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n) DETACH DELETE n")
    assert len(guarded._g.queries) == 1


def test_server_test_named_graph_raw_wipe_without_token_is_allowed():
    """(b) The other half of L2's exemption, pinned: on the RAW-QUERY lane a
    test-named SERVER graph wipes with no token (L1 is not on that lane). This
    is the counterpart to the non-test server refusal above."""
    proj = _bare_projection(graph_name="test_raw_server", embedded=False)
    guarded = _GuardedGraph(_RecordingGraph(), proj)
    guarded.query("MATCH (n) DETACH DELETE n")
    assert len(guarded._g.queries) == 1


# ── (c) DISCRIMINATING pins: the chokepoint body, not just caller shape ───


def test_l1_chokepoint_itself_refuses_without_the_token():
    """(c) The assertion BODY, not just the callers' shape.

    Neutering ``_assert_destructive_confirmed`` is the exact failure mode the
    identifier/signature pins above cannot observe, so this exercises the
    chokepoint directly: it must raise for ``False`` and return for ``True``."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj._assert_destructive_confirmed(False, "rebuild_all")
    proj._assert_destructive_confirmed(True, "rebuild_all")  # must not raise


def test_wipe_all_nodes_consults_the_token_before_issuing_the_wipe(monkeypatch):
    """(c) ``_wipe_all_nodes`` itself consults the token before wiping.

    The named method is called DIRECTLY: ``rebuild_all`` has its own L1
    fast-fail, so driving this through ``rebuild_all`` would only prove that
    *one* of the two calls exists (deleting the one in ``_wipe_all_nodes``
    left the full file green — cycle-4 finding). With the token check
    replaced by a sentinel raise, the direct call must raise and must never
    reach the handle."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)

    def _sentinel(confirm_destructive, operation):
        raise RuntimeError("confirm_destructive: sentinel")

    monkeypatch.setattr(proj, "_assert_destructive_confirmed", _sentinel)
    with pytest.raises(RuntimeError, match="sentinel"):
        proj._wipe_all_nodes(confirm_destructive=True, operation="probe")
    assert proj.g.queries == [], "graph I/O ran before the token check"
    assert proj.g.wipes() == [], "the wipe ran before the token check"


def test_wipe_all_nodes_refuses_without_the_token(tmp_path):
    """(c) DISCRIMINATING: the chokepoint consults the CALLER'S token value.

    The sentinel test below proves the call exists; this one proves the value
    is forwarded. A hardcoded ``True`` at the call site would satisfy the
    sentinel (it ignores its argument) but reds here."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)
    with pytest.raises(RuntimeError, match="confirm_destructive"):
        proj._wipe_all_nodes(confirm_destructive=False, operation="probe")
    assert proj.g.queries == [], "graph I/O ran on a refused token"


def test_rebuild_all_fast_fails_before_any_work(monkeypatch):
    """(c) The rebuild lane's own L1 fast-fail runs before snapshot/parse/wipe.

    ``NO_IO_DIR`` does not exist, so a caller that got past L1 would raise a
    filesystem error instead of the sentinel — the sentinel proves ordering."""
    proj = _bare_projection(graph_name="test_probe", embedded=True)

    def _sentinel(confirm_destructive, operation):
        raise RuntimeError("confirm_destructive: sentinel")

    monkeypatch.setattr(proj, "_assert_destructive_confirmed", _sentinel)
    with pytest.raises(RuntimeError, match="sentinel"):
        proj.rebuild_all(NO_IO_DIR, confirm_destructive=True)
    assert proj.g.queries == []


# ── #3044: the recovery route's emptiness claim, re-validated at the wipe ──


class _NonEmptyRecordingGraph(_RecordingGraph):
    """A fake handle whose *total-node* count reports ONE node.

    Models the #3044 window exactly: the caller proved the graph empty before
    the journal parse, and a node landed since. Only the whole-graph count is
    overridden, so the pre-wipe reads that legitimately expect 0 keep seeing 0
    and the test isolates the emptiness check."""

    def query(self, cypher, params=None, timeout=None):
        if " ".join((cypher or "").split()).upper() == \
                "MATCH (N) RETURN COUNT(N)":
            self.queries.append(cypher)
            return _Result([[1]])
        return super().query(cypher, params=params, timeout=timeout)


class _UnmeasurableRecordingGraph(_RecordingGraph):
    """A fake handle whose *total-node* count returns NO row at all.

    #3044 review: the guard reads an absent/NULL count as UNMEASURABLE and
    refuses — deliberately STRICTER than the probe it re-validates.
    ``_node_count`` (consistency.py) flattens the SAME shape to 0 and reserves
    ``None`` for a query that RAISED, so the two readers of one query disagree
    on this shape by design: the probe reads "empty" and proceeds while
    ``require_empty`` refuses. That asymmetry is the point, not an oversight —
    the probe's 0 is the very claim the guard exists to re-validate, and a
    count row the guard never received is not evidence of emptiness. The guard
    must fail CLOSED on it. The refusal carries its own measurement, so
    `recover_from_log` reports this shape to the operator as `db_points: None`
    (UNMEASURABLE), never a fabricated 0."""

    def query(self, cypher, params=None, timeout=None):
        if " ".join((cypher or "").split()).upper() == \
                "MATCH (N) RETURN COUNT(N)":
            self.queries.append(cypher)
            return _Result([])
        return super().query(cypher, params=params, timeout=timeout)


def test_require_empty_refuses_an_unmeasurable_count(tmp_path):
    """#3044 review: an UNMEASURABLE store is not an empty one.

    `MATCH (n) RETURN count(n)` yielding no row (or NULL) means the guard
    could not observe the store, not that the store is empty — flattening it
    to 0 issues the wipe on a count that was never taken and destroys whatever
    the window holds. The fail-closed choice is to refuse. NOTE the deliberate
    asymmetry with the probe this re-validates: `_node_count` returns 0 for
    the SAME shape and reserves `None` for a RAISED query, so the two readers
    of one query disagree here by design (pinned end to end by
    `test_unmeasurable_row_is_refused_by_the_guard_and_flattened_by_the_probe`).
    The refusal carries its own measurement, so the exception reports
    `db_points is None` — UNMEASURABLE — never a measured 0.
    FAILS IF: the wipe proceeds on an unmeasured count, or the refusal claims
    the store holds 0 nodes."""
    proj = _bare_projection(graph_name="test_3044_unmeasured", embedded=True)
    proj.g = _UnmeasurableRecordingGraph()
    with pytest.raises(WipePreconditionRefused, match="MEASURED") as excinfo:
        proj.rebuild_all(str(tmp_path), confirm_destructive=True,
                         require_empty=True)
    assert excinfo.value.db_points is None, (
        "the unmeasurable refusal carried a fabricated count: "
        f"{excinfo.value.db_points!r}")
    assert proj.g.wipes() == [], (
        "the wipe ran on a node count that was never measured")


def test_unmeasurable_row_is_refused_by_the_guard_and_flattened_by_the_probe(
        tmp_path):
    """#3044 review: the guard's refusal is STRICTER than the probe, on purpose.

    Both readers issue the same `MATCH (n) RETURN count(n)`, but they do NOT
    read an absent/NULL row the same way: `_node_count` (the probe in
    `recover_from_log`) flattens it to 0 and proceeds as if empty, while
    `require_empty` treats it as UNMEASURABLE and refuses. The docstrings above
    state that asymmetry, so this pins it END TO END on one shape — the
    probe's reading and the guard's must both hold, or the stated premise is
    false again.

    FAILS IF: the guard flattens the absent row (the wipe runs), or the probe
    starts reporting `None`/"graph unresponsive" for the same shape without the
    documented reason being rewritten.
    """
    from tortoise.consistency import recover_from_log

    (tmp_path / "events.jsonl").write_text("")  # one adjacent (empty) log

    # 1) The probe reads the absent count row as 0 — "empty" — and proceeds.
    probe = _bare_projection(graph_name="test_3044_asym_probe", embedded=True)
    probe.g = _UnmeasurableRecordingGraph()
    result = recover_from_log(str(tmp_path), probe)
    assert "graph unresponsive" not in result["reason"], (
        f"the probe read the absent count row as UNMEASURABLE; the guard's "
        f"docstring claims it flattens to 0: {result['reason']!r}")

    # 2) The guard re-validating the very same claim must NOT flatten it.
    guard = _bare_projection(graph_name="test_3044_asym_guard", embedded=True)
    guard.g = _UnmeasurableRecordingGraph()
    with pytest.raises(WipePreconditionRefused, match="MEASURED"):
        guard.rebuild_all(str(tmp_path), confirm_destructive=True,
                          require_empty=True)
    assert guard.g.wipes() == [], (
        "the wipe ran on the count the probe had flattened to 0")


def test_require_empty_refuses_a_graph_that_filled_after_the_probe(tmp_path):
    """#3044: the wipe re-validates the emptiness claim it acts on.

    Without this the recovery route's 0-node probe — taken in another module,
    before the whole journal parse — is the only thing bounding a wipe that is
    unconditional over EVERY label while its pre-wipe snapshot covers only the
    labels `_SNAPSHOT_SECTIONS` names. The refusal must land before any
    destructive statement."""
    proj = _bare_projection(graph_name="test_3044", embedded=True)
    proj.g = _NonEmptyRecordingGraph()
    with pytest.raises(WipePreconditionRefused, match="require_empty") as excinfo:
        proj.rebuild_all(str(tmp_path), confirm_destructive=True,
                         require_empty=True)
    assert excinfo.value.db_points == 1, (
        "the refusal must carry the count it refused on — the operator reads "
        f"it as db_points: {excinfo.value.db_points!r}")
    assert proj.g.wipes() == [], (
        "the wipe ran even though the graph had stopped being empty")


def test_wipe_refusal_names_the_real_snapshot_residual(tmp_path):
    """#3044 review: the blast-radius claim must match `_SNAPSHOT_SECTIONS`.

    The refusal is the operator's only account of what the wipe would have
    destroyed, and the exception docstring is the whole stated justification
    for the guard — so a residual clause that understates the snapshot's
    coverage mis-scopes what the refusal protects. The pre-#548-era wording
    ("covers only :Point/:Batch") is contradicted by the tuple: it also
    carries `:Session`, the onboarding pair, the config registry,
    `:GraphEventMeta` (restored by `reestablish_watermark` inside
    `rebuild_all`) and `:TeamMeta`. The genuinely unrestorable residual is
    `:GraphEvent` rows (#4664: no replay pass recreates them) plus any label
    outside the tuple. The claim and the tuple it cites are asserted TOGETHER,
    so an enrolment that changes the coverage reddens this test rather than
    silently re-opening the gap."""
    proj = _bare_projection(graph_name="test_3044_residual", embedded=True)
    proj.g = _NonEmptyRecordingGraph()
    with pytest.raises(WipePreconditionRefused) as excinfo:
        proj.rebuild_all(str(tmp_path), confirm_destructive=True,
                         require_empty=True)
    message = str(excinfo.value)

    # The clause the snapshot tuple contradicts must be gone...
    assert "covers only :Point/:Batch" not in message, (
        "the refusal still claims the snapshot covers only :Point/:Batch, "
        "which _SNAPSHOT_SECTIONS contradicts")
    # ...and the residual must be the REAL one.
    assert ":GraphEvent" in message, (
        "the refusal must name :GraphEvent rows — the residual no "
        "_SNAPSHOT_SECTIONS entry restores")
    # Pin the claim to the coverage it cites, so neither can drift alone.
    assert "_SNAPSHOT_SECTIONS" in message
    assert "event_meta" in _SNAPSHOT_SECTIONS, (
        ":GraphEventMeta IS captured, so a 'covers only :Point/:Batch' "
        "claim is false by construction")

    doc = WipePreconditionRefused.__doc__ or ""
    assert "covers only ``:Point`` / ``:Batch``" not in doc, (
        "the exception docstring — the guard's stated justification — still "
        "understates the snapshot coverage")
    assert ":GraphEvent" in doc


def test_require_empty_is_opt_in_not_a_new_global_precondition(tmp_path):
    """#3044: an explicit operator rebuild on a populated graph stays legal.

    The token is a per-call opt-in. Making the emptiness check unconditional
    would refuse every `tortoise rebuild --dir` on a store that has data — the
    operation the wipe exists for."""
    proj = _bare_projection(graph_name="test_3044_optin", embedded=True)
    proj.g = _NonEmptyRecordingGraph()
    counts = proj.rebuild_all(str(tmp_path), confirm_destructive=True)
    assert counts["nodes"] == 0
    assert proj.g.wipes() == ["MATCH (n) DETACH DELETE n"], (
        "without the token the wipe is the operator's explicit instruction")


def test_require_empty_passes_a_genuinely_empty_graph(tmp_path):
    """#3044: no false refusal — the recovery route's real precondition.

    The route reaches this call only on a graph it proved empty, so the token
    must be inert there. A check that refused here would turn every embedded
    auto-recovery into a permanent failure."""
    proj = _bare_projection(graph_name="test_3044_empty", embedded=True)
    counts = proj.rebuild_all(str(tmp_path), confirm_destructive=True,
                              require_empty=True)
    assert counts["nodes"] == 0
    assert proj.g.wipes() == ["MATCH (n) DETACH DELETE n"]


def test_require_empty_spares_a_label_the_wipe_never_restores(
        tmp_path, monkeypatch):
    """#3044 end-to-end on a real embedded graph: the non-vacuity pair.

    The node the wipe would destroy is a label the pre-wipe snapshot does NOT
    cover, so after the wipe there is no record of it anywhere. WITH the token
    the wipe refuses and the node survives; WITHOUT it the same call completes
    and the node is gone — so the survival is the token doing the work, not a
    coincidence of the call not reaching the wipe.

    A :Point is present too, so the run has a non-empty section and would
    write a rescue sidecar if it proceeded — the refusal must precede that
    write (the existing pre-wipe-guard convention)."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)  # force embedded
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    sidecar = prewipe_snapshot_path(str(log_dir))

    proj = FalkorProjection(str(tmp_path / "guard_3044.db"),
                            graph_name="test_guard_3044",
                            skip_health_check=True)
    try:
        proj.g.query("CREATE (:GraphEvent {seq: 1})")
        proj.g.query("CREATE (p:Point {id:'p3044', content:'x', "
                     "context:'t'})")

        with pytest.raises(WipePreconditionRefused, match="require_empty"):
            proj.rebuild_all(str(log_dir), confirm_destructive=True,
                             require_empty=True)
        rows = proj.g.query(
            "MATCH (n:GraphEvent) RETURN count(n)").result_set
        assert int(rows[0][0]) == 1, (
            "a node that appeared after the emptiness probe was destroyed: "
            "the wipe is unconditional and :GraphEvent rows are covered by "
            "no _SNAPSHOT_SECTIONS entry")
        assert not os.path.exists(sidecar), (
            "a refused wipe must strand no rescue sidecar")

        # Non-vacuity: WITHOUT the token the same call wipes it.
        proj.rebuild_all(str(log_dir), confirm_destructive=True)
        rows = proj.g.query(
            "MATCH (n:GraphEvent) RETURN count(n)").result_set
        assert int(rows[0][0]) == 0
    finally:
        proj.close()


def test_recover_from_log_refuses_the_wipe_when_a_writer_lands_in_the_window(
        tmp_path, monkeypatch):
    """#3044: the recovery route re-validates its probe at the wipe.

    `recover_from_log` proves 0 nodes, then parses the journal (seconds for a
    large log), then wipes. The window is simulated at the exact seam it sits
    at: a node is created while the pending sidecar is being loaded — after
    the probe, before the wipe. The refusal must leave the graph UNTOUCHED,
    NOT rolled back: a rollback there would be the destruction the refusal
    exists to prevent, because `_roll_back_declined_replay` is sound only when
    every node present came from the declined replay."""
    from tortoise import consistency as consistency_mod

    events = tmp_path / "events"
    events.mkdir()
    (events / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "IngestStarted"}) + "\n", encoding="utf-8")
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1, "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded", "projection_version": 2,
            "point": {"id": "sidecar-3044", "content": "x",
                      "pointKind": "fact", "status": "live"}}],
        "batch_snapshot": [], "batch_point_links": [],
        "session_snapshot": [], "session_point_links": [],
    })

    proj = FalkorProjection(str(tmp_path / "recover_3044.db"),
                            graph_name="test_recover_3044",
                            skip_health_check=True)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")  # the 0-node probe's subject

        real_load = consistency_mod._load_prewipe_snapshot

        def load_and_a_writer_lands(path):
            loaded = real_load(path)
            proj.g.query("CREATE (:GraphEvent {seq: 1})")  # concurrent write
            return loaded

        monkeypatch.setattr(consistency_mod, "_load_prewipe_snapshot",
                            load_and_a_writer_lands)
        result = consistency_mod.recover_from_log(str(events), proj)

        assert result["recovered"] is False, result
        assert "refused" in result["reason"], result
        assert result["db_points"] == 1, (
            "the refusal must report the count it refused on: the graph holds "
            f"1 node, not {result['db_points']!r}")
        rows = proj.g.query(
            "MATCH (n:GraphEvent) RETURN count(n)").result_set
        assert int(rows[0][0]) == 1, (
            "recover_from_log destroyed a node that landed after its "
            "emptiness probe — #3044's exact failure mode")
        assert os.path.exists(prewipe_snapshot_path(str(events))), (
            "the pre-existing sidecar is the record of an interrupted "
            "rebuild; a refusal must not retire it")
    finally:
        proj.close()


def test_recover_from_log_reports_an_unmeasurable_refusal_as_unknown(
        tmp_path):
    """#3044 review (P2): the refusal RESULT carries the guard's OWN count.

    The guard refuses the UNMEASURABLE shape — `MATCH (n) RETURN count(n)`
    yields no row — with "could not be MEASURED". `recover_from_log` used to
    report that refusal's `db_points` from `_node_count()`, which flattens the
    SAME absent row to 0, so `_auto_health_recover` was told the store was
    EMPTY in the very line that said its count was never taken. The
    measurement now travels on the exception, so the producer and the consumer
    cannot drift apart. FAILS IF: the empty-row refusal is reported as
    `db_points: 0` — the false "empty store" claim this guard exists to
    remove."""
    from tortoise import consistency as consistency_mod

    events = tmp_path / "events"
    events.mkdir()
    (events / "sdk.jsonl").write_text(json.dumps({
        "event_id": "e1", "ts": "2026-01-01T00:00:00Z",
        "type": "IngestStarted"}) + "\n", encoding="utf-8")
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1, "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded", "projection_version": 2,
            "point": {"id": "sidecar-3044-unmeasured", "content": "x",
                      "pointKind": "fact", "status": "live"}}],
        "batch_snapshot": [], "batch_point_links": [],
        "session_snapshot": [], "session_point_links": [],
    })

    proj = FalkorProjection(str(tmp_path / "recover_3044_unmeasured.db"),
                            graph_name="test_recover_3044_unmeasured",
                            skip_health_check=True)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")  # the 0-node probe's subject

        real_g = proj.g
        seen = {"total_count_reads": 0}

        class _UnmeasurableTotalCount:
            """Delegate to the real guarded handle, but make the TOTAL-node
            count unmeasurable (an empty rowset). `_GuardedGraph` is slotted,
            so the read cannot be patched in place."""

            def __getattr__(self, name):
                return getattr(real_g, name)

            def query(self, cypher, params=None, timeout=None):
                if " ".join((cypher or "").split()).upper() == \
                        "MATCH (N) RETURN COUNT(N)":
                    seen["total_count_reads"] += 1
                    return _Result([])  # the guard's "could not be MEASURED" row
                return real_g.query(cypher, params=params, timeout=timeout)

        proj.g = _UnmeasurableTotalCount()
        result = consistency_mod.recover_from_log(str(events), proj)

        assert seen["total_count_reads"] >= 2, (
            "the unmeasurable count was never read by both the probe and the "
            f"guard — this fixture is not exercising the shape ({seen})")
        assert result["recovered"] is False, result
        assert "refused" in result["reason"], result
        assert "MEASURED" in result["reason"], (
            "the fixture must reach the guard's UNMEASURABLE refusal, not "
            f"another one: {result['reason']!r}")
        assert result["db_points"] is None, (
            f"the unmeasurable refusal was reported as {result['db_points']!r}; "
            "0 asserts an empty store that was never observed, contradicting "
            "the refusal's own 'could not be MEASURED' message")
    finally:
        proj.close()


def test_recover_from_log_leaves_the_window_node_when_the_journal_fails_before_the_wipe(
        tmp_path, monkeypatch):
    """#3044 review (P1): the guard must cover BOTH destructive statements.

    The `require_empty` guard sits AFTER the journal parse, so a mid-file
    corruption raises in the parse — before the guard, before the sidecar and
    before any wipe. That failure lands in the pending route's generic
    `except`, which measured the graph and called
    `_roll_back_declined_replay`. With a node in the window the measured count
    is > 0, so the rollback issued its OWN `MATCH (n) DETACH DELETE n` and
    destroyed the very node the guard exists to protect — the rollback's entry
    invariant ("every node present came from the declined replay") is false
    when no replay ever ran. FAILS IF: a pre-wipe failure rolls back the
    window node, or any destructive statement runs before the guard."""
    from tortoise import consistency as consistency_mod

    events = tmp_path / "events"
    events.mkdir()
    # Line 1 is a valid record; line 2 is malformed MID-FILE (not a torn
    # append), so `rebuild_all`'s parse refuses it before reaching the guard.
    (events / "sdk.jsonl").write_text(
        json.dumps({"event_id": "e1", "ts": "2026-01-01T00:00:00Z",
                    "type": "IngestStarted"}) + "\n"
        + "this is not json\n", encoding="utf-8")
    _write_prewipe_snapshot(prewipe_snapshot_path(str(events)), {
        "version": 1, "created_at": "2026-01-01T00:00:00Z",
        "synthetic_events": [{
            "type": "PointAdded", "projection_version": 2,
            "point": {"id": "sidecar-3044-p1", "content": "x",
                      "pointKind": "fact", "status": "live"}}],
        "batch_snapshot": [], "batch_point_links": [],
        "session_snapshot": [], "session_point_links": [],
    })

    proj = FalkorProjection(str(tmp_path / "recover_before_wipe.db"),
                            graph_name="test_recover_before_wipe",
                            skip_health_check=True)
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")  # the 0-node probe's subject

        real_load = consistency_mod._load_prewipe_snapshot

        def load_and_a_writer_lands(path):
            loaded = real_load(path)
            proj.g.query("CREATE (:GraphEvent {seq: 1})")  # concurrent write
            return loaded

        monkeypatch.setattr(consistency_mod, "_load_prewipe_snapshot",
                            load_and_a_writer_lands)

        wipes: list = []
        real_wipe = FalkorProjection._wipe_all_nodes

        def traced_wipe(self, **kwargs):
            wipes.append(kwargs.get("operation"))
            return real_wipe(self, **kwargs)

        monkeypatch.setattr(FalkorProjection, "_wipe_all_nodes", traced_wipe)
        result = consistency_mod.recover_from_log(str(events), proj)

        assert result["recovered"] is False, result
        rows = proj.g.query(
            "MATCH (n:GraphEvent) RETURN count(n)").result_set
        assert int(rows[0][0]) == 1, (
            "a pre-wipe failure rolled back the node that landed after the "
            "emptiness probe — the exact loss #3044 exists to prevent")
        assert wipes == [], (
            f"no destructive statement may run before the guard; saw {wipes!r}")
        assert result.get("replay_rolled_back") is None, result
        assert "before the wipe" in result["reason"], result
        assert os.path.exists(prewipe_snapshot_path(str(events))), (
            "a refused pre-wipe failure must not retire the sidecar")
    finally:
        proj.close()
