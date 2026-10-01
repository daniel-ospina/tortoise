"""degradation_chain elevated-timeout test (issue #316 review, P2).

The elevated_timeout_ms benchmark override must actually ELEVATE the
measurement window: with slow strategy runners, the default 500ms collective
cap truncates collection (as_completed times out → partial results), while
elevated_timeout_ms lets every strategy complete. Uses monkeypatched strategy
runners — no graph, no DB.

Note: ThreadPoolExecutor joins worker threads on exit, so the censored call
waits out the slow futures even though the collection loop already moved on —
the test is intentionally tolerant of that.

#6842 — WHY THE STUB LATENCY IS FAR PAST THE CAP, NOT JUST PAST IT. The
collective cap bounds the WAIT, not the collection: when `as_completed` raises
`TimeoutError` at the deadline, `degradation_chain` rescans every future and
collects any that is `.done()` BY THEN — not any that was done at the deadline
(read from the `except TimeoutError` arm; its comment says "discarded", the
code collects it). So the test passes only if both slow stubs are STILL RUNNING
when that rescan looks, and a stub that overshoots the cap by ~100ms leaves the
rescan gap almost no room. That is the shape observed on `7153f608e`: `fts` was
collected and `vector` was not, although both sleep an identical duration in the
same pool — one-of-two can only come from scheduling at the boundary, not from
the strategy itself. Hence the stub latency below is a multiple of the cap rather
than a hair over it; the constants and guards that hold that apart are at the
top of the file. Do not tighten it back toward the cap.
"""
from __future__ import annotations

import time

import pytest

from tortoise.search_engine import degradation_chain, reset_circuit_breakers

pytestmark = pytest.mark.bench


@pytest.fixture(autouse=True)
def _reset_breakers():
    """Circuit breakers are module-level and persist across tests (#249); a
    vector strategy that fails in an earlier test (e.g. no index in the
    embedded env) trips the breaker and silently skips vector in later
    tests. Reset before each test so strategy filtering is per-test."""
    reset_circuit_breakers()
    yield
    reset_circuit_breakers()


#: The two windows this test has to sit BETWEEN: the default collective cap,
#: which must DROP the slow legs, and the elevated window, which must COLLECT
#: them. ELEVATED_MS is threaded into the call below. CAP_MS mirrors the
#: implementation's own default (the API takes no cap), and is checked
#: behaviourally by the censored assertion as well as by the margin guard.
CAP_MS = 500
ELEVATED_MS = 5000

#: The margin the stub must clear the cap by, so that it is still RUNNING when
#: the post-deadline rescan looks (see the module docstring). This is the guard
#: that actually protects #6842, and it is deliberately NOT "greater than the
#: cap": the regression WAS a stub 100ms above the cap, which satisfies that
#: and still flakes, because the rescan gap is scheduling noise rather than a
#: fixed budget. A mutation test pins it — setting SLOW_STUB_S back to 0.6 must
#: fail on this line (verified; a plain `stub > cap` assertion passed it).
MIN_MARGIN_S = 1.0

#: Stub latency for the two "slow" strategies. Must clear CAP_MS by at least
#: MIN_MARGIN_S, and fit comfortably inside ELEVATED_MS. 2.0s gives a 1.5s
#: margin and sits well inside the elevated window.
SLOW_STUB_S = 2.0


def test_elevated_timeout_collects_more_than_censored(monkeypatch):
    """A slow strategy is dropped by the default collective cap but collected
    under an elevated window — proving the override actually elevates the
    measurement window (more strategies → more collected rows). The exact
    latencies are the constants above; deliberately not restated here, so they
    cannot go stale."""

    def slow_fts(graph, query, entity_type="point", limit=20, timeout_ms=500,
                 excluded_statuses=None, keep_numeric=False,
                 expansion_terms=None):
        time.sleep(SLOW_STUB_S)
        return [("p1", 1.0)]

    def slow_vector(graph, query_vec, limit=20, timeout_ms=500,
                    is_embedded=True, entity_type="point",
                    vector_index_api=None, excluded_statuses=None):
        time.sleep(SLOW_STUB_S)
        return [("p2", 0.9)]

    def fast_structural(graph, kind, entity_type="point", limit=20, timeout_ms=500,
                        excluded_statuses=None):
        return [("p3", 0.5)]

    monkeypatch.setattr("tortoise.search_engine.run_fts_query", slow_fts)
    monkeypatch.setattr("tortoise.search_engine.run_vector_query", slow_vector)
    monkeypatch.setattr("tortoise.search_engine.run_structural_query", fast_structural)

    strategies = {"fts": True, "vector": True, "structural": True}

    # Censored (default): 500ms collective cap → as_completed times out → only
    # the fast structural strategy is collected (fts/vector still sleeping).
    censored = degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
        strategies=strategies,
    )
    assert set(censored) == {"structural"}

    # Both bounds are load-bearing and BOTH are asserted. The margin is the
    # #6842 direction: a stub only just past the cap is collected by the
    # post-deadline rescan, so the invariant is the SIZE of the margin, not the
    # ordering. The upper bound keeps the elevated case able to collect at all.
    assert SLOW_STUB_S - CAP_MS / 1000.0 >= MIN_MARGIN_S, (
        f"stub latency {SLOW_STUB_S}s must clear the {CAP_MS}ms default cap by "
        f"at least {MIN_MARGIN_S}s, or the post-deadline rescan collects it and "
        "the censored case flakes (#6842)"
    )
    assert SLOW_STUB_S < ELEVATED_MS / 1000.0, (
        f"stub latency {SLOW_STUB_S}s must fit inside the {ELEVATED_MS}ms "
        "elevated window, else the override test inverts (see docstring)"
    )
    elevated = degradation_chain(
        graph=None, query="q", kind="claim", query_vec=[0.1, 0.2, 0.3],
        strategies=strategies, elevated_timeout_ms=ELEVATED_MS,
    )
    assert set(elevated) == {"fts", "vector", "structural"}
    # The elevated column collected strictly more strategy results than the
    # censored column.
    assert len(elevated) > len(censored)


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
