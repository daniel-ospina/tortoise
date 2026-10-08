"""#5404: ``validate_entity_type`` is enforced where ``entity_type`` becomes STRUCTURE.

``entity_type`` is caller-controlled (an MCP tool arg, and a public argument on
each module-level runner) and each search leg interpolates its capitalized form
into a Cypher LABEL. ``tortoise/security.py`` declares ``validate_entity_type``
as the enforcement point for exactly that, but nothing but its own unit test
called it, so the runners accepted any string.

The guard is asserted at each runner's ENTRY (see the three tests below). That
placement is load-bearing, not stylistic: ``run_fts_query`` and
``run_vector_query`` both return ``[]`` — before any label exists — when their
circuit breaker is open, so a guard placed next to the label derivation lets an
invalid type through silently whenever the breaker is open. Every test here runs
DB-free precisely because the guard precedes all database work.
"""

from __future__ import annotations

import contextlib

import pytest

from tortoise import search_engine
from tortoise.security import VALID_ENTITY_TYPES

# A caller-controlled value shaped like a Cypher label: not in the vocabulary,
# so the guard must reject it before it can reach generated Cypher.
INJECTION = "point) DETACH DELETE n //"

# The capitalized/whitespace forms the guard rejects BY DESIGN — the same
# contract tests/test_security.py:96 pins on the validator itself — plus the
# dead v3.2 entity type and the plain-unknown values the SDK-level tests use.
BAD_TYPES = [INJECTION, "Point", "POINT", "point ", "action", "invalid-type", "x", ""]


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_run_fts_query_rejects_invalid_entity_type(bad):
    with pytest.raises(ValueError, match="Invalid entity_type"):
        search_engine.run_fts_query(None, "q", entity_type=bad)


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_run_vector_query_rejects_invalid_entity_type(bad):
    with pytest.raises(ValueError, match="Invalid entity_type"):
        search_engine.run_vector_query(None, [0.1, 0.2], entity_type=bad)


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_run_structural_query_rejects_invalid_entity_type(bad):
    with pytest.raises(ValueError, match="Invalid entity_type"):
        search_engine.run_structural_query(None, None, entity_type=bad)


#: (runner name, positional args) for each leg that must carry the guard.
LEGS = [
    ("run_fts_query", ("q",)),
    ("run_vector_query", ([0.1, 0.2],)),
    ("run_structural_query", (None,)),
]


@pytest.mark.parametrize("bad", BAD_TYPES)
def test_run_vector_query_rejects_invalid_entity_type_with_no_query_vec(bad):
    """The leg's FIRST early return is ``if not query_vec: return []``.

    The rejection tests above always pass a non-empty vector, so they never
    reach it — a guard placed between that return and the breaker would keep
    them green. Caught by mutation in review: moving the guard below
    ``if not query_vec`` left the whole file passing while
    ``run_vector_query(None, [], entity_type=INJECTION)`` returned ``[]``.
    """
    with pytest.raises(ValueError, match="Invalid entity_type"):
        search_engine.run_vector_query(None, [], entity_type=bad)


@pytest.mark.parametrize("runner,args", LEGS)
def test_guard_runs_at_entry_before_any_graph_use(monkeypatch, runner, args):
    """The guard runs before any graph use.

    Both the guard and any graph use append to one ordered list, and the guard
    must be first. The graph stub raises on first use, but the runners catch
    driver exceptions by design (the degradation chain returns []), so the
    ordering is what is asserted — not an escaping exception.
    """
    order: list[str] = []

    class _GraphSpy:
        def query(self, *a, **k):
            order.append("graph")
            raise RuntimeError("graph reached")

    def _spy(entity_type):
        order.append("guard")
        return entity_type

    monkeypatch.setattr(search_engine, "validate_entity_type", _spy)

    with contextlib.suppress(Exception):
        # A real driver error is fine; only the ORDER is under test.
        getattr(search_engine, runner)(_GraphSpy(), *args, entity_type="point")

    assert order and order[0] == "guard", f"guard did not run first: {order}"


def test_every_valid_entity_type_passes_the_guard():
    """No over-blocking: the guard accepts the whole declared vocabulary."""
    assert sorted(VALID_ENTITY_TYPES) == [
        "document", "event", "object", "operator", "point", "source", "subject",
    ]
    for good in sorted(VALID_ENTITY_TYPES):
        assert search_engine.validate_entity_type(good) == good


@pytest.mark.parametrize("runner,args", LEGS)
def test_rejection_does_not_depend_on_breaker_state(monkeypatch, runner, args):
    """The refusal must hold with the circuit breaker OPEN.

    This is the property the entry placement exists for. With the breaker
    forced open, a guard placed BELOW the breaker check is never reached — the
    leg short-circuits to ``[]`` first — so this test fails for that placement.

    Scope of the assertion: for ``run_fts_query`` and ``run_vector_query`` the
    label derivation sits below the breaker, so "below the breaker" and "beside
    the label" coincide. ``run_structural_query`` derives its label ABOVE its
    breaker check, so for that leg this pins only that the guard precedes the
    breaker — not that it is the first statement.
    """
    monkeypatch.setattr(search_engine, "_breaker_allow", lambda _name: False)

    with pytest.raises(ValueError, match="Invalid entity_type"):
        getattr(search_engine, runner)(None, *args, entity_type=INJECTION)


def test_degradation_chain_rejects_invalid_entity_type():
    """The public orchestrator must not swallow the rejection.

    ``degradation_chain`` catches every strategy exception by design — a failed
    leg must degrade, not crash the read. An unvalidated ``entity_type`` would
    therefore be swallowed into an EMPTY result, which is the fail-open symptom
    this issue closes, so the guard has to sit ahead of the workers.
    """
    with pytest.raises(ValueError, match="Invalid entity_type"):
        search_engine.degradation_chain(
            None,
            "q",
            None,
            [0.1, 0.2],
            {"fts": True, "vector": True, "structural": True},
            entity_type=INJECTION,
        )


def test_sdk_fts_query_rejects_invalid_entity_type():
    """The SDK tool is the public MCP surface.

    It already fails closed: ``sdk.py`` validates ``entity_type`` itself before
    deriving the label, so the SDK site named in the issue needs no change from
    this fix. Pinned here so that stays true.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK.__new__(TortoiseSDK)
    with pytest.raises(ValueError, match="entity_type"):
        sdk.tortoise_fts_query("q", entity_type=INJECTION)
