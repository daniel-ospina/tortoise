"""#3595 — the unsupported Cypher ``=~`` operator must FAIL LOUDLY, not silently.

FalkorDB does not implement Cypher's ``=~`` regex-match operator, and it does
not raise. It prints ``FalkorDB does not currently support =~`` IN PLACE OF
RESULTS, so the surrounding query returns an **empty result set** — a confident
false negative indistinguishable from "no matches". Two agents in one session
read exactly that: enumerating "does any graph hold a legacy ``obj-<26hex>``
id" with ``=~ '^[a-z]{2,3}-[0-9a-f]{26}$'`` returned 0 across every graph,
where the supported ``STARTS WITH`` found 5 legacy-prefixed nodes in 2 graphs
(``graphops_measure_tmp`` = 4, ``probe2517props`` = 1).

The fix is a guard at the shared graph-query chokepoint
(``tortoise.projection._GuardedGraph.query``) that refuses the statement BEFORE
it is sent, naming the supported alternatives.

Doctrine (TEST-DOCTRINE.md, Class B) — every test below answers both questions:

  (1) **what value makes this test fail?** The load-bearing falsifiers fail
      when the guard is ABSENT: ``_GuardedGraph.query`` then forwards the
      ``=~`` statement to the handle (no exception, and the spy records the
      call). Concretely, the value is the query text
      ``LEGACY_SHAPE_CHECK`` — a real ``=~`` OPERATOR, outside any string
      literal.
  (2) **is that value reachable in the fixture?** Yes — ``LEGACY_SHAPE_CHECK``
      is constructed verbatim below (the real near-miss from the issue), and
      the spy handle proves the guard saw exactly that text and withheld it.

These tests are PURE UNIT — no FalkorDB, no Docker, no embedded store. The
shared docker lane defrags and hangs TCP clients, so a guard test that needed a
live handle to prove a PRE-dispatch refusal would be needlessly fragile: the
refusal happens in Python, before any I/O.

The anti-overfix guards pass on the pre-fix revision BY DESIGN — they defend
the contracts this fix must not break: supported operators must still reach the
graph, a ``=~`` that is DATA (a string literal) must not trip, and the
Python-side id regexes (which contain ``=~`` in a character class) must be
untouched.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402, RUF100

from tortoise.exceptions import UnsupportedCypherOperatorError  # noqa: E402, RUF100
from tortoise.projection import (  # noqa: E402, RUF100
    FalkorProjection,
    _GuardedGraph,
    _unsupported_cypher_operator,
)

#: The REAL near-miss query from #3595. The ``=~`` is an OPERATOR (outside any
#: string literal); the regex after it is the legacy ``obj-<26hex>`` shape.
#: This is the exact value that makes the falsifiers below fail on unguarded code.
LEGACY_SHAPE_CHECK = (
    "MATCH (n) WHERE n.id =~ '^[a-z]{2,3}-[0-9a-f]{26}$' RETURN n.id"
)


class _FakeHandle:
    """Stand-in for a FalkorDB graph handle: records what reaches the wire."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def query(self, cypher, params=None, timeout=None):
        self.calls.append((cypher, params, timeout))
        return "SENTINEL-EMPTY-RESULT"


class _StubProj:
    """Minimal projection for ``_GuardedGraph`` — the bulk-wipe branch is only
    reached for DETACH DELETE, which none of these queries are."""

    _skip_guard = False
    _is_embedded = True
    _graph_name = "test_cypher_regex_guard_3595"

    def _assert_test_graph(self, reason: str = "") -> None:  # pragma: no cover
        raise AssertionError("bulk-wipe guard reached by a non-wipe query")


def _guarded() -> tuple[_GuardedGraph, _FakeHandle]:
    handle = _FakeHandle()
    return _GuardedGraph(handle, _StubProj()), handle


# ── falsifiers: the guard refuses the real near-miss ────────────────────────

def test_real_legacy_shape_check_is_refused_at_the_chokepoint():
    """Falsifier (guard removed -> RED): the exact #3595 query raises.

    Value that makes it fail: ``LEGACY_SHAPE_CHECK``, a ``=~`` OPERATOR in
    query text. Reachable in the fixture: it is the module constant above,
    handed straight to the shared chokepoint.
    """
    g, handle = _guarded()
    with pytest.raises(UnsupportedCypherOperatorError) as exc:
        g.query(LEGACY_SHAPE_CHECK)
    assert exc.value.operator == "=~"
    assert handle.calls == [], "the unsupported statement reached the graph handle"


def test_refusal_message_names_operator_falkordb_and_alternatives():
    """Falsifier: the error must be actionable, not a bare raise.

    Value that makes it fail: the message must name ``=~``, FalkorDB, and all
    three supported alternatives (``STARTS WITH`` / ``ENDS WITH`` /
    ``CONTAINS``). Reachable: the guard raises this exact message from
    ``LEGACY_SHAPE_CHECK``.
    """
    g, _ = _guarded()
    with pytest.raises(UnsupportedCypherOperatorError) as exc:
        g.query(LEGACY_SHAPE_CHECK)
    msg = str(exc.value)
    assert "=~" in msg
    assert "FalkorDB" in msg
    assert "STARTS WITH" in msg
    assert "ENDS WITH" in msg
    assert "CONTAINS" in msg
    assert "SILENTLY" in msg, "the silent false-negative is the whole point"


def test_falkorprojection_query_delegates_into_the_guard():
    """Falsifier: the public ``FalkorProjection.query`` path is covered too.

    Value that makes it fail: ``LEGACY_SHAPE_CHECK`` sent through
    ``FalkorProjection.query`` -> ``self.g.query`` (the ``_GuardedGraph``).
    Reachable: the projection is built with an injected fake handle, so no DB
    is opened and the delegation is exercised directly.
    """
    handle = _FakeHandle()
    proj = object.__new__(FalkorProjection)  # no DB / no __init__
    proj.g = _GuardedGraph(handle, proj)
    proj._skip_guard = False
    proj._is_embedded = True
    proj._graph_name = "test_cypher_regex_guard_3595"
    with pytest.raises(UnsupportedCypherOperatorError):
        proj.query(LEGACY_SHAPE_CHECK)
    assert handle.calls == [], "FalkorProjection.query bypassed the guard"


# ── anti-overfix guards: contracts the fix must not break ───────────────────

@pytest.mark.parametrize("cypher", [
    "MATCH (n) WHERE n.id STARTS WITH 'obj-' RETURN n.id",
    "MATCH (n) WHERE n.name ENDS WITH '-hex' RETURN n.name",
    "MATCH (n) WHERE n.content CONTAINS 'legacy' RETURN n.id",
])
def test_supported_operators_still_reach_the_handle(cypher):
    """Anti-overfix guard: the three supported alternatives pass through.

    Value that makes it fail: a supported operator query. Reachable: the
    parametrize list is handed to the same chokepoint; the spy must record it.
    """
    g, handle = _guarded()
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_tilde_equals_inside_a_string_literal_is_data_not_an_operator():
    """Anti-overfix guard: ``=~`` quoted as DATA must NOT trip the guard.

    Value that makes it fail: a literal containing ``=~``. Reachable: passed to
    the chokepoint; the spy must record it (the guard strips literals first).
    """
    g, handle = _guarded()
    cypher = "MATCH (n) WHERE n.content = 'explain a =~ b' RETURN n.id"
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_tilde_equals_inside_a_comment_is_prose_not_an_operator():
    """Anti-overfix guard: ``=~`` in a Cypher comment must NOT trip the guard.

    Value that makes it fail: a comment containing ``=~``. Reachable: passed to
    the chokepoint; the spy must record it.
    """
    g, handle = _guarded()
    cypher = "MATCH (n) /* legacy =~ shape */ RETURN n.id"
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_helper_classifies_operator_vs_literal_directly():
    """Anti-overfix guard: the classifier's operator/data/prose boundary."""
    assert _unsupported_cypher_operator(LEGACY_SHAPE_CHECK) == "=~"
    assert _unsupported_cypher_operator("RETURN 'a =~ b'") is None
    assert _unsupported_cypher_operator('RETURN "a =~ b"') is None
    assert _unsupported_cypher_operator(
        "MATCH (n) WHERE n.p CONTAINS '=~' RETURN n") is None
    assert _unsupported_cypher_operator("MATCH (n) RETURN n") is None
    # A `=~` in Cypher PROSE is not an operator either.
    assert _unsupported_cypher_operator("// matched with =~ before\nRETURN 1") is None
    assert _unsupported_cypher_operator("RETURN 1 /* no =~ here */") is None
    # …but an operator before a comment is still refused.
    assert _unsupported_cypher_operator(
        "MATCH (n) WHERE n.id =~ 'x' // =~ again\nRETURN n") == "=~"
    # An escaped quote does not end the literal, so a `=~` after it stays DATA.
    assert _unsupported_cypher_operator(
        "RETURN 'a\\'b=~c' AS x") is None


def test_python_side_id_regexes_are_untouched():
    """Anti-overfix guard: Python regexes are NOT Cypher — never guarded.

    The Python-side id regexes live in ``tortoise.sdk`` and never pass through
    ``_GuardedGraph``, so the guard cannot see them. ``sdk._DIGEST_STRUCTURE_RE``
    is the only pattern that literally contains ``=~`` — inside a CHARACTER
    CLASS — and it is asserted here to keep compiling and matching normally.
    """
    from tortoise import sdk

    assert sdk._is_entity_id("ab-0123456789abcdef0123456789")
    assert sdk._ENTITY_ID_RE.match("obj-0123456789abcdef0123456789")
    # The character-class `=~` is a Python-regex detail; it still works.
    assert sdk._DIGEST_STRUCTURE_RE.match("===")
    assert sdk._DIGEST_STRUCTURE_RE.match("-*-")
    assert sdk._DIGEST_STRUCTURE_RE.match("...")
    assert not sdk._DIGEST_STRUCTURE_RE.match("a real sentence")


def test_regex_pattern_as_a_query_parameter_does_not_trip():
    """Anti-overfix guard: a regex passed as DATA (``$pat``) is not an operator.

    Value that makes it fail: the guarded query text carries no ``=~`` — the
    pattern travels in ``params``. Reachable: handed to the chokepoint with the
    parameter dict; the spy must record both.
    """
    g, handle = _guarded()
    cypher = "MATCH (n) WHERE n.id = $pat RETURN n.id"
    params = {"pat": "^[a-z]{2,3}-[0-9a-f]{26}$"}
    assert g.query(cypher, params=params) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, params, None)]
