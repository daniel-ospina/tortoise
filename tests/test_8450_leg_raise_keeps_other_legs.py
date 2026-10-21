"""#8450 (lane L4 / #5096): a leg that RAISES must not drop the other legs' rows.

``degradation_chain``'s contract is stated in its own docstring — *"Each strategy
wraps in try/except → on failure, skip and log. Supports partial failure: one
strategy down, others continue."* — and the README-level reason it matters is
that a retrieval leg failing must DEGRADE the read, never silently narrow it.

Before this pin the collector called ``future.result()`` with **no per-future
guard** inside ``as_completed``. A raising worker therefore escaped into the
single outer ``except Exception`` — which only logs — and every future
``as_completed`` had not yet yielded was abandoned with its rows. The healthy
leg's rows were dropped from the returned mapping while that leg's own private
trace still said ``reason="ok"``: a caller that fuses or gates on the trace saw
a leg that ran and matched, whose rows were not in the result (#4999's
silent-degradation class, one layer above the leg).

Only the ``TimeoutError`` arm had a salvage rescan (``done()`` futures re-read);
the generic-exception arm is the one this file pins.

The ordering is made DETERMINISTIC rather than raced (#5049's rule: a verdict is
a function of a condition, never of a wall-clock margin). ``as_completed`` is
substituted with a feeder that waits for every worker and then yields the
RAISING future FIRST — so the pre-fix failure is not "sometimes it was already
collected" but always "the healthy rows after the raise are gone". After the fix
the ordering cannot matter, and that is asserted too.

DB-free: every runner is a monkeypatched double and ``graph=None``.
"""
from __future__ import annotations

import concurrent.futures

import pytest

from tortoise import search_engine
from tortoise.search_engine import degradation_chain

#: What the raising leg blows up with. Asserted on the log, not re-raised.
_BOOM = RuntimeError("leg blew up")


def _fts(*_a, **_k):
    return [("from_fts", 1.0)]


def _vector(*_a, **_k):
    raise _BOOM


def _structural(*_a, **_k):
    return [("from_struct", 0.5)]


def _raising_first(fs, timeout=None):  # mirrors the stdlib signature
    """``as_completed`` double: every worker finishes, the RAISING one yields first.

    ``concurrent.futures.wait`` is used (not ``as_completed``) so this feeder
    cannot recurse into the patch, and the futures are provably complete before
    the order is decided — the collector's own ``result()`` calls are then
    instantaneous, so nothing here races the 500 ms cap.
    """
    pending = list(fs)
    concurrent.futures.wait(pending)
    raised = [f for f in pending if f.exception() is not None]
    healthy = [f for f in pending if f.exception() is None]
    yield from raised + healthy


@pytest.fixture
def patched_legs(monkeypatch):
    """All three legs enabled: one raises, two are healthy."""
    monkeypatch.setattr(search_engine, "run_fts_query", _fts)
    monkeypatch.setattr(search_engine, "run_vector_query", _vector)
    monkeypatch.setattr(search_engine, "run_structural_query", _structural)
    monkeypatch.setattr(concurrent.futures, "as_completed", _raising_first)
    return {"fts": True, "vector": True, "structural": True}


def _run(leg_trace=None, **kwargs):
    return degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1] * 8,
        strategies=kwargs.pop("strategies"), is_embedded=True,
        leg_trace=leg_trace, **kwargs)


def test_a_raising_leg_does_not_drop_the_healthy_legs_rows(patched_legs, caplog):
    """The whole defect in one assert: the healthy rows survive a sibling raise."""
    with caplog.at_level("WARNING"):
        result = _run(strategies=patched_legs)

    assert result == {"fts": [("from_fts", 1.0)],
                      "structural": [("from_struct", 0.5)]}


def test_the_raising_leg_is_absent_not_silently_empty(patched_legs):
    """A leg that blew up is OMITTED, so a caller cannot read it as "no hits"."""
    result = _run(strategies=patched_legs)

    assert "vector" not in result
    assert set(result) == {"fts", "structural"}


def test_the_raise_is_logged_with_the_leg_name(patched_legs, caplog):
    """Skip-and-log stays the contract — but the log must say WHICH leg."""
    with caplog.at_level("WARNING"):
        _run(strategies=patched_legs)

    messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("vector" in m and "leg blew up" in m for m in messages), messages


def test_the_returned_legs_keep_the_fixed_strategy_order(patched_legs):
    """#2952: the mapping is a pure function of the leg contents, not of who
    finished first — a raise must not let completion order leak into the keys."""
    result = _run(strategies=patched_legs)

    assert list(result) == ["fts", "structural"]


def test_a_raise_on_the_LAST_leg_still_delivers_the_others(monkeypatch):
    """Order-independence, the other direction: raising last changes nothing."""
    monkeypatch.setattr(search_engine, "run_fts_query", _fts)
    monkeypatch.setattr(search_engine, "run_vector_query", _vector)
    monkeypatch.setattr(search_engine, "run_structural_query", _structural)

    def _raising_last(fs, timeout=None):
        pending = list(fs)
        concurrent.futures.wait(pending)
        raised = [f for f in pending if f.exception() is not None]
        healthy = [f for f in pending if f.exception() is None]
        yield from healthy + raised

    monkeypatch.setattr(concurrent.futures, "as_completed", _raising_last)

    result = _run(strategies={"fts": True, "vector": True, "structural": True})

    assert result == {"fts": [("from_fts", 1.0)],
                      "structural": [("from_struct", 0.5)]}


def test_all_legs_raising_is_still_an_empty_result_not_a_crash(patched_legs, monkeypatch):
    """The degradation contract's floor: every leg down yields {}, not an error."""
    def _boom(*_a, **_k):
        raise _BOOM

    monkeypatch.setattr(search_engine, "run_fts_query", _boom)
    monkeypatch.setattr(search_engine, "run_structural_query", _boom)

    result = _run(strategies={"fts": True, "vector": True, "structural": True})

    assert result == {}
