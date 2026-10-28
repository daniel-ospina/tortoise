"""#3019 — the ranked order must be a function of the DATA, not of DB row order.

Two determinism gaps are pinned here (the issue's parts 1 and 2; part 3 — the leg
SET flipping with wall-clock time via the embedder's negative cache — is a
product decision and is deliberately NOT touched):

  1. **Within-leg tie order.** Each ordering leg carried a SINGLE key — ``score
     DESC`` for the two FTS legs and the vector leg's brute-force scan, ``hops
     ASC`` for the hop expansion — and the kind-scan structural leg had no
     ``ORDER BY`` at all, so a tie fell through to DB row order. The operator FTS leg
     is the sharpest case: it returns a CONSTANT ``1.0 AS score`` for every row,
     so the entire leg is one tie *by construction* — no dependence on the
     index leg's scores.
  2. **A non-finite fusion weight.** ``json.loads`` accepts bare ``NaN`` /
     ``Infinity``. A NaN weight makes every fused score FROM THAT LEG NaN — a
     candidate carried only by another leg keeps a finite score, but a NaN key
     compares False against every key, so the ORDER damage is global. Tuple
     comparison against NaN is False in BOTH directions, so the ``(-score, id)``
     key that #2952 introduced silently degraded to insertion order for those
     candidates.

Every BEHAVIOURAL test below names (a) the value that makes it FAIL and (b) how
the fixture reaches that value. Tests whose fixture could silently produce
nothing carry an explicit "FIXTURE NOT REACHED" assertion, so a vacuous pass is a
failure.

Runnable with:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_determinism_tie_order_3019.py -v
"""
from __future__ import annotations

import inspect
import math
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise import search_engine
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import (
    reset_circuit_breakers,
    rrf_fusion,
    run_fts_query,
    run_structural_query,
)


@pytest.fixture
def sdk():
    """SDK on a temp database, reclaimed on teardown."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_3019_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit-breaker state is module-level — isolate every test from it."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


# ── part 2: a non-finite fusion weight must not degrade the tie order ───────


def test_non_finite_weight_does_not_degrade_the_tie_order():
    """FAIL VALUE: ``weights={"vector": nan}`` — every fused score becomes NaN, so
    the ``(-score, id)`` comparison is False in both directions and the sort falls
    back to insertion order, yielding ``["b", "a"]``.

    THE FIXTURE REACHES IT: each id occupies the SAME RANK in the two legs
    (``b`` is rank 0 in one and rank 1 in the other; ``a`` the reverse), so the
    two RRF scores are not merely close but EXACTLY equal. The id is then the only
    thing that can order them — which is precisely what a NaN destroys. And ``b``
    is inserted into the accumulator first, so insertion order really is
    ``["b", "a"]``.
    """
    leg_a = [("b", 0.9), ("a", 0.8)]
    leg_b = [("a", 0.9), ("b", 0.8)]

    finite = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                        weights={"vector": 1.0})
    assert list(finite) == ["a", "b"], "the tie-break itself regressed"

    degenerate = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                            weights={"vector": float("nan")})
    assert list(degenerate) == ["a", "b"], (
        "a non-finite weight degraded the fused order to insertion order: "
        f"{list(degenerate)}"
    )


def test_infinite_weight_does_not_destroy_the_score_signal():
    """FAIL VALUE: ``inf`` — every fused score becomes ``inf``, so all candidates
    tie at infinity and the fused SCORES carry no information at all; the ordering
    then rests on the id alone rather than on relevance.

    THE FIXTURE REACHES IT: ``json.loads('{"vector": Infinity}')`` is accepted, so
    an env-supplied weight really can be infinite.

    NOTE ON THE ISSUE'S FRAMING: an infinite weight does not degrade the ORDER
    the way ``NaN`` does — ``inf == inf`` is True, so the tuple falls through to
    the id and stays deterministic. What it costs is the score signal.
    """
    leg_a = [("b", 0.9), ("a", 0.8)]
    leg_b = [("a", 0.9), ("b", 0.8)]
    out = rrf_fusion([leg_a, leg_b], strategy_names=["vector", "fts"],
                     weights={"vector": float("inf")})
    assert list(out) == ["a", "b"], f"infinite weight reordered the result: {list(out)}"
    assert all(math.isfinite(s) for s in out.values()), (
        f"an infinite weight destroyed every fused score: {out}"
    )


# ── part 1: a tied leg must be ordered by id ────────────────────────────────


def test_operator_fts_leg_orders_a_tied_result_by_id(sdk):
    """FAIL VALUE: the operator leg returns its rows in DB row order instead of a
    data-determined order. This leg scores EVERY row a constant ``1.0``, so the
    whole leg is one tie and nothing but the secondary keys can order it.

    THE FIXTURE REACHES IT: the three nodes are created in ASCENDING id order
    (``alpha``, ``mike``, ``zulu``) and carry NO timestamp property, so the
    recency key coalesces to ``''`` for all three and the id key decides. #10121
    reversed that key's DIRECTION (newest first), so the expected order is
    ``["zulu-op", "mike-op", "alpha-op"]`` — the REVERSE of the mint order,
    which is the one order a bare DB scan cannot produce. (#10121's tie direction
    reversed the seeding too: minting them DESCENDING, as this fixture did
    before, made the expectation coincide with insert order, so the assertion
    passed whether or not the leg ordered anything at all.) This also confirms
    the BRUTE-FORCE path honours multi-key ordering (the operator leg uses no
    fulltext index), which the issue asks to establish.
    """
    graph = sdk._get_proj().g
    for pid in ["alpha-op", "mike-op", "zulu-op"]:
        graph.query(
            "CREATE (n:Point {id:$id, is_operator:true, label:$label})",
            params={"id": pid, "label": "determinism needle"},
        )

    rows = run_fts_query(graph, "determinism needle", entity_type="operator",
                         limit=10, excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the operator leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got, reverse=True), (
        "a fully-tied leg is not recency-then-id-ordered (#10121: the id key is "
        f"DESC, newest first): {got}"
    )


# ── part 1, all four sites: the clause must not be tidied away ─────────────


def test_index_fts_leg_orders_a_tied_result_by_id(sdk):
    """FAIL VALUE: the index-accelerated FTS leg returns rows in index order
    instead of a data-determined order. #3019 asks to CONFIRM the engine honours
    multi-key ordering on THIS path, not only the brute-force one — so this
    asserts the behaviour rather than the query text.

    THE FIXTURE REACHES IT: the three nodes are created in ASCENDING id order
    (``alpha``, ``mike``, ``zulu``), carry NO timestamp, and all match the term —
    so they score identically, the recency key coalesces to ``''``, and the id
    key decides. #10121 made that key ``DESC`` (newest first), so
    ``["zulu-ix", "mike-ix", "alpha-ix"]`` is the order — the REVERSE of the
    mint order, and it is unreachable unless the secondary keys are applied.
    """
    graph = sdk._get_proj().g
    for pid in ["alpha-ix", "mike-ix", "zulu-ix"]:
        graph.query(
            "CREATE (n:Point {id:$id, content:'determinism needle', "
            "pointKind:'statement'})",
            params={"id": pid},
        )

    rows = run_fts_query(graph, "determinism needle", entity_type="point", limit=10,
                         excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the index FTS leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got, reverse=True), (
        "an index-path tie is not recency-then-id-ordered (#10121: the id key is "
        f"DESC, newest first): {got}"
    )


def test_structural_leg_orders_a_constant_scored_result_by_id(sdk):
    """FAIL VALUE: the structural leg returns its rows in DB row order.

    This leg scores EVERY row a CONSTANT (``1.0`` when a kind is given, else
    ``0.5``), so it is one tie exactly like the operator leg — and it carried no
    ``ORDER BY`` at all, which made it the one leg that was completely unordered.

    THE FIXTURE REACHES IT: the three nodes are created in ASCENDING id order
    (``alpha``, ``mike``, ``zulu``), so an order-determined result is reachable
    only through the secondary keys — the expected value is the REVERSE of the
    mint order, i.e. the one thing DB row order cannot return. #10121 made the id
    key ``DESC``; with no timestamps the recency key coalesces to ``''`` and
    ``["zulu-st", "mike-st", "alpha-st"]`` is the expected order.
    """
    graph = sdk._get_proj().g
    for pid in ["alpha-st", "mike-st", "zulu-st"]:
        graph.query(
            "CREATE (n:Point {id:$id, pointKind:$kind, content:$id})",
            params={"id": pid, "kind": "statement"},
        )

    rows = run_structural_query(graph, "statement", entity_type="point", limit=10,
                                excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the structural leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert got == sorted(got, reverse=True), (
        "a constant-scored leg is not recency-then-id-ordered (#10121): "
        f"{got}"
    )


def test_structural_leg_orders_documents_by_their_canonical_key(sdk):
    """FAIL VALUE: `run_structural_query(..., entity_type="document")` returned
    `[(None, 1.0), (None, 1.0), (None, 1.0)]` — every pid ``None``, in DB row
    order. The leg resolved ``id_field`` to ``id`` for documents, and a document
    has no ``id`` (see the fixture), so ``ORDER BY n.id ASC`` ordered on NULL for
    every row: the tie stayed UNRESOLVED — the exact defect #3019 exists to close
    — and the caller could not identify a document it had been handed.

    THE FIXTURE REACHES IT: the sources are minted with
    ``projection.edges._mint_source_stub`` — the REAL stub path every provenance
    link uses — which sets url / sourceKind / contentHash / ingestedAt and NEVER
    sets ``id`` (measured: ``MATCH (s:Source) RETURN s.url, s.id`` =
    ``[['a-doc', None], ['m-doc', None], ['z-doc', None]]`` — the mint order).
    ``documentKind`` is
    then written onto those same nodes, which leaves ``id`` NULL — a state
    ``_upsert_source`` cannot repair either, since it sets ``id`` only ON CREATE.
    They are minted in ASCENDING url order (``a-doc``, ``m-doc``, ``z-doc``), so
    ``["z-doc", "m-doc", "a-doc"]`` — the reverse of the mint order — is
    unreachable unless the leg orders by the document's canonical key.

    WHY THIS IS BEHAVIOURAL AND NOT A SECOND STRING PIN:
    ``test_every_leg_query_carries_a_secondary_sort_key`` asserts the ORDER BY
    *text* (``"ORDER BY n.{id_field} ASC"``), so it stayed GREEN on the broken
    arm — ``id_field`` is interpolated, and a wrong value is invisible to a
    substring match. That is the pin's own lesson: pin the behaviour, not the
    presence of a textual fix.
    """
    from tortoise.projection.edges import _mint_source_stub

    graph = sdk._get_proj().g
    for url in ["a-doc", "m-doc", "z-doc"]:
        _mint_source_stub(graph, url)
        graph.query(
            "MATCH (s:Source {url:$url}) SET s.documentKind=$kind",
            params={"url": url, "kind": "note"},
        )

    # FIXTURE NOT REACHED guard: the premise of this test is that a document can
    # carry documentKind with NO id. If a writer starts setting `id` on the stub
    # path, this test would pass for the WRONG reason — so assert the premise.
    stored = graph.query(
        "MATCH (s:Source) RETURN s.url, s.id, s.documentKind").result_set
    assert len(stored) == 3, f"FIXTURE NOT REACHED: expected 3 sources, got {stored}"
    assert all(r[1] is None for r in stored), (
        "FIXTURE NOT REACHED: the documents now carry an `id`, so this test no "
        f"longer exercises the id-less path it is named for: {stored}"
    )

    rows = run_structural_query(graph, "note", entity_type="document", limit=10,
                                excluded_statuses=())
    got = [pid for pid, _ in rows]

    assert got, "FIXTURE NOT REACHED: the structural leg returned no rows"
    assert len(got) == 3, f"FIXTURE NOT REACHED: expected 3 rows, got {got}"
    assert None not in got, (
        "a document's pid came back None — the leg ordered on a property "
        f"documents do not have (D10: a document is a :Source keyed by url): {got}"
    )
    # #10121: the canonical key's DIRECTION is now DESC. A :Source stub carries
    # ``ingestedAt``, not ``createdAt``/``updatedAt``, so the recency key
    # coalesces to ``''`` for all three and the url key decides — deterministically,
    # which is the whole point (the failure this test exists for was ``None`` for
    # every pid, i.e. NO resolved order at all). The set, not an incidental
    # direction, is what the identity contract requires.
    assert got == ["z-doc", "m-doc", "a-doc"], (
        "documents are not ordered deterministically by their canonical key: "
        f"{got}"
    )


#: Every way a cypher reaches the graph in this module. `ro_query` is the
#: read-only verb `cypher_guard` wraps; matching only `query` would let a leg
#: written against it slip past the census entirely (#10121 round-5 review).
#: `profile` executes the statement (it returns a plan PLUS runs it) and
#: `execute_command` can carry a raw `GRAPH.QUERY` (see
#: `tortoise/projection/__init__.py::_HANDLE_MUTATING_VERBS`, which lists all six
#: so "what is a query verb" cannot drift between readers). `explain` PLANS and
#: never executes, so it is deliberately absent — it cannot return rows.
_QUERY_VERBS = frozenset({"query", "ro_query", "_query", "profile",
                          "execute_command"})

#: Markers that make a read non-ranking BY ITS OWN SHAPE, so a missing ORDER BY
#: is correct rather than a defect. Each is a property of the read, not a list of
#: blessed call sites — which is why this cannot rot into a stale inventory.
#:
#: * ``IN $ids`` / ``IN $op_ids`` — an ID-ANCHORED batch read: the caller already
#:   chose the rows, so the DB's row order is not consumed as a rank (every one of
#:   these returns an id-keyed dict/set and never reads a row POSITION). Note what
#:   this does NOT claim: a ``LIMIT`` inside such a read still picks its MEMBERS in
#:   engine order, so which peers survive a per-op cap is DB-order — pre-existing
#:   and out of #10121's scope (search_engine.py:4204, a ``CALL { … LIMIT $per_op }``
#:   with no ORDER BY, whose members are the Q2-crit-covered set), but it is not
#:   "ordering is irrelevant", which this comment said before round 8.
#: * ``RETURN count(`` — an aggregate: one row comes back, nothing to rank.
#: * ``{id: $`` — a single-row id lookup.
#: * ``LIMIT 1`` (as a TOKEN) — a single-row read by construction. This one is a
#:   PATTERN, not a substring: plain ``in`` matching let ``LIMIT 1`` match
#:   ``LIMIT 10`` / ``LIMIT 100`` / ``LIMIT 1_000``, so a NEW unordered leg with
#:   a ten-row cap was classified "id-anchored/aggregate" and skipped — the one
#:   hole left in a guard that otherwise fails closed (#10121 round-10 review,
#:   found at ``c23d35ca`` and still open at ``f255f49b6``). ``\b`` after the
#:   digit is what closes it: the next char must not be a word char, and ``_``
#:   is one, so ``1_000`` cannot pass either.
#:
#: A read with NONE of these and no ORDER BY is a leg returning rows in DB order
#: — exactly the unordered leg #3019 found — and it FAILS this census.
_ANCHORED_READ_MARKERS: tuple[str, ...] = (
    "IN $ids",
    "IN $op_ids",
    "RETURN count(",
    "{id: $",
)

#: The markers whose SUBSTRING form is ambiguous, held as patterns instead.
_ANCHORED_READ_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"LIMIT\s+1\b"),
)


def _is_anchored_read(text: str) -> bool:
    """Is this no-``ORDER BY`` cypher non-ranking BY ITS OWN SHAPE?

    Factored out of the census so the classification itself is testable
    (``test_a_bare_limit_is_not_an_id_anchor``): while it was inline, the
    ``LIMIT 1``/``LIMIT 10`` hole could only be found by a reviewer reading the
    marker list, which is how it survived to round 10.
    """
    if any(marker in text for marker in _ANCHORED_READ_MARKERS):
        return True
    return any(pattern.search(text) for pattern in _ANCHORED_READ_PATTERNS)


@pytest.mark.parametrize("text", [
    "MATCH (e:Event) RETURN e.eventId LIMIT 10",
    "MATCH (e:Event) RETURN e.eventId LIMIT 100",
    "MATCH (e:Event) RETURN e.eventId LIMIT 1_000",
    "MATCH (e:Event) RETURN e.eventId LIMIT $cap",
])
def test_a_bare_limit_is_not_an_id_anchor(text):
    """A MULTI-row cap must never be read as a single-row read.

    #10121 round-10 review: the marker was the substring ``"LIMIT 1"``, which
    matches ``LIMIT 10`` / ``LIMIT 100`` / ``LIMIT 1_000`` — so a NEW unordered
    leg with a ten-row cap was filed as "id-anchored/aggregate" and skipped, the
    one direction in which this census did not fail closed. FAIL VALUE: any of
    these returning True.
    """
    assert not _is_anchored_read(text), (
        f"{text!r} has no ORDER BY and is NOT a single-row read, so the census "
        "must judge it (and fail it) rather than exempt it")


@pytest.mark.parametrize("text", [
    "MATCH (s:Source) WHERE s.url IN $ids RETURN s.url",
    "MATCH (n) WHERE n.id IN $op_ids RETURN n.id",
    "MATCH (n:Point) RETURN count(n)",
    "MATCH (n:Point {id: $id}) RETURN n",
    "MATCH (n:Point {id: $id}) RETURN n.id LIMIT 1",
    "MATCH (n:Point {id: $id}) RETURN n.id LIMIT  1",
    "MATCH (n:Point {id: $id}) RETURN n.id LIMIT\n    1",
])
def test_the_anchor_markers_still_recognise_genuinely_anchored_reads(text):
    """The token fix must not cost the markers their real reads.

    FAIL VALUE: a genuinely id-anchored/aggregate/single-row read returning
    False — that turns the census into a false FAIL on the two live no-ORDER-BY
    sites in ``search_engine.py``.
    """
    assert _is_anchored_read(text)


def test_every_leg_query_carries_a_secondary_sort_key():
    """A CENSUS, not coverage: every ordering leg must use the SHARED tie clause.

    The #3019 lesson this file records is that a substring pin on the ORDER BY
    *text* cannot see a wrong ``id_field`` interpolation — it stayed green on the
    broken document arm. So the behavioural tests above are the coverage, and
    this test exists for the one thing behaviour cannot cover: a NEW leg added
    later, or a leg that quietly goes back to a single key.

    #10121 review: the first version of this census scanned source LINES for
    ``ORDER BY``, which a single-quoted clause or a clause assembled across lines
    evaded, and which a reformat could break for no reason. It now walks the
    module's AST and inspects every STRING CONSTANT in it, so quoting and
    line-splitting cannot hide a clause — and it counts what it found, so a
    census that sees nothing says so instead of passing.

    FAIL VALUE: a leg with a bare primary key (e.g. ``ORDER BY hops ASC``), a
    hand-written second key (``... , n.id ASC``), or NO ordering at all — the
    first leaves the tie to DB row order, the second re-introduces the
    oldest-first truncation #10121 fixed, the third is the unordered-leg shape
    #3019 found. The no-ordering arm is judged too (round-6 review): a read with
    no ``ORDER BY`` must carry an id-anchor/aggregate marker from
    ``_ANCHORED_READ_MARKERS`` or be declared in ``UNORDERED_ALLOWED``, so a leg
    that omits ordering cannot pass silently.
    """
    import ast

    source = inspect.getsource(search_engine)
    tree = ast.parse(source)

    def _calls_tie(node: ast.AST) -> bool:
        return any(
            isinstance(child, ast.Call)
            and (
                (isinstance(child.func, ast.Name)
                 and child.func.id == "recency_tie_clause")
                or (isinstance(child.func, ast.Attribute)
                    and child.func.attr == "recency_tie_clause")
            )
            for child in ast.walk(node)
        )

    clauses: list[str] = []
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):  # f-strings are the only shape
            continue                                # these clauses are built in
        literal = "".join(
            part.value for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str))
        if "ORDER BY" not in literal:
            continue
        clauses.append(literal.strip())
        if not _calls_tie(node):
            offenders.append(literal.strip())

    assert len(clauses) >= 6, (
        "FIXTURE NOT REACHED: the AST census found fewer ORDER BY f-strings "
        f"than the six legs define: {clauses}")
    assert not offenders, (
        "every ordering leg must append the SHARED recency tie "
        "(search_engine.recency_tie_clause) — a leg that does not is either "
        "leaving its tie to DB row order or re-inventing the oldest-first key "
        f"#10121 removed: {offenders}"
    )
    def _tie_args(call: ast.Call) -> tuple[object, ...]:
        return tuple(
            arg.value if isinstance(arg, ast.Constant) else None
            for arg in call.args)

    tie_calls = [
        _tie_args(n) for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == "recency_tie_clause")
            or (isinstance(n.func, ast.Attribute)
                and n.func.attr == "recency_tie_clause")
        )
    ]
    assert len(tie_calls) >= 6, (
        f"expected one shared-tie call per leg, found {tie_calls}")
    assert ("n", "id") in tie_calls, (
        "the operator leg's clause must survive: it returns a CONSTANT 1.0 for "
        f"every row, so the tie is its entire ranking — calls were {tie_calls}"
    )
    # INVERTED CHECK (#10121 review): the f-string scan above cannot see a leg
    # whose cypher is a plain string, a concatenation or ``.format(...)``. This
    # walks every ``.query(cypher, ...)`` call site instead and requires that any
    # cypher containing ORDER BY also calls the shared tie, AND that a cypher
    # WITHOUT one is an id-anchored/aggregate read or a declared exception — so a
    # NEW leg is covered by construction, since it cannot reach the graph without
    # a query.
    def _literal_text(node: ast.AST) -> str:
        return "".join(
            child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant) and isinstance(child.value, str))

    # These legs build the cypher into a local first (`cypher = (...)` then
    # `graph.query(cypher, ...)`), so a Name argument must be resolved to ITS
    # OWN assignment. The resolution is PER FUNCTION (#10121 review): a single
    # module-wide map keyed by variable name collapsed all nine `cypher` locals
    # onto whichever assignment traversal saw first, so a new leg was validated
    # against another leg's clause and the census stayed green. A name that
    # cannot be resolved in its own scope is an OFFENDER, not a skip — failing
    # closed is the only useful direction here.
    funcs = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def _scope_of(line: int) -> ast.AST:
        containing = [f for f in funcs if f.lineno <= line <= (f.end_lineno or f.lineno)]
        return min(containing, key=lambda f: (f.end_lineno or 0) - f.lineno) \
            if containing else tree

    def _assignments(scope: ast.AST) -> list[tuple[int, str, ast.expr]]:
        """(lineno, name, value) for each single-Name assignment IN this scope.

        Nested function bodies are excluded: their locals are not visible here,
        and walking them is what collapsed the census onto another leg's clause.
        """
        out: list[tuple[int, str, ast.expr]] = []
        own = list(scope.body) if hasattr(scope, "body") else []
        seen_ids: set[int] = set()
        while own:
            node = own.pop()
            if id(node) in seen_ids:
                continue
            seen_ids.add(id(node))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.Lambda)):
                continue  # a nested scope owns its own names
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)):
                out.append((node.lineno, node.targets[0].id, node.value))
            own.extend(ast.iter_child_nodes(node))
        return out

    def _for_target_iterable(scope: ast.AST, name: str, line: int) -> str:
        """The callable a ``for <name> in <callable>(...)`` loop draws from."""
        for node in ast.walk(scope):
            if not (isinstance(node, ast.For)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == name
                    and node.lineno <= line
                    and isinstance(node.iter, ast.Call)):
                continue
            func = node.iter.func
            if isinstance(func, ast.Name):
                return func.id
            if isinstance(func, ast.Attribute):
                return func.attr
        return "<unresolved>"

    def _bound_names(scope: ast.AST) -> set[str]:
        """Every name the scope BINDS, in any shape — not only ``x = ...``.

        A parameter, a ``for``/``with`` target, an ``AnnAssign``, a tuple unpack
        or a walrus all bind a local that :func:`_assignments` cannot resolve.
        Those must FAIL CLOSED rather than fall through to a same-named
        module constant (round-4 review), so this set is consulted first.
        """
        bound: set[str] = set()
        stack = list(scope.body) if hasattr(scope, "body") else []
        if hasattr(scope, "args"):
            args = scope.args
            for group in (args.posonlyargs, args.args, args.kwonlyargs):
                bound.update(a.arg for a in group)
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    bound.add(extra.arg)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
            stack.extend(ast.iter_child_nodes(node))
        return bound

    def _resolve(name: str, scope: ast.AST, line: int) -> ast.expr | None:
        """The assignment that DOMINATES the call: latest one at or before it."""
        candidates = [
            (assign_line, value) for assign_line, assign_name, value
            in _assignments(scope)
            if assign_name == name and assign_line <= line
        ]
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
        if name in _bound_names(scope):
            return None        # bound in a shape we cannot read: fail closed
        if scope is tree:      # module-level site: only module-level names apply
            return None
        module_level = [
            (assign_line, value) for assign_line, assign_name, value
            in _assignments(tree) if assign_name == name
        ]
        return max(module_level, key=lambda pair: pair[0])[1] if module_level else None

    # Names whose cypher genuinely CANNOT be resolved statically, each with the
    # reason it is not an ordering leg. Deliberately a dict, not a silent skip:
    # adding an entry is a visible act in review, and the size assertion below
    # keeps the hole from growing unnoticed.
    DYNAMIC_ALLOWED = {
        # (enclosing function, the ITERABLE the name comes from) — the cypher
        # here is a FOR-TARGET over ``epistemic_label_queries(template)``, whose
        # template carries no ORDER BY (an id-anchored read). Keyed by the
        # SOURCE, not by name or line: a new ``cypher`` loop in another leg must
        # not inherit the exemption, and a line shift must not break it.
        ("get_relationships", "epistemic_label_queries"):
            "for cypher in epistemic_label_queries(template) — no ORDER BY",
    }

    # Reads whose cypher carries NO literal text at all, so the census cannot
    # read a clause out of them. Keyed by (enclosing function, first 40 chars of
    # the literal text — "" when there is none) and deliberately a dict, for the
    # same reason as DYNAMIC_ALLOWED: adding an entry is a visible act in review
    # and the size assertion keeps the hole from growing unnoticed (#10121
    # review round 6).
    UNORDERED_ALLOWED = {
        # ("_query_nodes", "") — the vector index branch. Its cypher is RETURNED
        # by the nested `_sig_cypher(sig)` helper (two signatures, both wrapping
        # `recency_key_expr('node')` as a COLUMN), so the text the census can see
        # here is empty. The branch sorts in PYTHON, by the shared
        # `recency_tie_key` (search_engine.py:2495), which is the two-branch
        # agreement this PR exists for; the helper's own text is pinned by the
        # two `tests/test_search_engine_gaps.py::TestRunVectorQuery` cases that
        # read the COLUMN (`test_both_index_signatures_carry_the_recency_column`
        # for both signatures' `AS recency`, and
        # `test_index_branch_tie_follows_the_recency_column_not_the_id` for the
        # tie it feeds). Round-8 review: this comment originally named a test
        # that does not exist, and none of the `#6214`-era cases could see the
        # column at all — they return 2-column rows, so the recency read fell to
        # its `''` default and deleting `AS recency` from signature A red nothing.
        ("_query_nodes", ""):
            "cypher returned by the nested _sig_cypher helper; ranked in PYTHON "
            "by recency_tie_key — no ORDER BY by design",
    }

    checked = 0
    wandering: list[str] = []
    unresolvable: list[str] = []
    duplicated: list[str] = []
    anchored: list[str] = []
    seen_clauses: list[str] = []
    resolved_ids: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _QUERY_VERBS):
            continue
        # A cypher can also arrive as the first KEYWORD argument (#10121
        # round-5 review); a leg written that way must not slip past the walk.
        if node.args:
            cypher = node.args[0]
        elif node.keywords:
            cypher = node.keywords[0].value
        else:
            continue
        if isinstance(cypher, ast.Name):
            scope = _scope_of(node.lineno)
            resolved = _resolve(cypher.id, scope, node.lineno)
            if resolved is None:
                iterable = _for_target_iterable(scope, cypher.id, node.lineno)
                site = (getattr(scope, "name", "<module>"), iterable)
                if site not in DYNAMIC_ALLOWED:
                    unresolvable.append(
                        f"{cypher.id} in {site[0]} from {iterable}")
                continue
            # The NAME is kept before the rebind: a textless resolution can be
            # any expression (a call, an alias), which has no `.id`, and the
            # branch below must report it rather than raise `AttributeError`.
            name = cypher.id
            cypher = resolved
            # A resolved expression with NO literal text means the clause was
            # BUILT elsewhere (a helper, a module constant aliased through a
            # Name, a builder call): the census cannot judge it, so it fails
            # closed rather than counting it as clean (round-4 review).
            if not _literal_text(resolved):
                iterable = _for_target_iterable(scope, name, node.lineno)
                if (getattr(scope, "name", "<module>"), iterable) not in DYNAMIC_ALLOWED:
                    unresolvable.append(
                        f"{name} in {getattr(scope, 'name', '<module>')} "
                        f"resolved to a textless expression")
                continue
            if id(resolved) in resolved_ids:
                # Two legs must not be judged by the SAME clause — that is the
                # collapse this census exists to avoid.
                duplicated.append(getattr(_scope_of(node.lineno), "name", "?"))
            resolved_ids.add(id(resolved))
        text = _literal_text(cypher)
        if "ORDER BY" not in text:
            # A NO-ORDER-BY site is judged, never skipped (#10121 review round
            # 6: the old `continue` here made this census blind to the exact
            # shape #3019 found — a leg with no ``ORDER BY`` AT ALL — and to any
            # clause whose ORDER BY arrived through an unresolved helper or a
            # ``name + "..."`` concatenation, so the PR's "fails closed on a new
            # leg" claim was not what the code did).
            func_name = getattr(_scope_of(node.lineno), "name", "<module>")
            if _is_anchored_read(text):
                anchored.append(func_name)
                continue
            if (func_name, "" if not text.strip() else text.strip()[:40]) \
                    not in UNORDERED_ALLOWED:
                wandering.append(
                    f"{func_name}: no ORDER BY and no id-anchor/aggregate marker: "
                    f"{text.strip()[:100] or '<no literal text>'}")
            continue
        checked += 1
        seen_clauses.append(_literal_text(cypher).strip()[:120])
        if not _calls_tie(cypher):
            wandering.append(text.strip()[:120])

    assert checked >= 6, (
        "FIXTURE NOT REACHED: the query-site census found fewer ordering sites "
        f"than the six legs define ({checked})")
    assert len(set(seen_clauses)) >= 6, (
        "the census counted six sites but fewer DISTINCT clauses, so at least "
        f"one leg was validated against another leg's text: {seen_clauses}")
    assert not duplicated, (
        "two legs resolved to the SAME cypher assignment, so one of them was "
        f"never judged (#10121 review): {duplicated}")
    assert anchored, (
        "FIXTURE NOT REACHED: not one id-anchored/aggregate read was recognised, "
        "so the no-ORDER-BY arm of this census judged nothing")
    assert len(UNORDERED_ALLOWED) == 1, (
        "the no-ORDER-BY allow-list grew — every ADDED entry is a read this "
        f"census can no longer see: {UNORDERED_ALLOWED}")
    assert len(DYNAMIC_ALLOWED) == 1, (
        "the unresolvable-name allow-list grew — every entry is a leg the "
        f"census cannot see: {DYNAMIC_ALLOWED}")
    assert not unresolvable, (
        "the census could not resolve these cypher variables in their own "
        f"function scope, so it cannot judge them: {unresolvable}")
    assert not wandering, (
        "a read reached the graph either with an ORDER BY that lacks the shared "
        "recency tie, or with NO ordering and no id-anchor/aggregate marker (a "
        "leg returning rows in DB order — the shape #3019 found). This is also "
        "the shape the f-string scan cannot see (a plain string, a "
        "concatenation, ``.format``, or a helper call) — the leg is either "
        "leaving its tie to DB row order or re-inventing the oldest-first key "
        f"#10121 removed: {wandering}"
    )
