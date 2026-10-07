"""#7626: a projection's graph handle is re-resolved IN PLACE, not replaced.

The defect class this replaces: clearing the SDK's cached projection
(``self._proj = None``) DESTROYS object identity, so every caller that captured the
projection — or its ``g`` wrapper — holds a dead handle after a graph rebuild, and
each of those call sites needs its own re-bind. Four successive static-scan methods
kept finding new instances (10 live, ~45 latent). Repointing the handle in place has
no equivalent class, because there is nothing to re-bind.

These tests pin the two properties that make that true, each of which fails if the
implementation is swapped for the identity-destroying one:

* the WRAPPER object is preserved across a rebind (so a caller that captured
  ``proj.g`` is not stranded) — this fails if ``rebind()`` builds a new wrapper and
  reassigns ``proj.g``;
* the INNER handle is re-resolved (so the dead graph is actually left behind) — this
  fails if ``rebind()`` is a no-op.
"""

from __future__ import annotations

import pytest
from redis.exceptions import ResponseError

from tortoise.projection import _GuardedGraph
from tortoise.retry import graph_replaced

_REPLACED = ("graph was deleted or replaced while the query was running, aborting")


class _StubDB:
    """Minimal stand-in for the FalkorDB client the projection holds."""

    def __init__(self) -> None:
        self.select_graph_calls: list[str] = []

    def select_graph(self, name: str):
        self.select_graph_calls.append(name)
        return _StubHandle(name, generation=len(self.select_graph_calls))


class _StubHandle:
    def __init__(self, name: str, generation: int, *, raise_replaced: bool = False):
        self.name = name
        self.generation = generation
        self.raise_replaced = raise_replaced
        self.queries: list[str] = []
        # One stable instance, so a test can assert exception IDENTITY, not just type.
        self.replaced_error = ResponseError(_REPLACED)

    def query(self, cypher: str, params=None, timeout=None):
        self.queries.append(cypher)
        if self.raise_replaced:
            raise self.replaced_error
        return {"rows": [], "generation": self.generation}


class _StubProj:
    """Only the two attributes ``_GuardedGraph`` reaches for."""

    def __init__(self, graph_name: str = "tortoise_test_live_handle"):
        self._graph_name = graph_name
        self.db = _StubDB()


def test_rebind_preserves_wrapper_identity_and_resolves_a_new_handle():
    proj = _StubProj()
    original = _StubHandle("tortoise_test_live_handle", generation=0)
    wrapper = _GuardedGraph(original, proj)

    held_by_caller = wrapper  # a caller that captured `proj.g` before the rebuild
    wrapper.rebind()

    # (a) identity preserved — the captured wrapper is still the live one
    assert wrapper is held_by_caller
    # (b) the dead graph is actually left behind
    assert wrapper._g is not original
    assert wrapper._g.generation == 1
    # and it re-resolved THIS projection's own graph name
    assert proj.db.select_graph_calls == ["tortoise_test_live_handle"]


def test_rebind_is_idempotent_and_keeps_resolving_the_same_graph_name():
    proj = _StubProj()
    wrapper = _GuardedGraph(_StubHandle("tortoise_test_live_handle", 0), proj)

    wrapper.rebind()
    wrapper.rebind()

    assert proj.db.select_graph_calls == ["tortoise_test_live_handle"] * 2
    assert wrapper._g.generation == 2


def test_a_replaced_graph_error_rebinds_in_place_and_propagates():
    """The funnel heals the handle for ALL holders, then re-raises.

    Re-raising rather than retrying is deliberate: the funnel cannot know whether
    the statement is idempotent, and a bare CREATE re-issued on an unknown outcome
    mints a duplicate point.
    """
    proj = _StubProj()
    dead = _StubHandle("tortoise_test_live_handle", 0, raise_replaced=True)
    wrapper = _GuardedGraph(dead, proj)

    with pytest.raises(ResponseError) as ei:
        wrapper.query("RETURN 1")

    # The ORIGINAL exception propagates, unswapped — callers bucket on type.
    assert ei.value is dead.replaced_error

    # Healed in place: the SAME wrapper now reaches a fresh handle, so the next use
    # by this caller — or by anyone else holding this projection — succeeds.
    assert wrapper._g is not dead
    assert dead.queries == ["RETURN 1"]
    assert wrapper.query("RETURN 1") == {"rows": [], "generation": 1}


def test_the_funnel_does_not_re_dispatch_on_the_fresh_handle():
    """The no-retry invariant, mutation-pinned.

    This is the test that catches a re-dispatch: if the funnel ever retried the
    statement on the freshly-resolved handle, that second dispatch would land on
    the NEW handle and mint a duplicate point. Asserting only that the OLD handle
    was dispatched once (as an earlier version of this suite did) leaves that
    invisible, because the duplicate happens on the other object.
    """
    proj = _StubProj()
    dead = _StubHandle("tortoise_test_live_handle", 0, raise_replaced=True)
    wrapper = _GuardedGraph(dead, proj)

    with pytest.raises(ResponseError):
        wrapper.query("CREATE (:Point {id:'p1'})")

    fresh = wrapper._g
    assert fresh is not dead
    # THE ASSERTION THAT PINS NO-RETRY: the replacement handle was never asked to
    # run the statement, so no duplicate can have been minted through it.
    assert fresh.queries == [], (
        f"the funnel re-dispatched on the freshly-resolved handle: {fresh.queries!r} "
        "— a non-idempotent CREATE would have minted a duplicate point"
    )


def test_a_healthy_graph_is_not_rebound():
    """The common path must not re-resolve — a rebind per query would be a
    regression in itself (a needless select_graph on every call)."""
    proj = _StubProj()
    healthy = _StubHandle("tortoise_test_live_handle", 0)
    wrapper = _GuardedGraph(healthy, proj)

    assert wrapper.query("RETURN 1") == {"rows": [], "generation": 0}
    assert proj.db.select_graph_calls == []
    assert wrapper._g is healthy


def test_a_failed_rebind_does_not_mask_the_caller_real_error():
    """A heal that itself fails must not replace the error the caller needs.

    Callers bucket on `type` (`_classify_db_failure`), so letting an AttributeError
    from a bare/mock projection escape here would silently reclassify a DB error on
    the hot path every `proj.g.query` goes through.
    """
    proj = _StubProj()

    def _boom(_name):
        raise RuntimeError("select_graph boom")

    proj.db.select_graph = _boom  # type: ignore[method-assign]
    dead = _StubHandle("tortoise_test_live_handle", 0, raise_replaced=True)
    wrapper = _GuardedGraph(dead, proj)

    with pytest.raises(ResponseError) as ei:
        wrapper.query("RETURN 1")
    assert ei.value is dead.replaced_error


def test_a_projection_without_a_db_does_not_mask_the_error():
    """The degenerate projection shape (no `db`) must not change the raised type."""

    class _Bare:
        pass

    dead = _StubHandle("tortoise_test_live_handle", 0, raise_replaced=True)
    wrapper = _GuardedGraph(dead, _Bare())

    with pytest.raises(ResponseError) as ei:
        wrapper.query("RETURN 1")
    assert ei.value is dead.replaced_error


def test_sibling_verbs_heal_too():
    """The handle is dead for READS as well — every verb heals, not just `query`.

    The issue reports reads succeeding where writes are refused; that asymmetry must
    not leave a dead READ handle behind once the graph is replaced.
    """
    proj = _StubProj()
    dead = _StubHandle("tortoise_test_live_handle", 0)
    dead.ro_query = _raising(dead.replaced_error)  # type: ignore[method-assign]
    wrapper = _GuardedGraph(dead, proj)

    with pytest.raises(ResponseError):
        wrapper.ro_query("MATCH (n) RETURN n")

    assert wrapper._g is not dead


def _raising(exc: BaseException):
    def _fn(*_a, **_k):
        raise exc

    return _fn


def test_graph_replaced_predicate_matches_only_the_invalidation_shape():
    assert graph_replaced(ResponseError(_REPLACED)) is True
    # case-insensitive: the vendor string is not a stable contract
    assert graph_replaced(ResponseError(_REPLACED.upper())) is True
    # NOT a licence to retry: an unrelated ResponseError is not an invalidated handle
    assert graph_replaced(ResponseError("WRONGTYPE Operation against a key")) is False
    assert graph_replaced(ResponseError("MISCONF Can't persist")) is False
    # and not a graph error at all
    assert graph_replaced(TimeoutError("read timed out")) is False
