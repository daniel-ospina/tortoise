"""degradation_chain elevated-timeout test (issue #316 review, P2).

The elevated_timeout_ms benchmark override must actually ELEVATE the
measurement window: with strategy runners still running at the default 500ms
collective cap, the cap drops them (as_completed times out → partial results),
while elevated_timeout_ms lets them complete. Uses monkeypatched strategy
runners — no graph, no DB.

#5049 — the CENSORED verdict is a function of a CONDITION, never of a tight
wall-clock margin. The previous form raced a ``time.sleep(0.6)`` leg against the
500ms cap: the collector's post-deadline second-chance sweep re-reads
``future.done()``, so on a loaded shard a >100ms scheduling delay made the leg
read as done and the assertion flipped (the main-red flake this file caused,
``assert {'fts','structural'} == {'structural'}``). The slow legs are now GATED
on a ``threading.Event`` that the product itself opens — through the
caller-supplied leg trace it appends to *after* its sweep has sealed every
verdict (``_ReleaseOnTimeout``) — so no amount of machine load can move the
censored verdict.

Note: ThreadPoolExecutor joins worker threads on exit, so the censored call
cannot return until the gate opens; that is exactly why the gate is opened by
the product's own sealed timeout record rather than by a harness timer. The
gate wait carries a generous, fail-loud guard, so a harness bug can never hang
the suite.
"""
from __future__ import annotations

import threading
import time

import pytest

from tortoise.search_engine import degradation_chain, reset_circuit_breakers

pytestmark = pytest.mark.bench

#: The pre-registered collective cap in search_engine.degradation_chain (ms).
DEFAULT_CAP_MS = 500

#: The elevated benchmark window under test (ms).
ELEVATED_CAP_MS = 5000

#: Guard on how long a gated leg waits for the gate to open. This is NOT a
#: verdict margin — the censored gate is opened causally by the product's own
#: sealed timeout record, so it is reached immediately (measured: the censored
#: call returns at ~0.51s). It exists only so a harness bug (a gate that is
#: never opened) fails loud instead of hanging the executor's join forever.
_GATE_GUARD_S = 30.0

#: When the ELEVATED call's gate opens, measured from the gated legs starting.
#: A multiple of the default cap — NOT a tight margin: the legs must still be
#: running well past the 500ms default cap (so a change that ignores
#: elevated_timeout_ms genuinely drops them) and finish well inside the 5000ms
#: elevated cap. The elevated column was never the flake site; the censored
#: column is (see the module docstring).
_ELEVATED_RELEASE_AFTER_S = 3 * (DEFAULT_CAP_MS / 1000.0)


class _ReleaseOnTimeout(list):
    """A leg trace whose first ``reason="timeout"`` record opens ``gate``.

    ``degradation_chain`` appends one entry per timed-out leg to the caller's
    trace *after* its post-deadline second-chance sweep has sealed every
    verdict, so opening the gate here cannot change the censored verdict — it
    only lets the executor's join-on-exit complete. That makes the release a
    function of the code under test's own sealed verdict rather than of a
    wall-clock margin (#5049).
    """

    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self._gate = gate

    def append(self, entry: dict) -> None:
        super().append(entry)
        if entry.get("reason") == "timeout":
            self._gate.set()


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit breakers are module-level and persist across tests (#249); a
    vector strategy that fails in an earlier test (e.g. no index in the
    embedded env) trips the breaker and silently skips vector in later
    tests. Reset before each test so strategy filtering is per-test."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


def test_elevated_timeout_collects_more_than_censored(monkeypatch):
    """A strategy still running at the 500ms collective cap is dropped, but is
    collected under elevated_timeout_ms — proving the override actually
    elevates the measurement window (more strategies → more collected rows)."""
    gate = threading.Event()
    legs_started = threading.Event()
    seen_timeout_ms: dict[str, list[int]] = {
        "fts": [], "vector": [], "structural": [],
    }

    def _gated(name, result):
        """A runner that cannot finish until the harness opens ``gate``."""

        def runner(*args, timeout_ms=DEFAULT_CAP_MS, leg_trace=None, **kwargs):
            seen_timeout_ms[name].append(timeout_ms)
            legs_started.set()
            if not gate.wait(timeout=_GATE_GUARD_S):
                raise AssertionError(
                    f"the {name} gate was never opened — the harness is at fault"
                )
            return result

        return runner

    def fast_structural(graph, kind, entity_type="point", limit=20,
                        timeout_ms=DEFAULT_CAP_MS, excluded_statuses=None,
                        leg_trace=None):
        seen_timeout_ms["structural"].append(timeout_ms)
        return [("p3", 0.5)]

    monkeypatch.setattr("tortoise.search_engine.run_fts_query",
                        _gated("fts", [("p1", 1.0)]))
    monkeypatch.setattr("tortoise.search_engine.run_vector_query",
                        _gated("vector", [("p2", 0.9)]))
    monkeypatch.setattr("tortoise.search_engine.run_structural_query",
                        fast_structural)

    strategies = {"fts": True, "vector": True, "structural": True}

    # ── Censored (default 500ms cap) ──────────────────────────────────────
    # The gate is CLOSED and only the product's own sealed timeout verdict
    # opens it, so the collector cannot observe the slow legs as done however
    # long the shard stalls.
    trace = _ReleaseOnTimeout(gate)
    censored = degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
        strategies=strategies, leg_trace=trace,
    )
    assert gate.is_set(), "the censored gate was never opened by the timeout record"
    assert set(censored) == {"structural"}
    # …and the dropped legs were dropped FOR THE TIMEOUT REASON (not silently
    # absent), which is the verdict the product sealed.
    assert {e["leg"] for e in trace if e["reason"] == "timeout"} == {"fts", "vector"}

    # ── Elevated (5000ms window) ──────────────────────────────────────────
    # Re-arm the gate; open it only once the legs are running and well past the
    # DEFAULT cap, so what collects them is the elevated window.
    gate.clear()
    legs_started.clear()

    def _open_after_default_cap():
        if not legs_started.wait(timeout=_GATE_GUARD_S):
            return
        time.sleep(_ELEVATED_RELEASE_AFTER_S)
        gate.set()

    releaser = threading.Thread(target=_open_after_default_cap, daemon=True)
    releaser.start()
    try:
        elevated = degradation_chain(
            graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
            strategies=strategies, elevated_timeout_ms=ELEVATED_CAP_MS,
            leg_trace=_ReleaseOnTimeout(gate),
        )
    finally:
        releaser.join(timeout=_GATE_GUARD_S)

    assert set(elevated) == {"fts", "vector", "structural"}
    # The elevated column collected strictly more strategy results than the
    # censored column.
    assert len(elevated) > len(censored)
    # The override reaches the runners themselves — the mechanism the elevated
    # window depends on — independent of any timing.
    assert all(
        seen == [DEFAULT_CAP_MS, ELEVATED_CAP_MS]
        for seen in seen_timeout_ms.values()
    ), seen_timeout_ms


def test_elevated_timeout_is_default_off(monkeypatch):
    """elevated_timeout_ms=None keeps byte-identical behavior: a fast strategy
    set completes fully under the default 500ms cap."""
    monkeypatch.setattr(
        "tortoise.search_engine.run_fts_query",
        lambda graph, query, entity_type="point", limit=20, timeout_ms=500,
               excluded_statuses=None, keep_numeric=False,
               expansion_terms=None: [("p1", 1.0)],
    )
    monkeypatch.setattr(
        "tortoise.search_engine.run_vector_query",
        lambda graph, query_vec, limit=20, timeout_ms=500, is_embedded=True,
        entity_type="point", vector_index_api=None,
        excluded_statuses=None: [("p2", 0.9)],
    )
    strategies = {"fts": True, "vector": True}

    results = degradation_chain(
        graph=None, query="q", kind=None, query_vec=[0.1, 0.2, 0.3],
        strategies=strategies,
    )
    assert set(results) == {"fts", "vector"}
