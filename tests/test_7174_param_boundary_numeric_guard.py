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


class _EmptyResult:
    """The vendor ``QueryResult`` shape with no rows.

    A real ``falkordb`` handle answers a ``RETURN`` with a result object; the
    projection's Point edge fold reads the node's final ``tags`` through
    ``.result_set`` (#2897), so a fake that returns a bare forwarded-verb
    sentinel is no longer a faithful stand-in for that one read.
    """

    def __init__(self) -> None:
        self.result_set: list = []


class _FakeHandle:
    """Stand-in for a graph handle: records EVERY verb that reaches the wire."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _record(self, verb: str, *args, **kwargs):
        self.calls.append((verb, args, kwargs))
        return "SENTINEL-REACHED-HANDLE"

    def query(self, q, params=None, timeout=None):
        sentinel = self._record("query", q, params, timeout)
        # #2897: the tag-sync fold issues this one indexed RETURN and reads
        # ``.result_set`` off it; answer with an empty result (no tags) so the
        # fold is a no-op and every other assertion keeps the sentinel.
        if "RETURN n.tags" in q:
            return _EmptyResult()
        return sentinel

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


def test_a_numeric_literal_in_the_statement_text_is_refused():
    """Falsifier (guard absent -> RED): the #4647 value is refused as TEXT.

    INVERTED from the scope pin that stood here before #8479. The pin was honest
    about a real gap — a PARAM-boundary seam cannot see a number written into the
    statement — but it also ASSUMED the store clamps such a literal the way it
    clamps a param. MEASURED, that assumption is false for this shape (the Cypher
    literal parser range-checks and raises) and true for a value INLINED into a
    ``CYPHER k=<v>`` header, so the seam now covers the TEXT and the pin becomes
    an assertion of the new behaviour.

    Value that makes it fail: the statement with the literal inlined. The spy
    must stay EMPTY — pre-fix it records the call.
    """
    g, handle = _guarded()
    literal = f"CREATE (n:X {{v: {OUT_OF_DOMAIN}}})"
    with pytest.raises(UnrepresentableNumberError):
        g.query(literal)
    assert handle.calls == [], "the out-of-domain literal reached the graph handle"


# ── #8479: the SAME numeric domain, on the statement's own TEXT ─────────────
#
# MEASURED 2026-10-16 (this worktree) on THREE engines — the embedded build
# (``falkordblite`` 0.10.0, module 41803 = 4.18.3, run under
# ``env -u TORTOISE_DB_URI``) and two throwaway containers
# (``falkordb/falkordb-server:v4.20.1``, ``falkordb/falkordb:6.0.1``) — because
# the surface is NOT uniform and a param result must not be transferred to it.
# ``2**70`` in each shape, one value per shape:
#
#   shape                                    embedded 4.18.3      docker 4.20.1        docker 6.0.1
#   ---------------------------------------  -------------------  -------------------  -----------------
#   literal in CREATE map / list / SET /
#     ``SET +=`` / ``SET =`` / MERGE /
#     WITH / RETURN / WHERE / IN              Integer overflow     Integer overflow     Integer overflow
#   ``CYPHER v=2**70 ...`` INLINED header     CLAMPS to INT64_MAX  CLAMPS to INT64_MAX  Failed to parse
#   param ``$v = 2**70``                      CLAMPS to INT64_MAX  CLAMPS to INT64_MAX  Failed to parse
#   float literal ``1.0e400``                 Float overflow       Float overflow       Float overflow
#   float literal ``1.0e-400``                Float overflow       Float overflow       ACCEPTED -> 0
#
# The issue's headline claim — that a bare out-of-domain literal is CLAMPED —
# does NOT reproduce: the literal parser range-checks and raises in every clause
# position on every engine, and it is the PARAM payload that clamps. The surface
# that IS silently altered is the value inlined into a ``CYPHER k=<v>`` header,
# which is TEXT by the time it is dispatched — so one scan over the statement
# covers both. See ``cypher_guard._numeric_literal_reason`` for the full reading.

#: The statement a caller writes when the number IS the statement — no params.
LITERAL_CYPHER = f"CREATE (n:X {{v: {OUT_OF_DOMAIN}}})"

#: The same value inlined into a ``CYPHER`` param header. The vendor builds this
#: from a param MAP inside ``_query`` (where the map is already guarded), so it
#: reaches the wire as bare TEXT only via a caller that assembles the header
#: itself — and it is the shape MEASURED to be CLAMPED on 4.x rather than raise.
INLINED_HEADER_CYPHER = f"CYPHER v={OUT_OF_DOMAIN} CREATE (n:X {{v: $v}})"


def test_the_inlined_cypher_param_header_is_refused():
    """Falsifier: the one shape MEASURED to be silently altered on 4.x is refused.

    ``CYPHER v=1180591620717411303424 CREATE (n:X {v: $v})`` is CLAMPED to
    ``9223372036854775807`` by the 4.18.3 and 4.20.1 engines — a silent
    alteration, not a refusal — while the same value as a bare statement literal
    raises. It is text by dispatch time, so the text scan is the seam that can
    catch it. Value that makes it fail: this statement; the handle must stay
    empty.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.query(INLINED_HEADER_CYPHER)
    assert handle.calls == [], "the inlined header was forwarded to a 4.x clamp"


@pytest.mark.parametrize("verb", ["query", "ro_query", "_query", "profile", "explain"])
def test_every_query_entry_point_on_the_seam_refuses_a_literal(verb):
    """Falsifier: no query VERB on the guarded vendor handle skips the text scan."""
    handle, wire = _guarded_vendor_handle()
    with pytest.raises(UnrepresentableNumberError):
        getattr(handle, verb)(LITERAL_CYPHER)
    assert wire.commands == [], f"{verb} forwarded the out-of-domain literal"


@pytest.mark.parametrize("verb", ["query", "ro_query", "_query", "profile", "explain"])
def test_every_query_entry_point_on_the_projection_wrapper_refuses_a_literal(verb):
    """Falsifier: the ``proj.g`` wrapper refuses on its OWN, literal included.

    ``_GuardedGraph`` keeps its refusals complete rather than depending on the
    inner handle's class (the #3595 precedent), so a literal must be refused on
    that lane too — it does not route through ``_guard_query``.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        getattr(g, verb)(LITERAL_CYPHER)
    assert handle.calls == [], f"{verb} forwarded the out-of-domain literal"


def test_the_raw_execute_command_channel_refuses_a_literal():
    """Falsifier: the raw ``GRAPH.QUERY`` channel carries the text scan too.

    A raw command has no params map, so this is the channel where an inlined
    ``CYPHER`` header actually travels — the refusal must be here, not only on
    the ``params=`` path.
    """
    handle, wire = _guarded_vendor_handle()
    with pytest.raises(UnrepresentableNumberError):
        handle.execute_command("GRAPH.QUERY", "g", LITERAL_CYPHER)
    assert wire.commands == [], "the raw channel forwarded the literal"


@pytest.mark.parametrize("statement", [
    f"MATCH (n) WHERE n.id = '{OUT_OF_DOMAIN}' RETURN n",
    f"MATCH (n) // {OUT_OF_DOMAIN}\nRETURN n",
    f"MATCH (n) /* {OUT_OF_DOMAIN} */ RETURN n",
    f"MATCH (n:`{OUT_OF_DOMAIN}`) RETURN n",
    "MATCH (n:Point2) RETURN n.v2",
    "MATCH (n) WHERE n.id = $p1 RETURN n",
    "CREATE (n:X) SET n.v = 0x1FFFFFFFFFFFFFFF",
    "RETURN 1.5, 2, [1, 2, 3], 1e10",
    "MATCH (n) RETURN n LIMIT 10000",
    "MATCH (n) RETURN n SKIP 5 LIMIT 5",
    "RETURN x[0..5]",
    "RETURN x[1..2]",
    "RETURN x[0..9223372036854775807]",
    "RETURN x[0.5..1]",
    "RETURN [1, 2, 3][0..1]",
])
def test_the_literal_scan_reads_CODE_and_not_data(statement):
    """Anti-overfix guard: the scan cannot refuse what is not a number literal.

    A false refusal BLOCKS a legitimate query, which is its own class of harm
    even though it is loud. Each value here names what it pins: the same digits
    inside a string / ``//`` comment / block comment / backtick identifier (DATA,
    blanked by ``_mask_literals``), a digit inside a label / property name /
    parameter name (a NAME), a valid hex literal, ordinary floats and lists, and
    the repo's own inlined ``LIMIT``/``SKIP`` constants (``tortoise/audit.py``
    interpolates ``SAMPLE_LIMIT`` into Cypher this way).

    Value that makes it fail: any of these reaching the scan as a number.
    """
    g, handle = _guarded()
    assert g.query(statement) == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", (statement, None, None), {})]


@pytest.mark.parametrize("statement", [
    "RETURN 0",
    "RETURN 0.0",
    "RETURN 0.0e-400",
    "RETURN 0.1",
    "RETURN 0.12345678901234567890",
    "RETURN 1.0e-320",
    "RETURN 1.7976931348623157e308",
])
def test_ordinary_literals_reach_the_handle_unchanged(statement):
    """Anti-overfix guard: no FALSE refusal on an ordinary number literal.

    A float literal is parsed to the nearest double, which is FAITHFUL for every
    finite non-zero result — so ``0.1`` (not exactly a double) and a long decimal
    must pass, exactly as the engine parses them. The zero-denoting texts pin the
    exponent exclusion: ``0.0e-400`` denotes ZERO, and reading its exponent digits
    as significant would refuse it.
    """
    g, handle = _guarded()
    assert g.query(statement) == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", (statement, None, None), {})]


def test_the_literal_frontier_is_the_stores_limit_not_a_digit_count():
    """Anti-overfix guard: the boundary is INT64, and the SIGN is honoured.

    ``INT64_MAX`` is representable and ``INT64_MAX + 1`` is not, so the two must
    diverge. ``-9223372036854775808`` is INT64_MIN and IS representable, while
    its UNSIGNED token is ``2**63`` and is not — checking the magnitude alone
    would refuse a legal literal, which is why the scan reads the sign in front
    of the token and only ever ADMITS one extra value with it.
    """
    g, handle = _guarded()
    assert g.query(f"CREATE (n:X {{v: {INT64_MAX}}})") == "SENTINEL-REACHED-HANDLE"
    assert g.query("CREATE (n:X {v: -9223372036854775808})") == "SENTINEL-REACHED-HANDLE"
    # ``INT64_MAX + 1`` and the first value BELOW INT64_MIN. ``-(INT64_BEYOND)``
    # is deliberately NOT here: it IS INT64_MIN, i.e. the legal literal above.
    for out_of_domain in (INT64_BEYOND, -(INT64_BEYOND + 1)):
        with pytest.raises(UnrepresentableNumberError):
            g.query(f"CREATE (n:X {{v: {out_of_domain}}})")
    # The SAME frontier on a ``..`` slice bound: the shape the pre-fix walk
    # walked past by dropping its first digit.
    assert g.query(f"RETURN x[0..{INT64_MAX}]") == "SENTINEL-REACHED-HANDLE"
    with pytest.raises(UnrepresentableNumberError):
        g.query(f"RETURN x[0..{INT64_BEYOND}]")
    assert len(handle.calls) == 3, "only the representable literals may be sent"


def test_a_literal_that_leaves_the_double_domain_is_refused():
    """Falsifier: the float half of the domain is enforced on the text too.

    ``1.0e400`` overflows the double (the engine raises on every engine), and
    ``1.0e-400`` parses to ``0.0`` and is MEASURED to be stored as ``0`` on
    docker 6.0.1 — a silent alteration on the one engine that accepts it. Both
    are refused before dispatch, so the outcome no longer depends on the engine.
    """
    g, handle = _guarded()
    for statement in ("RETURN 1.0e400", "RETURN 1.0e-400", "RETURN 1.8e308"):
        with pytest.raises(UnrepresentableNumberError):
            g.query(statement)
    assert handle.calls == [], "an out-of-domain float literal reached the handle"


@pytest.mark.parametrize("statement", [
    "RETURN .5e-400",
    "RETURN .1e-400",
    "RETURN -.5e-400",
    "CREATE (n:X {v: .5e-400}) RETURN n",
    "MATCH (n) WHERE n.v = .5e-400 RETURN n",
    "RETURN .5e400",
])
def test_a_leading_dot_float_out_of_domain_is_refused(statement):
    """Falsifier: a float with NO integer part is scanned like any other.

    ``.5e-400`` is a legal openCypher double literal and denotes ``5e-401``,
    which underflows to ``0.0`` — MEASURED stored as ``0`` on docker 6.0.1 when
    the guarded handle forwarded it. The scan used to START a number token only
    on a digit, so a leading-dot literal never entered it and its exponent was
    never read (P1, review 2026-10-16); the guard's own predicate already
    refuses this token, so the miss was in the walk, not the rule. The same
    shape at the OVERFLOW end (``.5e400`` -> ``inf``) is caught by the same
    start. Value that makes it fail: any statement here; the handle must stay
    EMPTY — pre-fix it records the call.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.query(statement)
    assert handle.calls == [], "a leading-dot out-of-domain float reached the handle"


@pytest.mark.parametrize("statement", [
    "RETURN .5",
    "RETURN .5e10",
    "RETURN .0e-400",
    "RETURN -.5",
    "RETURN [.5, .25]",
    "RETURN .5, 1.5, 0.0",
])
def test_ordinary_leading_dot_literals_reach_the_handle_unchanged(statement):
    """Anti-overfix guard: no FALSE refusal on an ordinary leading-dot float.

    Starting a token at ``.`` must not turn a property access into a number
    (``n.v2`` is separately pinned in ``test_the_literal_scan_reads_CODE_and_not_data``)
    and must not refuse a float that IS in the domain: ``.5`` and ``-.5`` are
    faithful doubles, ``.0e-400`` denotes ZERO (the exponent digits must not be
    read as significant, exactly as for ``0.0e-400``). Value that makes it fail:
    any of these being refused.
    """
    g, handle = _guarded()
    assert g.query(statement) == "SENTINEL-REACHED-HANDLE"
    assert handle.calls == [("query", (statement, None, None), {})]


def test_a_long_leading_zero_integer_is_judged_by_domain_not_length():
    """Falsifier: a valid literal with >4300 leading zeros is not a bare ValueError.

    ``int()`` refuses a string past CPython's 4300-digit decimal conversion
    limit. The integer branch stripped leading zeros for its LENGTH test but
    then parsed the RAW token, so a representable value such as ``0…01`` (5000
    zeros, ``1``) — valid, and evaluated correctly by the engine — escaped this
    seam as an unhandled ``ValueError`` instead of being admitted, and an
    out-of-domain one escaped as the wrong exception type (P2, review
    2026-10-16). The scan must judge the STRIPPED digits, so a legal value
    reaches the handle and an illegal one raises the seam's own error. Value
    that makes it fail: the three statements below.
    """
    g, handle = _guarded()
    zeros = "0" * 5000
    assert g.query(f"RETURN {zeros}1") == "SENTINEL-REACHED-HANDLE"
    assert g.query(f"RETURN -{zeros}1") == "SENTINEL-REACHED-HANDLE"
    with pytest.raises(UnrepresentableNumberError):
        g.query(f"RETURN {zeros}{INT64_BEYOND}")
    assert len(handle.calls) == 2, "only the two representable literals may be sent"


def test_a_slice_bound_out_of_domain_is_refused():
    """Falsifier: a ``..`` bound is its OWN number, not the fraction of ``0.``.

    openCypher writes the slice/range operator as ``x[0..N]``. The scan absorbed
    the second dot into the token before it (``0.``) and then skipped a single
    character on re-entry, so the FIRST digit of ``N`` was dropped and the rest
    read as a shorter number: ``x[0..9223372036854775808]`` (INT64_MAX + 1) was
    ADMITTED, ``x[0..9999999999999999999]`` dropped its leading ``9`` and was
    admitted too, and ``x[0..1.0e400]`` was re-read as ``.0e400`` -> ``0.0``
    (P2, review 2026-10-16). A ``..`` bound must be scanned like any other
    literal. Value that makes it fail: any bound here; the handle must stay
    EMPTY — pre-fix it records the call.
    """
    g, handle = _guarded()
    for bound in (
        "9223372036854775808",
        "9999999999999999999",
        "1180591620717411303424",
        "1.0e400",
    ):
        with pytest.raises(UnrepresentableNumberError):
            g.query(f"RETURN x[0..{bound}]")
    assert handle.calls == [], "an out-of-domain slice bound reached the handle"


def test_a_name_digit_run_is_consumed_whole_not_one_char_at_a_time():
    """Falsifier: a digit run belonging to a NAME is skipped in full.

    The walk skipped exactly one character when a digit continued a name, so a
    multi-digit name tail was re-entered at its SECOND digit: ``$p12`` read as
    the number ``2``, and a name whose tail is out of domain after that dropped
    digit (``$p19223372036854775808`` -> ``9223372036854775808``) would refuse a
    legal query (P2, review 2026-10-16). Value that makes it fail: the long name
    below, which must reach the handle unchanged.
    """
    g, handle = _guarded()
    name = "$p19223372036854775808"
    assert g.query(f"RETURN {name}") == "SENTINEL-REACHED-HANDLE"
    assert g.query("MATCH (n:Point2) RETURN n.v2") == "SENTINEL-REACHED-HANDLE"
    assert len(handle.calls) == 2, "a name's digits were read as a number"


def test_the_replay_exemption_covers_the_statement_text_too():
    """Falsifier: the #5011 replay scope is the SAME exemption on both surfaces.

    #5011 decided that a replayed record carrying a number the store cannot hold
    is a DIVERGENCE for ``check_consistency`` to report, never a crash, so the
    replay engines run with the refusal suspended. That decision is about the
    DOMAIN, not about which surface carries the number, so the literal scan must
    honour the same scope — and reset it, not leak it.
    """
    from tortoise.cypher_guard import tolerating_altered_numbers

    g, handle = _guarded()
    with tolerating_altered_numbers():
        assert g.query(LITERAL_CYPHER) == "SENTINEL-REACHED-HANDLE"
    with pytest.raises(UnrepresentableNumberError):
        g.query(LITERAL_CYPHER)
    assert len(handle.calls) == 1, "only the replayed write may reach the handle"


def test_a_read_query_is_refused_too_by_policy():
    """Falsifier: the refusal is NOT write-gated — a read is refused as well.

    A read of an out-of-domain value cannot be trusted either (the store alters
    what it compares against), and gating on write clauses would need Cypher
    write-detection, which is its own source of false refusals. The ONE
    exemption is a REPLAY write (#5011) — see the next test. This test pins the
    read policy so a later "optimisation" that gates on write clauses has to
    argue for the read it stops guarding.
    """
    g, handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        g.ro_query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert handle.calls == []


# ── anti-overfix guards: contracts the fix must not break ───────────────────

def test_the_replay_exemption_is_scoped_and_does_not_leak():
    """Falsifier: the #5011 exemption must be a REPLAY SCOPE, not a global switch.

    #5011 decided that a replayed record carrying a number the store cannot hold
    is a DIVERGENCE for ``check_consistency`` to report, never a crash; a CALLER
    write is refused, because the caller can pass a string instead. Value that
    makes it fail: the SAME params inside and outside
    ``tolerating_altered_numbers()`` — inside must reach the handle, and outside
    must raise again (which also proves the scope is reset, not leaked).
    """
    from tortoise.cypher_guard import tolerating_altered_numbers

    g, handle = _guarded()
    with tolerating_altered_numbers():
        assert g.query(CYPHER, {"v": OUT_OF_DOMAIN}) == "SENTINEL-REACHED-HANDLE"
    with pytest.raises(UnrepresentableNumberError):
        g.query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert len(handle.calls) == 1, "only the replayed write may reach the handle"


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


def test_recover_from_log_counts_a_numeric_refusal_apart_from_torn_lines(tmp_path):
    """Falsifier: a deliberate domain refusal must not be reported as DAMAGE.

    ``recover_from_log`` never raises for a replay failure — it REPORTS it (its
    docstring is explicit). The pre-#7174 shape of its apply loop caught
    everything into ``torn``, the crash-damage tolerance, so a record the store
    would have ALTERED landed there: a deliberate refusal, reported as a torn
    line, hiding a data-loss event. Value that makes it fail: an event whose
    props carry the out-of-domain number; ``reason`` must name the refusal and
    must NOT call it a skip.
    """
    from tortoise.consistency import recover_from_log
    from tortoise.log import EventLog

    log = EventLog(str(tmp_path / "log.jsonl"))
    for index in range(3):
        log.append({"type": "PointCreated", "id": f"p{index}", "v": 1.0})

    class _Result:
        def __init__(self, node):
            self.result_set = [[node]]

    class _FakeProj:
        def __init__(self):
            self.count_calls = 0
            self.applied = []

        def query(self, cypher):
            self.count_calls += 1
            return _Result(0 if self.count_calls == 1 else 3)

        def apply(self, ev):
            if ev.get("id") == "p1":
                raise UnrepresentableNumberError(
                    "'v': integer with 71 bits is outside the range the store "
                    "can hold"
                )
            self.applied.append(ev.get("id"))

    proj = _FakeProj()
    result = recover_from_log(str(tmp_path), proj)
    assert result["recovered"] is True, result
    assert proj.applied == ["p0", "p2"], proj.applied
    assert "1 refused by the numeric domain" in result["reason"], result["reason"]
    assert "skipped" not in result["reason"], (
        "a deliberate refusal must not be reported as a torn/skipped line: "
        f"{result['reason']!r}"
    )


def test_the_journal_write_path_tolerates_an_unholdable_number():
    """Falsifier for BOTH halves of the scope: journal path tolerant, seam refuses.

    #5011's own test (``test_an_over_range_int_is_a_divergence_not_a_crash``)
    seeds through ``FalkorProjection.apply`` and expects the write to LAND — the
    store clamps and ``check_consistency`` reports a divergence; "a raise here
    makes the gate un-runnable, durably, on every retry". Value that makes it
    fail: an out-of-domain prop through ``proj.apply``, which must reach the
    handle rather than raise — while the SAME value at a live param boundary
    (``proj.g.query``) is still refused. Removing the exemption from ``apply``
    reds the first half; a refusal that leaks into the journal path reds it too.
    """
    handle = _FakeHandle()
    proj = object.__new__(FalkorProjection)
    proj.g = _GuardedGraph(handle, proj)
    proj._skip_guard = False
    proj._is_embedded = True
    proj._graph_name = "test_7174_param_boundary_numeric_guard"
    proj.apply({
        "type": "PointAdded",
        "point": {"id": "p1", "content": "hello", "confidence": OUT_OF_DOMAIN},
    })
    assert handle.calls, "the journal write path must reach the handle (#5011)"
    live, live_handle = _guarded()
    with pytest.raises(UnrepresentableNumberError):
        live.query(CYPHER, {"v": OUT_OF_DOMAIN})
    assert live_handle.calls == [], "a live param write must still be refused"


def test_every_whole_journal_replay_engine_is_exempt():
    """Falsifier: a replay engine that loses the exemption crashes on #5011's case.

    The journal VALUE is not re-authorable at replay time, so every whole-journal
    replay engine must run under the tolerance: ``FalkorProjection.apply`` (#5011's
    own seeding path), ``rebuild``, ``rebuild_all``, ``consistency.recover_from_log``
    and ``backup.restore`` — whose JSONL fallback writes the journaled ``valid_to``
    verbatim through ``apply_journal_point_restamp``, so an out-of-domain instant
    aborted the restore before this. This test DECLARES the set (a fifth engine
    cannot be detected automatically — the list is asserted here and in the
    decorator's docstring), and it reds the moment one of them loses its
    exemption: the ``functools.wraps`` attribute is the documented marker.
    """
    from tortoise import backup, consistency
    from tortoise.projection import FalkorProjection

    engines = (
        FalkorProjection.apply,
        FalkorProjection.rebuild,
        FalkorProjection.rebuild_all,
        consistency.recover_from_log,
        backup.restore,
    )
    for fn in engines:
        assert getattr(fn, "__wrapped__", None) is not None, (
            f"{fn.__qualname__} replays a journal and must carry "
            "@tolerates_altered_numbers (#7174/#5011)"
        )
