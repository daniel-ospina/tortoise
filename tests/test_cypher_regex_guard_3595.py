"""#3595 — the unsupported Cypher ``=~`` operator must FAIL LOUDLY, not silently.

FalkorDB does not implement Cypher's ``=~`` regex-match operator, and it does
not raise. It prints ``FalkorDB does not currently support =~`` IN PLACE OF
RESULTS, so the surrounding query returns an **empty result set** — a confident
false negative indistinguishable from "no matches". Two agents in one session
read exactly that: enumerating "does any graph hold a legacy ``obj-<26hex>``
id" with ``=~ '^[a-z]{2,3}-[0-9a-f]{26}$'`` returned 0 across every graph,
where the supported ``STARTS WITH`` found 5 legacy-prefixed nodes in 2 graphs
(``graphops_measure_tmp`` = 4, ``probe2517props`` = 1).

The fix is a guard at the HANDLE-PRODUCING seam. ``tortoise/cypher_guard.py``
wraps the vendor FalkorDB CLIENT, so the ``select_graph`` on a client the
package builds returns a guarded ``Graph`` subclass that refuses the statement
on EVERY query entry point (``query`` / ``ro_query`` / ``_query`` / ``profile``
/ ``explain``) BEFORE it is sent. The projection's own handle wrapper
(``_GuardedGraph``, the ``proj.g`` path) applies the same refusal, and the
seam tests below drive that (a raw ``select_graph`` handle is refused too).

Doctrine (TEST-DOCTRINE.md, Class B) — every test below answers both questions:

  (1) **what value makes this test fail?** The load-bearing falsifiers fail
      when the guard is ABSENT: the guarded handle then forwards the
      ``=~`` statement to the wire (no exception, and the spy records the
      call). Concretely, the value is the query text
      ``LEGACY_SHAPE_CHECK`` — a real ``=~`` OPERATOR, outside any string
      literal.
  (2) **is that value reachable in the fixture?** Yes — ``LEGACY_SHAPE_CHECK``
      is constructed verbatim below (the real near-miss from the issue), and
      the spy handle proves the guard saw exactly that text and withheld it.

These tests are PURE UNIT — no FalkorDB store, no Docker, no embedded server.
The seam tests do use the REAL ``falkordb.Graph`` class, but with a spy
``execute_command`` in place of the redis connection, so the proof is that the
refusal happens in Python before any I/O — a guard test that needed a live
handle to prove a PRE-dispatch refusal would be needlessly fragile.

The anti-overfix guards pass on the pre-fix revision BY DESIGN — they defend
the contracts this fix must not break: supported operators must still reach the
graph, a ``=~`` that is DATA (a string literal or a parameter) or a NAME
(backtick-quoted identifier) must not trip, and the Python-side id regexes
(which contain ``=~`` in a character class) must be untouched.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402, RUF100

from tortoise.cypher_guard import guarded_client  # noqa: E402, RUF100
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

def test_real_legacy_shape_check_is_refused_at_the_projection_wrapper():
    """Falsifier (guard removed -> RED): the exact #3595 query raises.

    Value that makes it fail: ``LEGACY_SHAPE_CHECK``, a ``=~`` OPERATOR in
    query text. Reachable in the fixture: it is the module constant above,
    handed straight to the ``proj.g`` wrapper.
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
    parametrize list is handed to the same guard entry point; the spy must record it.
    """
    g, handle = _guarded()
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_tilde_equals_inside_a_string_literal_is_data_not_an_operator():
    """Anti-overfix guard: ``=~`` quoted as DATA must NOT trip the guard.

    Value that makes it fail: a literal containing ``=~``. Reachable: passed to
    the guard entry point; the spy must record it (the guard strips literals first).
    """
    g, handle = _guarded()
    cypher = "MATCH (n) WHERE n.content = 'explain a =~ b' RETURN n.id"
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_tilde_equals_inside_a_comment_is_prose_not_an_operator():
    """Anti-overfix guard: ``=~`` in a Cypher comment must NOT trip the guard.

    Value that makes it fail: a comment containing ``=~``. Reachable: passed to
    the guard entry point; the spy must record it.
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
    # A BACKTICK-quoted identifier may contain any character except a backtick,
    # so `a=~b` there is a NAME, not the operator (openCypher).
    assert _unsupported_cypher_operator("MATCH (n:`a=~b`) RETURN n") is None
    assert _unsupported_cypher_operator(
        "MATCH (n) RETURN n.`p=~q`") is None
    # …but a real operator outside a backtick identifier is still refused.
    assert _unsupported_cypher_operator(
        "MATCH (n:`legacy id`) WHERE n.id =~ 'x' RETURN n") == "=~"
    # An unterminated backtick consumes the remainder rather than inventing one.
    assert _unsupported_cypher_operator("MATCH (n:`a=~b) RETURN n") is None


def test_tilde_equals_inside_a_backtick_identifier_is_a_name_not_an_operator():
    """Anti-overfix guard: ``=~`` in a backtick identifier must NOT trip.

    Backticks quote an identifier and openCypher permits any character except a
    backtick inside them, so ``MATCH (n:`a=~b`)`` is legitimate Cypher. A
    scanner blind to backticks blocks it — a false positive. Value that makes
    it fail: the backtick-quoted query below; reachable: handed to the same
    guard entry point as the falsifiers.
    """
    g, handle = _guarded()
    for cypher in (
        "MATCH (n:`a=~b`) RETURN n",
        "MATCH (n) RETURN n.`p=~q`",
    ):
        assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [
        ("MATCH (n:`a=~b`) RETURN n", None, None),
        ("MATCH (n) RETURN n.`p=~q`", None, None),
    ]


def test_python_regex_character_class_as_data_does_not_trip_the_guard():
    """Drives the real claim: a PYTHON regex is DATA on the wire, never the operator.

    ``sdk._DIGEST_STRUCTURE_RE`` is the only Python-side pattern containing
    ``=~`` — inside a CHARACTER CLASS, ``[=~]``. If such a pattern ever reaches
    Cypher it arrives as a string literal (the way a regex should be passed),
    and the guard must read that ``=~`` as DATA. This test drives the scanner
    (unlike its predecessor, which only compiled Python regexes and so could not
    fail for any value the guard took).

    Value that makes it fail: the pattern text of the real Python regex,
    embedded in a Cypher literal. Reachable: taken straight off the real
    compiled object and handed to the guarded query text.
    """
    from tortoise import sdk

    pattern = sdk._DIGEST_STRUCTURE_RE.pattern
    assert "=~" in pattern, "premise: the Python regex carries a [=~] class"
    assert "'" not in pattern, "premise: embeddable in a single-quoted literal"
    assert sdk._is_entity_id("ab-0123456789abcdef0123456789")
    assert sdk._ENTITY_ID_RE.match("obj-0123456789abcdef0123456789")
    assert sdk._DIGEST_STRUCTURE_RE.match("===")

    g, handle = _guarded()
    cypher = f"MATCH (n) WHERE n.p = '{pattern}' RETURN n.id"
    assert g.query(cypher) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, None, None)]


def test_regex_pattern_as_a_query_parameter_does_not_trip():
    """Anti-overfix guard: a regex passed as DATA (``$pat``) is not an operator.

    Value that makes it fail: the guarded query text carries no ``=~`` — the
    pattern travels in ``params``. Reachable: handed to the guarded handle with
    the parameter dict; the spy must record both.
    """
    g, handle = _guarded()
    cypher = "MATCH (n) WHERE n.id = $pat RETURN n.id"
    params = {"pat": "^[a-z]{2,3}-[0-9a-f]{26}$"}
    assert g.query(cypher, params=params) == "SENTINEL-EMPTY-RESULT"
    assert handle.calls == [(cypher, params, None)]


# ── the HANDLE-PRODUCING seam: a raw `select_graph` handle is guarded ───────

class _SpyWire:
    """Stand-in for the redis connection: any command reaching it is recorded."""

    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def execute_command(self, *args, **kwargs):
        self.commands.append(args)
        return [[], [], []]  # a valid empty result set


class _VendorClientWithRealGraph:
    """A client whose ``select_graph`` returns the REAL vendor ``falkordb.Graph``.

    Deliberately NOT a fake graph class: the seam must work on the vendor's own
    ``Graph``, whatever ``select_graph`` yields, so this wraps the real one with
    a spy connection.
    """

    def __init__(self, wire: _SpyWire) -> None:
        self._wire = wire

    def select_graph(self, graph_id):
        from falkordb import Graph as VendorGraph

        return VendorGraph(self._wire, graph_id)


def _guarded_vendor_handle() -> tuple[object, _SpyWire]:
    wire = _SpyWire()
    client = guarded_client(_VendorClientWithRealGraph, wire)
    return client.select_graph("g"), wire


def test_raw_select_graph_handle_refuses_the_operator():
    """Falsifier (guard removed -> RED): a ``select_graph(...)`` handle is guarded.

    This is the P1 regression: the guard must live on the HANDLE, not on one
    call site. Value that makes it fail: ``LEGACY_SHAPE_CHECK`` sent to a handle
    returned by ``select_graph`` — the ~150+ call sites the previous fix missed.
    Reachable: the handle comes off the real vendor graph class, and the spy
    proves nothing reached the wire.
    """
    g, wire = _guarded_vendor_handle()
    with pytest.raises(UnsupportedCypherOperatorError) as exc:
        g.query(LEGACY_SHAPE_CHECK)
    assert exc.value.operator == "=~"
    assert wire.commands == [], "the unsupported statement reached the wire"


def test_raw_select_graph_handle_refuses_ro_query():
    """Falsifier: the alternate public read verb is guarded too (P1).

    The previous wrapper forwarded ``ro_query`` to the raw handle, so a ``=~``
    written against it still returned an empty result set. Value that makes it
    fail: ``LEGACY_SHAPE_CHECK`` through ``ro_query``.
    """
    g, wire = _guarded_vendor_handle()
    with pytest.raises(UnsupportedCypherOperatorError):
        g.ro_query(LEGACY_SHAPE_CHECK)
    assert wire.commands == [], "ro_query forwarded the unsupported statement"


@pytest.mark.parametrize("verb", ["_query", "profile", "explain"])
def test_raw_select_graph_handle_refuses_every_other_query_entry_point(verb):
    """Falsifier: ``_query`` / ``profile`` / ``explain`` are not escape hatches.

    The vendor's ``profile`` / ``explain`` issue ``GRAPH.PROFILE`` /
    ``GRAPH.EXPLAIN`` WITHOUT routing through ``_query``, and ``_query`` is the
    verb both public methods delegate to — so each needs its own refusal.
    """
    g, wire = _guarded_vendor_handle()
    with pytest.raises(UnsupportedCypherOperatorError):
        getattr(g, verb)(LEGACY_SHAPE_CHECK)
    assert wire.commands == []


def test_raw_select_graph_supported_query_reaches_the_wire():
    """Anti-overfix guard: the guarded handle still passes supported queries."""
    g, wire = _guarded_vendor_handle()
    g.query("MATCH (n) WHERE n.id STARTS WITH 'obj-' RETURN n.id")
    assert len(wire.commands) == 1, wire.commands


def test_projection_wrapper_refuses_on_every_query_entry_point():
    """Falsifier: ``_GuardedGraph`` refuses on all five entry points itself.

    ``__getattr__`` forwards un-overridden attributes to the inner handle; every
    QUERY entry point must be overridden, not forwarded, or the wrapper is an
    escape hatch on its own.
    """
    for verb in ("query", "ro_query", "_query", "profile", "explain"):
        g, handle = _guarded()
        with pytest.raises(UnsupportedCypherOperatorError):
            getattr(g, verb)(LEGACY_SHAPE_CHECK)
        assert handle.calls == [], f"{verb} reached the raw handle"
