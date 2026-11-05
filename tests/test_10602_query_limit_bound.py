"""#10602 — `tortoise_query`'s `limit` was silently dropped on its default path.

``tortoise_query(kind=..., limit=N)`` documents (and its own body computes,
``eff_limit = limit if limit is not None else (20 if paginated else 100)``) that
a plain structural query is bounded. It was not: ``paginated`` is
``offset is not None or page is not None``, so ``limit`` ALONE fell through to
the plain path, which called ``sdk.query(kind, …)`` — and ``sdk.query`` has no
``limit`` parameter at all (``tortoise/sdk.py``: ``MATCH (n:Point) WHERE …
RETURN properties(n)``, no ``LIMIT``, no ``ORDER BY``). **The whole label came
back**, and ``eff_limit`` was used only on the tag/text/paginated branches.

Measured on the hosted graph 2026-11-05 (``graph_size`` 31,248,
``pointkinds``: statement 15,733)::

    tortoise_query({"kind":"statement","limit":3})            -> 15,417 ms, no data
    tortoise_query({"kind":"statement","limit":3})   x7        -> 10,1xx ms  -32009
    tortoise_query({"kind":"statement","offset":0,"limit":3})  ->  1,362 ms, 3 rows

The unbounded read + serialisation of 15,708 rows outlives
``mcp_auth._TRANSPORT_WAIT_BOUND_S`` (10.0), so a SINGLE agent gets
``-32009`` ``retry_after: 10`` — the issue's own failure signature — and the
advertised retry cannot recover it because the work is deterministic.

Test doctrine (Class B): every test states (1) the value whose presence makes it
fail and (2) that the fixture reaches that value.

Run with (stub-only, no database):
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_10602_query_limit_bound.py -q -p no:cacheprovider
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001


class _StubSDK:
    """A graph with `n_points` rows and the real accessors' bounds semantics.

    `query()` deliberately behaves like the SDK's own: no limit, whole
    population, no ordering. `paginated_query()` slices. The distinction is the
    value under test — a fix that merely slices in the handler would still pay
    the unbounded read, so the assertion is on what the ACCESSOR was asked for.
    """

    def __init__(self, n_points=5, *, raises=None):
        self.calls = []
        self.points = [{"id": f"p{i}"} for i in range(n_points)]
        self.raises = raises

    def query(self, kind=None, **kwargs):
        self.calls.append(("query", kind, kwargs))
        return self.points

    def paginated_query(self, kind=None, skip=0, limit=20, **kwargs):
        self.calls.append(("paginated_query", kind, skip, limit, kwargs))
        if self.raises is not None:
            raise self.raises
        total = len(self.points)
        return {"results": self.points[skip:skip + limit], "total": total,
                "hasMore": skip + limit < total}

    def query_points_by_tag(self, tag, *, _record_reads=True):
        self.calls.append(("query_points_by_tag", tag, _record_reads))
        return []


@pytest.fixture(autouse=True)
def _http_transport():
    """These tests drive the tool bodies directly (no HTTP middleware).

    `_safe` fail-closes on `_transport_mode is None`, and the stdio arm further
    refuses when `TORTOISE_API_KEY` is present (a dev box that also holds a
    hosted key). The `http` arm is the one this code runs under in production
    and it delegates auth to the transport, which is exactly what a tool-body
    test wants — the stub is still what `_get_org_sdk()` resolves to.
    """
    from tortoise.mcp_auth import (  # noqa: I001
        _current_org_id, _current_org_limits, _transport_mode,
    )
    _transport_mode.set("http")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
    _current_org_limits.set(None)


@pytest.fixture
def stub_factory(monkeypatch):
    from tortoise import mcp_server

    def make(n_points=5, *, raises=None):
        stub = _StubSDK(n_points, raises=raises)
        monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: stub)
        return stub

    return make


def test_limit_is_honoured_on_the_plain_structural_path(stub_factory):
    """RED before the fix: limit=2 returned all 5 rows (the whole label).

    Fails on the value `len(result) == 2`; the fixture reaches it because the
    stub holds 5 rows and a bound of 2 must select 2 of them.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory(n_points=5)
    result = tortoise_query(kind="statement", limit=2)
    assert isinstance(result, list), "the plain path must stay a plain list"
    assert len(result) == 2, f"limit ignored: asked for 2, got {len(result)} of 5"


def test_the_bound_reaches_the_accessor_not_just_the_response(stub_factory):
    """A handler-side slice would return 2 rows while STILL reading the label.

    The bound must be pushed to the accessor, so `sdk.query` (no LIMIT in its
    Cypher at all) must not be the accessor used for a bounded plain read.
    """
    from tortoise.mcp_server import tortoise_query
    stub = stub_factory(n_points=500)
    tortoise_query(kind="statement", limit=2)
    name = stub.calls[-1][0]
    assert name == "paginated_query", (
        "a bounded plain query must reach an accessor that applies LIMIT; "
        f"got {name!r} (sdk.query has no LIMIT in its Cypher)")


def test_default_plain_query_is_capped_at_the_computed_eff_limit(stub_factory):
    """`eff_limit` defaults to 100 on the plain path — and was never applied.

    RED before the fix: no `limit` returned all 500 rows. The default cap is
    the code's OWN number (`eff_limit`, 100 when not paginated), so applying it
    implements the intent rather than inventing a new one.
    """
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=500)
    result = tortoise_query(kind="statement")
    assert isinstance(result, list)
    assert len(result) == 100, f"default cap not applied: got {len(result)} of 500"


def test_pagination_shape_is_unchanged(stub_factory):
    """offset=/page= keep returning {results,total,hasMore} — no regression."""
    from tortoise.mcp_server import tortoise_query
    stub_factory(n_points=5)
    out = tortoise_query(kind="statement", offset=0, limit=2)
    assert out == {"results": [{"id": "p0"}, {"id": "p1"}],
                   "total": 5, "hasMore": True}


def test_a_legitimately_empty_result_is_not_an_error(stub_factory):
    """The issue's implied case: finding nothing must NOT raise or read as failure.

    A bounded read over a kind with no rows is a successful answer of `[]` —
    it must not become an error, and must not be an `_SafeError`.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub = stub_factory(n_points=0)
    result = tortoise_query(kind="statement", limit=5)
    assert not isinstance(result, _SafeError), "an empty read is not a failure"
    assert result in ([], ) or (isinstance(result, dict)
                                and result.get("results") == [])


def test_a_real_failure_still_surfaces_through_the_bound(stub_factory):
    """The bound must not swallow an error that used to surface.

    `_safe` returns an `_SafeError` (a dict SUBCLASS) rather than raising, so a
    fix that does `.get("results", [])` on its return turns every database
    failure into an empty successful read — a false PASS. Gate on the type.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=5, raises=RuntimeError("db down"))
    result = tortoise_query(kind="statement", limit=2)
    assert isinstance(result, _SafeError), (
        "a failed read must surface as _SafeError, not as an empty list")


def test_a_reserved_filter_key_is_a_structured_error_not_a_typeerror(stub_factory):
    """#10602 review F3 — the bound must not turn a clean refusal into a crash.

    `skip`/`limit` are reserved filter keys (`security.validate_filter_key`) AND
    the names the bounded path passes as explicit keywords, so a caller's
    `filters={"limit": 5}` collides at the `_safe(...)` call site — OUTSIDE its
    `try` — and escaped as a bare TypeError. Before the bound, the same input
    reached `validate_filter_key` inside `_safe` and came back as the structured
    "Reserved filter key" error. Fails on the value `isinstance(result,
    _SafeError)`; the fixture reaches it because 5 > 0 points and the filter dict
    carries a colliding key.
    """
    from tortoise.mcp_server import _SafeError, tortoise_query
    stub_factory(n_points=5)
    for bad in ({"limit": 5}, {"skip": 3}):
        result = tortoise_query(kind="statement", filters=bad)
        assert isinstance(result, _SafeError), (
            f"filters={bad} escaped as {type(result).__name__} instead of a "
            "structured refusal")
        assert "Reserved filter key" in str(result.get("error", "")), result


def test_the_served_description_discloses_the_bound():
    """#10602 review F2 — the truncation must not be SILENT to the caller.

    `docs/architecture/BOUNDED-OPERATION-CONTRACT.md` §1 forbids returning a
    silently-truncated result indistinguishable from a complete one. This arm
    still returns a bare list (no `hasMore`/`total`), so the disclosure has to
    live in the text the model actually reads: the tool's SERVED description
    (`ToolDefinition.description`, what `FunctionTool.from_function` publishes),
    not the handler docstring. Fails on the value of that description.
    """
    from tortoise.tool_registry import TOOL_REGISTRY
    entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_query")
    desc = entry.description.lower()
    assert "bound" in desc, (
        "the served description does not say the structural read is bounded: "
        f"{entry.description}")
    assert "offset" in desc, (
        "the served description does not say how to walk the population: "
        f"{entry.description}")
