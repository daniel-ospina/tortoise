"""#7174 — the #4647 numeric guard must be a SEAM, not a call-site list.

FalkorDB stores an integer as INT64 and a number as a double. A Python ``int``
is unbounded and ``decimal.Decimal`` is arbitrary-precision, so a value outside
those domains is SILENTLY ALTERED: ``SET n.v = $v`` reports success and stores
``9223372036854775807`` for ``2**70`` (#4647, reproduced against a live store).

#7007 refused that from a HAND-MAINTAINED list of SDK call sites. The list
leaked in FIVE consecutive review cycles — each round found another unguarded
writer — because the set of writers is open, and a list cannot be complete.
#7174 makes the refusal structural: the predicate moved to
``tortoise.numeric_domain`` and ``tortoise.cypher_guard`` applies it to every
param map on every guarded handle, which is the one boundary that does not have
to be remembered.

Each FALSIFIER below names the value that makes it fail (TEST-DOCTRINE, Class
B): they fail on the pre-fix revision, where the out-of-domain number rides
through to the handle and the spy records it. The ANTI-OVERFIX guards are the
other class — they pass on unmodified code by design, and each one names the
wrong implementation it pins (an over-refusing predicate, a retired dead branch,
a dropped refusal).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402, RUF100

from tortoise.cypher_guard import (  # noqa: E402, RUF100
    guarded_client,
)
from tortoise.exceptions import (  # noqa: E402, RUF100
    UnrepresentableNumberError,
    UnsupportedCypherOperatorError,
)
from tortoise.projection import FalkorProjection, _GuardedGraph  # noqa: E402, RUF100

#: The load-bearing value: outside INT64, silently clamped by the store.
OUT_OF_DOMAIN = 2 ** 70

#: The frontier is the STORE's limit, never a digit count — so these two must
#: diverge (max is representable, max + 1 is not). The predicate is exactness,
#: which is why a digit-count proxy was wrong in both directions.
INT64_MAX = 2 ** 63 - 1
INT64_BEYOND = 2 ** 63

#: A query whose param carries the number. The guard is domain-only, so the
#: statement text is irrelevant to the refusal — deliberately: a write-only
#: gate would need Cypher write-detection, its own source of false refusals.
CYPHER = "MATCH (n {v: $v}) RETURN n"


class _FakeHandle:
    """Stand-in for a graph handle: records EVERY verb that reaches the wire."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _record(self, verb: str, *args, **kwargs):
        self.calls.append((verb, args, kwargs))
        return "SENTINEL-REACHED-HANDLE"

    def query(self, q, params=None, timeout=None):
        return self._record("query", q, params, timeout)

    def ro_query(self, q, params=None, timeout=None):
        return self._record("ro_query", q, params, timeout)

    def _query(self, q, params=None, timeout=None, read_only=False):
        return self._record("_query", q, params, timeout, read_only)

    def profile(self, q, params=None):
        return self._record("profile", q, params)

    def explain(self, q, params=None):
        return self._record("explain", q, params)

    def execute_command(self, *args, **kwargs):
        return self._record("execute_command", *args, **kwargs)


class _StubProj:
    """Minimal projection for ``_GuardedGraph`` — the wipe branch is unreachable."""

    _skip_guard = False
    _is_embedded = True
    _graph_name = "test_7174_param_boundary_numeric_guard"

    def _assert_test_graph(self, reason: str = "") -> None:  # pragma: no cover
        raise AssertionError("bulk-wipe guard reached by a non-wipe query")


def _guarded() -> tuple[_GuardedGraph, _FakeHandle]:
    handle = _FakeHandle()
    return _GuardedGraph(handle, _StubProj()), handle


class _SpyWire:
    """Stand-in for the redis connection: any command reaching it is recorded."""

    def __init__(self) -> None:
        self.commands: list[tuple] = []

    def execute_command(self, *args, **kwargs):
        self.commands.append(args)
        return [[], [], []]  # a valid empty result set


class _VendorClientWithRealGraph:
    """A client whose ``select_graph`` returns the REAL vendor ``falkordb.Graph``."""

    def __init__(self, wire: _SpyWire) -> None:
        self._wire = wire

    def select_graph(self, graph_id):
        from falkordb import Graph as VendorGraph

        return VendorGraph(self._wire, graph_id)


def _guarded_vendor_handle() -> tuple[object, _SpyWire]:
    wire = _SpyWire()
    client = guarded_client(_VendorClientWithRealGraph, wire)
    return client.select_graph("g"), wire


# ── falsifiers: the out-of-domain number is refused BEFORE the handle ───────

def test_proj_g_refuses_an_out_of_domain_integer_param():
    """Falsifier (guard absent -> RED): ``proj.g`` refuses the #4647 value.

    Value that makes it fail: ``2**70`` in ``params``. Reachable: handed to the
    ``proj.g`` wrapper; the spy must be empty (pre-fix it records the call and
    the store then clamps the number).
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert handle.calls == [], "the out-of-domain number reached the graph handle"


def test_falkorprojection_query_delegates_into_the_seam():
    """Falsifier: the public ``FalkorProjection.query`` lane is covered too.

    Value that makes it fail: ``2**70`` sent through ``FalkorProjection.query``
    -> ``self.g.query``. Reachable: the projection is built with an injected
    fake handle, so no DB is opened and the delegation is exercised directly.
    """
    handle = _FakeHandle()
    proj = object.__new__(FalkorProjection)  # no DB / no __init__
    proj.g = _GuardedGraph(handle, proj)
    proj._skip_guard = False
    proj._is_embedded = True
    proj._graph_name = "test_7174_param_boundary_numeric_guard"
    with pytest.raises(UnrepresentableNumberError):
        proj.query(CYPHER, v=OUT_OF_DOMAIN)
    assert handle.calls == [], "FalkorProjection.query bypassed the seam"


def test_the_refusal_names_the_param_key_and_the_range():
    """Falsifier: the message must be actionable — which KEY, which range."""
    g, _ = _guarded()
    with pytest.raises(UnrepresentableNumberError) as exc:
        g.query(CYPHER, {"v": OUT_OF_DOMAIN})
    message = str(exc.value)
    assert "'v'" in message
    assert "INT64" not in message or "range" in message
    assert str(INT64_MAX) in message, "the message must name the storable range"
    assert "string" in message, "the message must name the faithful alternative"


def test_raw_select_graph_handle_refuses_the_same_param():
    """Falsifier: the cypher_guard SEAM covers the raw ``select_graph`` handle.

    The ~150+ SDK/hosted/direct call sites reach the store through a handle off
    a client the package built — the path the call-site list never covered.
    Value: ``2**70`` sent to a handle off the real vendor graph class.
    """
    handle, wire = _guarded_vendor_handle()
    with pytest.raises(UnrepresentableNumberError):
        handle.query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert wire.commands == [], "the out-of-domain number reached the wire"


@pytest.mark.parametrize("verb", ["query", "ro_query", "_query", "profile", "explain"])
def test_every_query_entry_point_on_the_seam_refuses(verb):
    """Falsifier: no query VERB may be an unguarded way around the domain check."""
    handle, wire = _guarded_vendor_handle()
    with pytest.raises(UnrepresentableNumberError):
        getattr(handle, verb)(CYPHER, {"v": OUT_OF_DOMAIN})
    assert wire.commands == [], f"{verb} forwarded the out-of-domain number"


@pytest.mark.parametrize("verb", ["query", "ro_query", "_query", "profile", "explain"])
def test_every_query_entry_point_on_the_projection_wrapper_refuses(verb):
    """Falsifier: the ``proj.g`` wrapper stays complete on its own.

    ``_GuardedGraph`` deliberately refuses without depending on the inner
    handle's class (the #3595 precedent), so the guard must be on BOTH lanes.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        getattr(g, verb)(CYPHER, {"v": OUT_OF_DOMAIN})
    assert handle.calls == [], f"{verb} forwarded the out-of-domain number"


def test_duck_typed_handle_proxy_refuses_before_the_stub_sees_it():
    """Falsifier: the delegating proxy lane (a stub handle) is guarded too."""

    class _StubHandle:
        def __init__(self):
            self.calls: list = []

        def query(self, q, params=None):
            self.calls.append((q, params))
            return "OK"

    class _ClientReturningStub:
        def __init__(self, handle):
            self._handle = handle

        def select_graph(self, graph_id):
            return self._handle

    stub = _StubHandle()
    guard = guarded_client(_ClientReturningStub, stub).select_graph("g")
    with pytest.raises(UnrepresentableNumberError):
        guard.query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert stub.calls == [], "the stub saw the out-of-domain number"


def test_the_raw_execute_command_channel_has_no_positional_params():
    """Scope pin: ``execute_command`` carries NO positional params argument.

    MEASURED against the pinned vendor (``falkordb`` 1.6.2, ``graph.py::_query``):
    params are INLINED into the statement (``_build_params_header(params) + q``)
    and the command vector is ``[cmd, name, query, "--compact", ...]`` — so
    ``args[3]`` is a flag such as ``--compact``, never a payload. An earlier
    revision of this change decoded ``args[3]`` as JSON params, which was dead
    code asserting a protocol that does not exist; this test is what keeps that
    claim from returning.

    Value that makes it fail: a JSON blob in ``args[3]``. No client produces
    one, so the seam must NOT read it as params — the command is forwarded.
    """
    g, handle = _guarded()
    assert (
        g.execute_command(
            "GRAPH.QUERY", "g", CYPHER, json.dumps({"v": OUT_OF_DOMAIN})
        )
        == "SENTINEL-REACHED-HANDLE"
    )
    assert handle.calls, "the raw command must still be forwarded to the handle"


def test_a_numeric_literal_in_the_statement_text_is_NOT_covered_here():
    """Scope pin: a number inlined in the Cypher TEXT is a different mechanism.

    ``CREATE (n:X {v: 1180591620717411303424})`` with no ``params`` is clamped by
    the store exactly as a bound param is, and a PARAM-boundary seam cannot see
    it — catching it needs a scan of the statement text. The residual is
    declared on the issue and in ``_guard_numeric_params``'s docstring instead
    of being silently assumed covered; this test makes covering it a deliberate
    change to this file.

    Value that makes it fail: the statement with the literal inlined. It must
    REACH the handle — the honest statement of this seam's scope, not a claim
    that the store will hold the number.
    """
    g, handle = _guarded()
    literal = f"CREATE (n:X {{v: {OUT_OF_DOMAIN}}})"
    assert g.query(literal) == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", (literal, None, None), {})]


def test_a_read_query_is_refused_too_by_policy():
    """Falsifier: the refusal is uniform, deliberately NOT write-gated.

    Only the WRITE side is measured (``SET n.v = $v`` stores the clamped value);
    the read is refused on the same doctrine, because a read cannot be ASSUMED to
    compare against the caller's number once the store has altered it. This test
    pins the policy so a later "optimisation" gating the guard on write clauses
    has to argue for the read it stops guarding.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.ro_query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert handle.calls == []


# ── anti-overfix guards: contracts the fix must not break ───────────────────

def test_representable_params_reach_the_handle_unchanged():
    """Anti-overfix guard: no FALSE refusal, and params are forwarded verbatim.

    Value that makes it fail: an entirely ordinary param map — a float, a
    string, a bool (an ``int`` subclass that IS representable), a list of
    doubles, a nested dict, and the big number passed as a STRING (the
    documented faithful representation). Reachable: handed to the same guard.
    """
    g, handle = _guarded()
    params = {
        "v": 1.5,
        "s": "x",
        "b": True,
        "b0": False,
        "list": [1.0, 2.0],
        "nested": {"deep": [{"n": 1}]},
        "big_as_str": str(OUT_OF_DOMAIN),
        "int_ok": INT64_MAX,
    }
    assert g.query(CYPHER, params) == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", (CYPHER, params, None), {})]


def test_the_frontier_is_the_stores_limit_not_a_digit_count():
    """Anti-overfix guard: the predicate is EXACTNESS at the store's boundary.

    Value that makes it fail: ``2**63 - 1`` (representable, must pass) versus
    ``2**63`` (clamped, must refuse), plus ``float(2**63)`` — a DOUBLE, which
    the store holds exactly, so it must pass despite being numerically equal to
    the refusing integer. A digit-count proxy gets all three wrong.
    """
    g, handle = _guarded()
    assert g.query(CYPHER, {"v": INT64_MAX}) == "SENTINEL-REACHED-HANDLE"
    assert g.query(CYPHER, {"v": float(INT64_BEYOND)}) == "SENTINEL-REACHED-HANDLE"
    assert len(handle.calls) == 2, "the representable values must pass"
    with pytest.raises(UnrepresentableNumberError):
        g.query(CYPHER, {"v": INT64_BEYOND})
    assert len(handle.calls) == 2, "the beyond-range integer must not pass"


def test_a_nested_container_is_actually_walked():
    """Anti-overfix guard: the walk is RECURSIVE, so nesting is not a bypass."""
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.query(CYPHER, {"props": {"alpha": [1.0, OUT_OF_DOMAIN]}})
    assert handle.calls == []


def test_a_decimal_that_rounds_is_refused_and_an_exact_one_is_not():
    """Anti-overfix guard: the double axis, not only the INT64 axis."""
    import decimal

    g, handle = _guarded()
    assert (
        g.query(CYPHER, {"v": decimal.Decimal("0.5")})
        == "SENTINEL-REACHED-HANDLE"
    )
    with pytest.raises(UnrepresentableNumberError):
        g.query(CYPHER, {"v": decimal.Decimal("0.12345678901234567890")})
    assert len(handle.calls) == 1


def test_no_params_is_not_a_refusal():
    """Anti-overfix guard: a param-less query is untouched."""
    g, handle = _guarded()
    assert g.query("MATCH (n) RETURN n") == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", ("MATCH (n) RETURN n", None, None), {})]


def test_unsupported_operator_still_refused_first():
    """Anti-overfix guard: combining the two refusals did not drop either."""
    g, handle = _guarded()
    with pytest.raises(UnsupportedCypherOperatorError):
        g.query("MATCH (n) WHERE n.id =~ 'x' RETURN n", {"v": OUT_OF_DOMAIN})
    assert handle.calls == []


# ── the structural claim: ONE predicate home, so the two consumers cannot drift

def test_both_consumers_use_the_same_predicate_object():
    """Falsifier: a consumer keeping its OWN predicate copy would red this.

    What this pins: ``tortoise.sdk``'s ``_reject_unrepresentable_number`` and
    ``tortoise.cypher_guard``'s predicate ARE the same module-level function, so
    the early refusal (before the in-memory mutation and the journal emit) and
    the structural refusal cannot drift apart.

    What it does NOT pin: a predicate reimplemented INLINE inside either
    consumer. That is the behaviour tests' job, not this one's — stated so the
    test is not read as stronger than it is.
    """
    from tortoise import cypher_guard, numeric_domain, sdk

    assert (
        cypher_guard.numeric_alteration_reason
        is numeric_domain.numeric_alteration_reason
    )
    assert (
        sdk._reject_unrepresentable_number
        is numeric_domain.reject_unrepresentable_number
    )


def test_the_sdk_early_refusal_still_stands():
    """Anti-regression: the #7007 call-site guards stay (they are now EARLY)."""
    from tortoise.sdk import _reject_unrepresentable_number

    with pytest.raises(ValueError):
        _reject_unrepresentable_number("alpha", OUT_OF_DOMAIN)
    _reject_unrepresentable_number("alpha", 1.5)


def test_a_non_mapping_params_payload_is_forwarded_not_guessed():
    """Anti-overfix guard: only a ``dict`` is a param map.

    ``params=[...]`` is not a channel the vendor accepts — the pinned vendor
    rejects it with ``TypeError("'params' must be a dict")`` before reading any
    value — so the seam does not invent a refusal for it and the vendor raises
    its own error. An earlier revision walked sequences under ``"0"``/``"1"``
    keys, which guarded a shape no client can send (the same mistake as the
    retired ``args[3]`` decode).
    """
    g, handle = _guarded()
    assert g.query(CYPHER, [OUT_OF_DOMAIN]) == "SENTINEL-REACHED-HANDLE"
    assert g.query(CYPHER, "not-a-mapping") == "SENTINEL-REACHED-HANDLE"
    assert len(handle.calls) == 2
