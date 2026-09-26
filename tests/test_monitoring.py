"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tortoise import monitoring

#: Generous wall-clock tolerance (seconds) for the retry-total behavioural test.
#: ``time.sleep`` can OVERSHOOT its duration on a shared/loaded box, so the
#: upper-bound assertion needs SECONDS of headroom — the old version compared
#: against ``PROBE_DB_TOTAL_TIMEOUT`` with only ~40-60ms and did genuinely
#: flake (3.1827s > 3.1s). A flaky guard in a registered CI surface is worse
#: than no guard.
RETRY_TOTAL_TIMING_TOLERANCE_S = 2.0


class FakeSDK:
    """Minimal SDK stub for testing health checks."""
    def __init__(self, db_ok=True, graph_size=42):
        self._db_ok = db_ok
        self._graph_size = graph_size

    def _get_proj(self):
        if not self._db_ok:
            raise RuntimeError("connection refused")
        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj

    def taxonomy(self):
        return {"Point": self._graph_size, "Event": 0}


class SlowColdStartSDK:
    """A REACHABLE graph whose projection cold-start is slow — the shape of
    the #3143 9,019-entity org: `_get_proj()` pays connect + the version probe
    + `_ensure_indexes()` (~28 round trips, plus an O(graph) index build when
    an index is missing), and the cost scales with graph size, while the
    `RETURN 1` reachability query itself is sub-millisecond."""
    def __init__(self, delay=0.2, graph_size=10):
        self._delay = delay
        self._graph_size = graph_size
        self.query_calls = 0  # proof the reachability query was reached

    def _get_proj(self):
        time.sleep(self._delay)  # cold-start cost, NOT a reachability signal
        proj = MagicMock()

        def _q(*args, **kwargs):
            self.query_calls += 1
            return MagicMock(result_set=[[1]])

        proj.g.query.side_effect = _q
        return proj

    def taxonomy(self):
        return {"Point": self._graph_size, "Event": 0}


def _counter_value(counter, labels=None):
    """Extract counter value from collect(). labels is {name: value} dict."""
    for m in counter.collect():
        for s in m.samples:
            if s.name.endswith("_total") and not s.name.endswith("_created_total"):
                if labels and not all(s.labels.get(k) == v for k, v in labels.items()):
                    continue
                return s.value
    return 0


@pytest.fixture(autouse=True)
def _restore_sdk_global():
    """Save/restore monitoring._sdk around every test — the metrics tests set
    it directly, and residue would leak a FakeSDK (or None) into any later
    no-arg metrics()/serve_health test in the same pytest process (#2202
    review fix)."""
    previous = monitoring._sdk
    yield
    monitoring._sdk = previous


@pytest.fixture(autouse=True)
def _fresh_probe_worker():
    """#2850: the probe worker is now process-lifetime (one daemon thread)
    instead of one per call. A test that deliberately wedges it (see
    TestProbeDb.test_never_raises_on_hung_connection, whose HungSDK sleeps
    30s) would otherwise make every following probe in the file time out
    while that call is still blocked. Reset the worker to a fresh daemon
    thread per test — the same escape hatch ops uses to recover a wedged
    worker."""
    monitoring._reset_probe_worker()
    yield
    monitoring._reset_probe_worker()


class TestProbeDb:
    """probe_db() deep-check (#1384) — never raises, hard-bounded."""

    def test_healthy_shape(self):
        result = monitoring.probe_db(FakeSDK(db_ok=True))
        assert result["ok"] is True
        assert isinstance(result["latency_ms"], (int, float))
        assert result["error"] is None

    def test_degraded_reports_error(self):
        result = monitoring.probe_db(FakeSDK(db_ok=False))
        assert result["ok"] is False
        assert "connection refused" in result["error"]
        assert isinstance(result["latency_ms"], (int, float))

    def test_non_connect_error_not_retried(self):
        """#1565: a non-connection failure (arbitrary RuntimeError here) is
        NOT a transient connect race — probe once, report degraded, never
        retry (the retry exists ONLY for the connect-refused class)."""
        calls = {"n": 0}

        class BoomSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise RuntimeError("connection refused")

        result = monitoring.probe_db(BoomSDK())
        assert result["ok"] is False
        assert calls["n"] == 1
        assert "connection refused" in result["error"]

    def test_transient_connect_error_retries_once_and_recovers(self):
        """#1565: ONE retry on a transient ConnectionError (server-startup
        race under parallel load) must NOT flip /health to degraded — the
        retry succeeds once the server answers."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class FlakySDK:
            def _get_proj(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise redis_exc.ConnectionError("transient connect refused")
                proj = MagicMock()
                proj.g.query.return_value = MagicMock(result_set=[[1]])
                return proj

        result = monitoring.probe_db(FlakySDK())
        assert result["ok"] is True
        assert calls["n"] == 2
        assert result["error"] is None

    def test_persistent_connect_error_stays_degraded_after_retry(self):
        """#1565: a PERSISTENT connect failure (real outage — NXDOMAIN,
        stopped FalkorDB) must still report degraded after the single retry:
        the retry never masks an outage."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class DeadSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise redis_exc.ConnectionError("NXDOMAIN")

        result = monitoring.probe_db(DeadSDK())
        assert result["ok"] is False
        assert calls["n"] == 2  # retried once, then still degraded
        assert "NXDOMAIN" in result["error"]

    def test_retry_rides_the_single_combined_deadline(self, monkeypatch):
        """#2988/#3143: the COMBINED (platform) shape's real ceiling is ONE
        ``PROBE_TIMEOUT`` deadline — the #1565 retry rides the REMAINDER of it,
        it does not take a second attempt bound. ``PROBE_DB_TOTAL_TIMEOUT``
        (2 x ``PROBE_TIMEOUT`` + the retry delay) is retained only as the LOOSE
        outer-alignment figure coordinators are sized above.

        This test used to assert the wall clock against that loose figure
        (~0.95s at the patched sizes) — a bound the call cannot reach (~0.25s),
        so it no longer guarded what it claimed. It now guards the REAL
        structure: (a) a spy pins the retry's ``timeout`` at the REMAINDER —
        strictly below the single bound — which is exactly what a "fresh
        second bound" regression would break, and (b) the wall-clock backstop
        is the single combined deadline. The SHIPPED ``PROBE_DB_TOTAL_TIMEOUT``
        formula is pinned separately, as the alignment figure.

        Determinism over the knife-edge: the per-attempt bound and the retry
        delay are MONKEYPATCHED DOWN so the measured total is small, and the
        SDK fails well INSIDE the (small) attempt bound so nothing races the
        worker's own timeout. ``time.sleep`` can overshoot but never undershoot,
        so the LOWER bound is safe; the UPPER backstop carries a generous NAMED
        tolerance.
        """
        import time

        import redis.exceptions as redis_exc

        # Shipped constants, captured BEFORE the patch — the drift guard at the
        # end pins these, so shrinking them for the behavioural run cannot hide
        # a drift in the shipped formula.
        shipped_timeout = monitoring.PROBE_TIMEOUT
        shipped_delay = monitoring.PROBE_RETRY_DELAY
        shipped_total = monitoring.PROBE_DB_TOTAL_TIMEOUT

        attempt_bound = 0.4
        retry_delay = 0.15
        # 8x headroom under ``attempt_bound``. This was 0.3 (100ms of slack),
        # which is a knife-edge of the SAME class this test was de-flaked for:
        # if the sleep overshoots, ``_probe_once`` hits its own per-attempt
        # timeout, and a TIMEOUT is never retried (only transient connect
        # errors are), so ``calls['n']`` becomes 1 and the retry assertion
        # fails. Measured 45% failure under load at 0.3; 0.05 leaves 8x.
        per_attempt_work = 0.05
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", attempt_bound)
        monkeypatch.setattr(monitoring, "PROBE_RETRY_DELAY", retry_delay)
        # The COMBINED shape's real ceiling: the retry rides the remainder of
        # this ONE deadline — NOT `2 x attempt_bound + retry_delay`.
        combined_deadline = attempt_bound

        calls = {"n": 0}
        # Spy on the seam the retry budget crosses WITHOUT replacing the real
        # implementation (the run stays behavioural).
        seen: list[tuple] = []
        real_probe_once = monitoring._probe_once

        def spy_probe_once(sdk, timeout=None, setup_timeout=None):
            seen.append((timeout, setup_timeout))
            return real_probe_once(sdk, timeout=timeout,
                                   setup_timeout=setup_timeout)

        monkeypatch.setattr(monitoring, "_probe_once", spy_probe_once)

        class SlowTransientSDK:
            def _get_proj(self):
                calls["n"] += 1
                # Fail transiently well INSIDE the per-attempt bound, so the
                # elapsed time really is two attempts plus the delay rather
                # than a same-tick error or a worker timeout.
                time.sleep(per_attempt_work)
                raise redis_exc.ConnectionError("transient connect refused")

        start = time.monotonic()
        result = monitoring.probe_db(SlowTransientSDK())
        elapsed = time.monotonic() - start

        assert calls["n"] == 2, "the transient retry did not happen"
        assert result["ok"] is False
        # STRUCTURAL guard: attempt 1 is the combined shape (no explicit
        # allowance), and the retry is handed the REMAINDER — strictly less
        # than the one deadline, because the first attempt and the delay were
        # spent out of it. A regression that gave the retry a FRESH bound
        # (``attempt_bound``) or the whole total would pin ``seen[1][0]`` at
        # ``attempt_bound`` and fail here; the wall clock cannot see it, because
        # each attempt fails in ~50ms.
        assert seen[0] == (None, None), seen[0]
        assert seen[1][1] is None, seen[1]  # the retry stays in COMBINED shape
        assert 0 < seen[1][0] < combined_deadline, seen[1][0]
        # SAFE lower bound: both attempts' work plus the inter-attempt delay
        # really elapsed, so this run exercises the retry path.
        assert elapsed >= 2 * per_attempt_work + retry_delay, (
            f"probe_db returned in {elapsed:.3f}s — below two attempts "
            f"({per_attempt_work}s each) plus the {retry_delay}s delay, so this "
            "measurement is not exercising the retry path"
        )
        # UPPER backstop (generous NAMED tolerance): the call must stay inside
        # its ONE combined deadline. The retry-budget structure is pinned by the
        # structural assertions above, not by this clock.
        assert elapsed <= combined_deadline + RETRY_TOTAL_TIMING_TOLERANCE_S, (
            f"probe_db took {elapsed:.3f}s — above the single combined deadline "
            f"({combined_deadline}s) plus the "
            f"{RETRY_TOTAL_TIMING_TOLERANCE_S}s tolerance"
        )
        # Drift guard, NOT a bound: the SHIPPED alignment figure must stay
        # exactly two attempt bounds plus one retry delay.
        assert shipped_total == 2 * shipped_timeout + shipped_delay, (
            "PROBE_DB_TOTAL_TIMEOUT drifted from the alignment structure it "
            "describes (2 x PROBE_TIMEOUT + PROBE_RETRY_DELAY)"
        )

    def test_never_raises_on_hung_connection(self, monkeypatch):
        """A dead socket must not hang the handler — the worker thread is
        abandoned after the hard timeout and the probe returns degraded.
        #1565: a TIMEOUT is never retried (a hung DB would just hang again)
        — exactly one probe attempt, then degraded."""
        import time

        calls = {"n": 0}

        class HungSDK:
            def _get_proj(self):
                calls["n"] += 1
                time.sleep(30)  # simulates a blocked connect on a dead URI
                raise AssertionError("should never get here")

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.probe_db(HungSDK())
        assert result["ok"] is False
        assert calls["n"] == 1  # timeout → no retry
        assert "timeout" in result["error"]
        assert result["latency_ms"] < 2000

    def test_malformed_sdk_never_raises(self):
        """#3143 review: keep the never-raise contract for a malformed SDK.
        The `_get_proj`/`proj.g` lookups run in the WORKER, so an
        AttributeError is a classified degraded result — never an exception on
        the caller thread (which would crash /health's handler)."""
        class NoProjAttr:
            pass

        result = monitoring.probe_db(NoProjAttr())
        assert result["ok"] is False
        assert "_get_proj" in result["error"]

        class NoneProj:
            def _get_proj(self):
                return None

        result = monitoring.probe_db(NoneProj())
        assert result["ok"] is False
        assert "attribute" in result["error"]

        class ProjWithoutGraph:
            def _get_proj(self):
                return object()

        result = monitoring.probe_db(ProjWithoutGraph())
        assert result["ok"] is False
        assert "attribute" in result["error"]

    def test_setup_raising_a_bare_timeout_never_reports_an_empty_error(
            self, monkeypatch):
        """#3143 review P2: on py3.12 ``concurrent.futures.TimeoutError`` IS
        ``builtins.TimeoutError``, so a callable that RAISES one (a bare
        ``TimeoutError()`` / ``socket.timeout`` from inside ``_get_proj``) is
        re-raised out of ``Future.result`` and caught by the same handler as a
        genuine phase overrun. ``setup.done()`` is True for BOTH causes, and
        ``str(bare_timeout)`` is EMPTY — so pre-fix the probe returned
        ``error=''`` (rendered as a bare 'unreachable' by /health) instead of
        the synthesized setup message.
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.5)

        class BareTimeoutSDK:
            def _get_proj(self):
                raise TimeoutError()  # str() == ""

        calls = {"n": 0}

        class MessagedTimeoutSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise TimeoutError("timed out")

        # A bare timeout falls back to the synthesized setup message — never
        # an empty string.
        bare = monitoring.probe_db(BareTimeoutSDK())
        assert bare["ok"] is False
        assert bare["error"] == "probe setup timeout after 0.5s", bare
        # A timeout WITH its own message keeps it (more informative).
        messaged = monitoring.probe_db(MessagedTimeoutSDK())
        assert messaged["ok"] is False
        assert "timed out" in messaged["error"], messaged
        # A TimeoutError is never retried (a hung DB stays hung).
        assert calls["n"] == 1, calls

    def test_default_budget_is_shared_across_the_two_phases(self, monkeypatch):
        """#3143 review: with NO explicit setup allowance (the /health shape)
        the cold-start and the query SHARE PROBE_TIMEOUT — the caller's total
        wait stays inside the pre-#3143 single bound (#1384), never 2x it.

        Asserts the QUERY's own dwell (the remaining budget), not a raw wall
        clock: shared → ~0.8s inside the query; a per-phase budget (the
        regression) → the full timeout. Wider margin than a total-elapsed
        assertion, which the repo has been burned by under parallel load
        (#1565 flake history).
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 3.0)
        calls = {"query": 0}
        entered = {}

        class SlowSetupSlowQuery:
            def _get_proj(self):
                time.sleep(2.2)  # succeeds, but eats most of the budget
                proj = MagicMock()

                def _q(*args, **kwargs):
                    calls["query"] += 1
                    entered["t"] = time.monotonic()
                    time.sleep(5.0)  # would blow the remaining budget
                    return MagicMock(result_set=[[1]])

                proj.g.query.side_effect = _q
                return proj

        result = monitoring.probe_db(SlowSetupSlowQuery())
        returned = time.monotonic()
        assert calls["query"] == 1  # setup succeeded → phase 2 was reached
        assert result["ok"] is False
        assert "timeout" in result["error"]
        # Shared budget → the query may spend only the ~0.8s left; a per-phase
        # budget would let it run the full 3.0s.
        dwell = returned - entered["t"]
        assert dwell < 2.0, f"query was given a fresh budget, not the remainder: {dwell:.2f}s"

    def test_transient_retry_is_bounded_by_the_same_deadline(self, monkeypatch):
        """#3143 review: the #1565 retry must NOT re-arm a fresh cold-start
        allowance. The documented bound is ONE deadline of
        ``setup_timeout + PROBE_TIMEOUT`` for the whole call (retry included).

        Deterministic: an INJECTED clock makes attempt 1 consume exactly 2.5s
        of the 3.0s deadline, so the retry budget must be exactly 0.5s. Without
        that consumption a regression handing the retry a fresh
        ``attempt_timeout`` (1.0s) or the whole ``total_budget`` (3.0s) would
        satisfy any loose range — the exact defect this test exists to catch.
        (No real sleeps: a wall-clock version left only ~0.5s of scheduling
        slack and could flake on a loaded runner.)
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 1.0)
        monkeypatch.setattr(monitoring, "PROBE_RETRY_DELAY", 0.0)
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))
        seen = []

        def fake_probe_once(sdk, timeout=None, setup_timeout=None):
            seen.append((timeout, setup_timeout))
            if len(seen) == 1:
                clock.t += 2.5  # the attempt consumed its deadline
            return False, "transient", True

        monkeypatch.setattr(monitoring, "_probe_once", fake_probe_once)
        result = monitoring.probe_db(object(), setup_timeout=2.0)
        assert result["ok"] is False
        assert len(seen) == 2  # retried once
        assert seen[0] == (None, 2.0)  # explicit allowance on attempt 1
        # Retry rides the remaining deadline in COMBINED shape — never a second
        # 2s cold-start allowance (which would double the documented bound).
        assert seen[1][1] is None
        # 3.0s deadline − 2.5s consumed → 0.5s. A fresh 1.0s or 3.0s allowance
        # would fail this.
        assert seen[1][0] == pytest.approx(0.5), seen[1][0]

    @pytest.mark.parametrize("overrun", [1.0, 1.2])
    def test_exhausted_deadline_never_retries(self, monkeypatch, overrun):
        """#3143 review: when attempt 1 already spent the whole deadline the
        retry must NOT fire. A negative OR ZERO remainder would otherwise be
        passed as the worker timeout, replacing the REAL transient error with
        a bogus synthesized 'probe setup timeout after <non-positive>s'. The
        ``overrun=1.0`` case pins the exact-deadline boundary (``>`` must not
        be ``>=``)."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 1.0)
        monkeypatch.setattr(monitoring, "PROBE_RETRY_DELAY", 0.0)
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))
        seen = []

        def fake_probe_once(sdk, timeout=None, setup_timeout=None):
            seen.append((timeout, setup_timeout))
            clock.t += overrun  # 1.0 = exactly the deadline, 1.2 = overrun
            return False, "connection refused", True

        monkeypatch.setattr(monitoring, "_probe_once", fake_probe_once)
        result = monitoring.probe_db(object(), setup_timeout=0.0)
        assert len(seen) == 1, seen  # no retry past the deadline
        assert result["ok"] is False
        assert result["error"] == "connection refused"  # the real error, not a fake timeout

    def test_retry_short_budget_never_masks_the_real_error(self, monkeypatch):
        """#3143 review: the ``remaining > 0`` guard does NOT cover the
        window ``0 < remaining < cold-start``. There the retry still fires,
        cannot redo the cold-start, and its synthesized setup timeout used to
        OVERWRITE the real transient error — so ``/health`` reported a clock
        artifact ("probe setup timeout after 0.01s") instead of the outage
        cause, and abandoned a second worker thread for nothing."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 1.5)
        monkeypatch.setattr(monitoring, "PROBE_RETRY_DELAY", 0.1)
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))
        seen = []

        def fake_probe_once(sdk, timeout=None, setup_timeout=None):
            seen.append((timeout, setup_timeout))
            if len(seen) == 1:
                clock.t += 1.39  # transient failure LATE in the shared budget
                return False, "NXDOMAIN / connection refused", True
            # The remainder (~0.01s) is far below the cold-start it must redo.
            return False, f"probe setup timeout after {timeout}s", False

        monkeypatch.setattr(monitoring, "_probe_once", fake_probe_once)
        result = monitoring.probe_db(object())
        assert len(seen) == 2, seen  # the retry did fire...
        assert seen[1][0] == pytest.approx(0.01), seen[1]  # ...on the remainder
        assert result["ok"] is False
        # ...but its inconclusive setup timeout must not mask the real cause.
        assert result["error"] == "NXDOMAIN / connection refused", result["error"]


class TestProbeDbBoundedAcquisition:
    """#3446 — the SDK-acquisition phase is BOUNDED by ``probe_db(acquire=…)``.

    The defect this closes: the acquisition ran on the coordinator's own
    thread with nothing bounding it, so the outer coordinator bound — which is
    DERIVED by summing an inner budget that includes
    ``PROBE_SDK_ACQUISITION_BUDGET`` — mixed one enforced term with one
    unenforced one. An inequality can only be PROVEN above a sum of deadlines
    the code actually imposes, so the constant was nominal and the derivation
    was "best-effort alignment".

    These tests are the enforcement, not the arithmetic: a deadline that
    nothing observes is exactly the property that re-stales.
    """

    def test_acquisition_phase_is_abandoned_at_its_own_budget(self, monkeypatch):
        """An acquisition that overruns is REPORTED at its budget.

        LOAD-BEARING (mutation: call ``_acquire_on_probe_worker`` with
        ``budget=None``, or drop the ``timeout=`` from ``future.result``): the
        wait becomes the acquisition's own duration, so ``probe_db`` returns
        the stub probe's verdict after ~1.0s instead of the acquisition-phase
        timeout at ~0.05s — both assertions below red.

        The acquire callable sleeps well inside the (patched) budget's
        overshoot tolerance rather than racing it: ``time.sleep`` can
        overshoot but never undershoot, so the UPPER backstop is what proves
        the wait was abandoned early, and the message pins WHICH phase
        reported.
        """
        budget = 0.05
        monkeypatch.setattr(monitoring, "PROBE_SDK_ACQUISITION_BUDGET", budget)
        acquired = {"n": 0}

        def slow_acquire():
            acquired["n"] += 1
            time.sleep(1.0)  # 20x the budget: unmistakably past the deadline
            return FakeSDK(db_ok=True)

        start = time.monotonic()
        result = monitoring.probe_db(acquire=slow_acquire)
        elapsed = time.monotonic() - start

        assert acquired["n"] == 1, "the acquisition phase did not run"
        assert result["ok"] is False, result
        assert result["error"] == (f"probe sdk acquisition timeout after {budget}s"), (
            f"the acquisition phase must report ITS OWN spelling — got "
            f"{result['error']!r}. A phase that reports another phase's error "
            "makes the fault unattributable (#3446)"
        )
        assert elapsed < 0.5, (
            f"probe_db waited {elapsed:.3f}s for an acquisition bounded at "
            f"{budget}s — the phase deadline is NOMINAL, not enforced, and the "
            "outer bound's derivation is unsound again"
        )

    def test_acquisition_runs_on_the_shared_probe_worker(self):
        """The phase runs on the probe worker, NOT the caller's thread.

        That is what bounds it: the worker is the same process-lifetime daemon
        ``_probe_once`` submits its two phases to, so a hung acquisition is
        abandoned rather than leaked as a thread per probe (#2850).

        LOAD-BEARING (mutation: acquire inline — call the callable on the
        caller's thread instead of submitting it): the recorded thread name
        becomes the caller's and this reds.
        """
        seen: dict = {}

        def acquiring():
            seen["thread"] = threading.current_thread().name
            return FakeSDK(db_ok=True)

        result = monitoring.probe_db(acquire=acquiring)
        assert result["ok"] is True, result
        assert seen["thread"].startswith("tortoise-probe-worker"), (
            f"the acquisition ran on {seen['thread']!r} — an acquisition that "
            "does not run on the bounded probe worker has no enforced deadline "
            "(#3446)"
        )

    def test_acquisition_is_one_phase_even_when_the_probe_retries(self):
        """The #1565 retry must NOT re-acquire the SDK.

        ``probe_db`` retries the PROBE on a transient connect failure; the SDK
        handle is a phase of its own and must be acquired exactly once, or a
        flapping DB pays the acquisition cost twice inside a deadline sized for
        one of each phase.

        LOAD-BEARING (mutation: re-run the acquisition before the retry —
        ``sdk = _acquire_on_probe_worker(acquire, …)[0]`` added just above the
        ``_probe_once(sdk, timeout=remaining, …)`` call): ``calls['acquire']``
        becomes 2 and this reds.
        """
        calls = {"acquire": 0, "probe": 0}

        def acquiring():
            calls["acquire"] += 1
            return TransientOnceSDK(calls)

        result = monitoring.probe_db(acquire=acquiring)
        assert result["ok"] is True, result
        assert calls["acquire"] == 1, (
            f"the SDK was acquired {calls['acquire']} times — the retry must ride "
            "the SAME handle, not re-pay the acquisition phase (#3446)"
        )
        assert calls["probe"] == 2, "the transient retry did not happen"

    def test_raising_acquisition_never_escapes(self):
        """A raising acquisition callable is classified, not propagated.

        ``probe_db``'s contract for the DB path is NEVER raise — /health must
        degrade rather than 500 (#1384).

        LOAD-BEARING (mutation: drop the ``except Exception`` around
        ``_acquire_on_probe_worker``): the RuntimeError escapes and this reds.
        """
        def broken():
            raise RuntimeError("no handle for you")

        result = monitoring.probe_db(acquire=broken)
        assert result["ok"] is False
        assert result["error"] == "no handle for you", result

    def test_refused_submission_reports_the_workers_own_message(self, monkeypatch):
        """A saturated backlog is NOT a phase timeout.

        ``_WorkerBacklogFull`` IS a ``concurrent.futures.TimeoutError`` on
        py3.12, so the deadline handler catches it; ``future.done()`` is what
        tells the two apart. The worker's own message must survive, or the
        remedy for a wedged worker reads as "the acquisition was slow".

        LOAD-BEARING (mutation: drop the ``if future.done()`` branch): the
        message becomes the synthesized acquisition timeout and this reds.
        """
        def refusing(_fn):
            future: concurrent.futures.Future = concurrent.futures.Future()
            future.set_exception(monitoring._WorkerBacklogFull(
                "tortoise-probe-worker backlog full (32) — worker wedged"))
            return future

        monkeypatch.setattr(monitoring, "_probe_worker",
                            lambda: SimpleNamespace(submit=refusing))
        result = monitoring.probe_db(acquire=lambda: FakeSDK())
        assert result["ok"] is False
        assert "backlog full" in result["error"], result
        assert "acquisition timeout" not in result["error"], result

    def test_ambiguous_phase_ownership_is_rejected(self):
        """Passing BOTH an sdk and an acquire callable is a CALL error.

        The phase needs exactly one owner; silently preferring one would leave
        the other's cost unbounded while looking handled.

        LOAD-BEARING (mutation: replace the ``if sdk is not None: raise`` guard
        with ``pass``): the ambiguity is silently resolved and this reds.
        """
        with pytest.raises(ValueError):
            monitoring.probe_db(FakeSDK(), acquire=lambda: FakeSDK())
        with pytest.raises(ValueError):
            monitoring.probe_db()


class TransientOnceSDK:
    """First ``_get_proj`` fails transiently, the second succeeds — the #1565
    retry shape, with a counter so the test can prove how many PROBES ran."""

    def __init__(self, calls):
        self._calls = calls

    def _get_proj(self):
        import redis.exceptions as redis_exc

        self._calls["probe"] += 1
        if self._calls["probe"] == 1:
            raise redis_exc.ConnectionError("transient connect refused")
        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])
        return proj


class TestProbeSetupTimeoutResolution:
    """#3143 review: the operator knob is read at CALL time and is tolerant.

    An import-time read would be frozen before ``mcp_server._load_dotenv()``
    runs (so a repo-root `.env` value would be silently ignored), and an
    unguarded ``float()`` would brick ``import tortoise.sdk`` on a blank or
    malformed value (the shipped `.env.example` line is blank-valued).
    """

    def test_unset_env_uses_default(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_PROBE_SETUP_TIMEOUT", raising=False)
        assert monitoring.probe_setup_timeout() == monitoring.PROBE_SETUP_TIMEOUT

    def test_env_override_resolved_at_call_time(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "42.5")
        assert monitoring.probe_setup_timeout() == 42.5

    @pytest.mark.parametrize("raw,expected", [
        ("1.5", 1.5),     # inclusive lower bound — same budget as /health
        ("300", 300.0),   # inclusive upper bound
    ])
    def test_env_bounds_are_inclusive(self, monkeypatch, raw, expected):
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", raw)
        assert monitoring.probe_setup_timeout() == expected

    @pytest.mark.parametrize("raw", [
        "",       # the documented blank form
        "   ",
        "abc",    # non-numeric
        "20s",
        "0",      # would be an instant permanent timeout
        "-5",
        "nan",
        "inf",
        "0.1",    # below PROBE_TIMEOUT: can only false-degrade a reachable graph
        "1e-9",   # …and this reads as "valid" without the lower BOUND
        "300.001",  # just above the accepted maximum
        "1e12",
    ])
    def test_bad_env_falls_back_to_default_never_raises(self, monkeypatch, raw):
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", raw)
        assert monitoring.probe_setup_timeout() == monitoring.PROBE_SETUP_TIMEOUT

    @pytest.mark.parametrize("raw", ["abc", "0.1", "300.001", "nan"])
    def test_invalid_env_warns_before_falling_back(
            self, monkeypatch, caplog, raw):
        """The operator-visible contract: an invalid value is not silent — the
        warning names the variable AND the fallback it substituted, so a
        typo'd 0.1 is not discovered only via a resumed false-degrade."""
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", raw)
        with caplog.at_level("WARNING", logger="tortoise.monitoring"):
            monitoring.probe_setup_timeout()
        assert "TORTOISE_PROBE_SETUP_TIMEOUT" in caplog.text, caplog.text
        assert f"using {monitoring.PROBE_SETUP_TIMEOUT}s" in caplog.text, caplog.text

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_blank_env_is_silent(self, monkeypatch, caplog, raw):
        """The shipped ``.env.example`` line is blank-valued, so warning on
        blank would warn on every default deployment."""
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", raw)
        with caplog.at_level("WARNING", logger="tortoise.monitoring"):
            assert monitoring.probe_setup_timeout() == monitoring.PROBE_SETUP_TIMEOUT
        assert [r for r in caplog.records
                if r.name.startswith("tortoise.monitoring")] == []

    def test_valid_env_is_silent(self, monkeypatch, caplog):
        """A valid in-range value is honoured silently — the warning path
        must not fire for it (e.g. a log hoisted above the early return)."""
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "42.5")
        with caplog.at_level("WARNING", logger="tortoise.monitoring"):
            assert monitoring.probe_setup_timeout() == 42.5
        assert [r for r in caplog.records
                if r.name.startswith("tortoise.monitoring")] == []

    def test_mcp_tortoise_health_honors_env_override(self, monkeypatch):
        """The `.env` knob must actually reach the tool (call-time read),
        which the old import-time constant did not."""
        from tortoise import mcp_server
        from tortoise.mcp_auth import _transport_mode

        captured = {}
        real_metrics = monitoring.metrics

        def spy_metrics(*args, **kwargs):
            captured.update(kwargs)
            return real_metrics(*args, **kwargs)

        monkeypatch.setattr(monitoring, "metrics", spy_metrics)
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "7")
        monkeypatch.setattr(mcp_server, "_get_org_sdk",
                            lambda: FakeSDK(db_ok=True, graph_size=3))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert captured["setup_timeout"] == 7.0


class TestMetricsFunction:
    """metrics() function tests."""

    def test_no_sdk_returns_honest_unknown_never_degraded(self):
        """#2202: NO probe target (no sdk= arg, nothing registered) is an
        unverified handle, NOT an observed component failure — the report must
        be an accurate intermediate state (unknown, db.ok=None), never
        'degraded'. Reporting degraded here is the onboarding lie: the HTTP
        daemon/hosted surfaces never register the module-global, so the old
        code claimed the served system was broken while /health said ok."""
        monitoring._sdk = None
        result = monitoring.metrics()
        assert result["status"] == "unknown"
        assert result["falkordb"] == "no_sdk_registered"
        assert result["db"] == {"ok": None, "latency_ms": 0.0,
                                "error": "no_sdk_registered"}
        assert result["graph_size"] == 0

    def test_registered_sdk_returns_ok(self):
        """The module-global handle (stdio path: main() registers it) still
        works when no explicit sdk= is passed."""
        monitoring._sdk = FakeSDK(db_ok=True, graph_size=7)
        result = monitoring.metrics()
        assert result["status"] == "ok"
        assert result["falkordb"] == "connected"
        assert result["db"]["ok"] is True
        assert "latency_ms" in result["db"]
        assert result["graph_size"] == 7

    def test_broken_db_returns_degraded(self):
        """#2202 pin: degraded is reserved for an observed probe FAILURE — a
        real component failing — never for a missing registration."""
        monitoring._sdk = FakeSDK(db_ok=False)
        result = monitoring.metrics()
        assert result["status"] == "degraded"
        assert "connection refused" in result["falkordb"]
        assert result["db"]["ok"] is False
        assert "connection refused" in result["db"]["error"]

    def test_includes_uptime(self):
        monitoring._sdk = FakeSDK()
        result = monitoring.metrics()
        assert result["uptime"] >= 0


class TestMetricsExplicitSdkArg:
    """metrics(sdk=...) — #2202: serving surfaces pass the SDK whose graph
    they actually serve so the report reflects the real graph even when the
    module-global handle is unregistered (HTTP daemon/hosted paths)."""

    def test_explicit_healthy_sdk_returns_ok_when_nothing_registered(self):
        """The #2202 regression: daemon-served (unregistered module-global)
        but healthy → ok, exactly as /health reports."""
        monitoring._sdk = None
        result = monitoring.metrics(sdk=FakeSDK(db_ok=True, graph_size=42))
        assert result["status"] == "ok"
        assert result["falkordb"] == "connected"
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 42
        assert "no_sdk_registered" not in str(result)

    def test_explicit_broken_sdk_returns_degraded(self):
        """degraded still fires when the SERVED graph's probe actually fails
        — the fix narrows degraded to real component failures only."""
        monitoring._sdk = None
        result = monitoring.metrics(sdk=FakeSDK(db_ok=False))
        assert result["status"] == "degraded"
        assert result["db"]["ok"] is False
        assert "connection refused" in result["db"]["error"]

    def test_explicit_sdk_overrides_registered_global(self):
        """sdk= is authoritative when passed — an explicit target never falls
        through to (or masks itself as) the module-global handle."""
        monitoring._sdk = FakeSDK(db_ok=False)
        result = monitoring.metrics(sdk=FakeSDK(db_ok=True, graph_size=3))
        assert result["status"] == "ok"
        assert result["graph_size"] == 3
        # And the reverse: a healthy registered handle must not mask a broken
        # explicitly-passed serving SDK.
        monitoring._sdk = FakeSDK(db_ok=True)
        result = monitoring.metrics(sdk=FakeSDK(db_ok=False))
        assert result["status"] == "degraded"

    def test_no_taxonomy_roundtrip_when_probe_failed(self):
        """#2202 (review fix): a FAILED probe must not drag an extra taxonomy
        graph round-trip onto the degraded health call (a dead DB degrades
        fast, bounded by the RETURN-1 probe only) — and the skipped count
        never inflates the ``errors`` field this same response reports."""
        calls = {"taxonomy": 0}

        class BrokenSDK(FakeSDK):
            def __init__(self):
                super().__init__(db_ok=False)

            def taxonomy(self):
                calls["taxonomy"] += 1
                return {"Point": 5}

        monitoring._sdk = None
        result = monitoring.metrics(sdk=BrokenSDK())
        assert result["status"] == "degraded"
        assert calls["taxonomy"] == 0
        assert result["graph_size"] == 0


class TestProbeSetupBudget:
    """#3143: the probe's cost is the projection cold-start, not `RETURN 1`.

    `_probe_once` used to bound ``sdk._get_proj()`` AND ``RETURN 1`` with the
    same 1.5s liveness budget. The cold-start is not a reachability signal —
    it is connect + `_ensure_indexes()` and, on a large graph, an index build
    over the whole graph — so a fully-reachable big graph timed out during
    setup and was reported ``db.ok=false`` / ``status=degraded`` /
    ``graph_size=0``: the onboarding gate lie. A REQUEST-PATH liveness gate
    keeps the tight bound (a fast-degrade gate, #1384); the on-demand MCP
    health tool gets an explicit setup allowance, and so does a BACKGROUND
    liveness refresher whose request path reads an in-memory snapshot (#2988/
    #3243 — see ``monitoring.PROBE_SETUP_TIMEOUT``).
    """

    def test_platform_liveness_budget_still_times_out_on_a_slow_cold_start(
            self, monkeypatch):
        """Unchanged platform behavior: a cold-start that overruns
        PROBE_TIMEOUT still reports a bounded timeout, never a hang. The
        message names the SETUP phase (the cold-start is what overran) and the
        reachability query is never reached — there is no second budget."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        sdk = SlowColdStartSDK(delay=0.2)
        result = monitoring.probe_db(sdk)
        assert result["ok"] is False
        assert "setup timeout" in result["error"], result["error"]
        assert sdk.query_calls == 0

    def test_metrics_default_shape_forwards_no_allowance(self, monkeypatch):
        """#3143 review: the request-path liveness surface that reaches the probe
        through ``metrics()`` (the standalone ``serve_health`` server) passes NO
        allowance, so the #1384 fast-degrade contract holds. A REQUEST-PATH
        liveness probe cannot absorb the allowance as gate latency; the callers
        that DO pass it (the MCP tool, and the selfhost liveness refresher — a
        background refresher whose request path reads an in-memory snapshot,
        #2988/#3243) are pinned in their own files: ``tests/test_selfhost.py``
        and ``tests/test_selfhost_health_probe_executor.py`` here, hosted
        ``_probe_db``/``_READY_PROBE`` in ``tests/test_hosted_api.py``. A
        refactor that had ``metrics()`` resolve the allowance itself (the
        natural 'make all callers benefit' change) would give this surface a
        multi-second cold-start; this pins the explicit ``setup_timeout=None``
        it forwards AND the resulting degraded status."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        forwarded = {}
        real_probe_db = monitoring.probe_db

        def spy_probe_db(target, setup_timeout=None):
            forwarded["setup_timeout"] = setup_timeout
            return real_probe_db(target, setup_timeout=setup_timeout)

        monkeypatch.setattr(monitoring, "probe_db", spy_probe_db)
        result = monitoring.metrics(sdk=SlowColdStartSDK(delay=0.2))
        assert forwarded["setup_timeout"] is None, forwarded
        assert result["status"] == "degraded"
        assert "setup timeout" in result["db"]["error"]
        assert result["graph_size"] == 0  # no taxonomy round-trip on a failed probe

    def test_query_phase_keeps_its_own_budget_with_an_allowance(
            self, monkeypatch):
        """#3143 review: an explicit allowance must NOT be inherited by the
        reachability query — the query keeps its own fresh ``PROBE_TIMEOUT``,
        so the total stays ``setup_timeout + PROBE_TIMEOUT``. A regression
        setting ``query_budget = setup_timeout`` (or the whole total) could pin
        a black-hole query for the full allowance and would pass every other
        test in this file."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        calls = []

        class SlowQuerySDK:
            def _get_proj(self):
                proj = MagicMock()

                def _q(*args, **kwargs):
                    calls.append(time.monotonic())
                    time.sleep(0.5)  # ≫ PROBE_TIMEOUT, ≪ the 20s allowance
                    return MagicMock(result_set=[[1]])

                proj.g.query.side_effect = _q
                return proj

            def taxonomy(self):
                return {"Point": 1}

        started = time.monotonic()
        result = monitoring.probe_db(SlowQuerySDK(), setup_timeout=20.0)
        elapsed = time.monotonic() - started
        assert calls, "the query phase was never reached"
        assert result["ok"] is False
        assert "probe timeout" in result["error"]
        assert "setup timeout" not in result["error"]  # the QUERY phase overran
        assert elapsed < 1.0, f"query inherited the allowance: {elapsed:.2f}s"

    def test_combined_budget_exhausted_in_setup_never_submits_the_query(
            self, monkeypatch):
        """#3143 review: in the COMBINED (platform) shape, a cold-start that
        consumes the whole budget must early-return WITHOUT submitting the
        query — the guard that keeps ``future.result`` from being handed a
        zero/negative timeout. Pinned by the observable: the query is never
        called."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 1.0)
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))
        # Deterministic discriminator: the guard's ONLY effect is that the query
        # is never SUBMITTED. Counting submits (setup=1, query=2) is exact,
        # whereas watching proj.g.query races with the abandoned worker thread.
        # #3062 port: the probe now submits BOTH phases to the shared
        # process-lifetime single-slot worker (``_probe_worker``), not a
        # per-call ``ThreadPoolExecutor`` — so count through that seam.
        submits = {"n": 0}
        real_worker = monitoring._probe_worker()

        class CountingWorker:
            def submit(self, fn):
                submits["n"] += 1
                return real_worker.submit(fn)

        monkeypatch.setattr(monitoring, "_probe_worker",
                            lambda: CountingWorker())

        class SetupHogSDK:
            def _get_proj(self):
                clock.t += 1.5  # overruns the whole shared budget
                proj = MagicMock()
                proj.g.query.return_value = MagicMock(result_set=[[1]])
                return proj

        result = monitoring.probe_db(SetupHogSDK())
        assert result["ok"] is False
        # The cold-start consumed the shared budget, so the QUERY never ran:
        # it carries the SETUP spelling (one spelling per phase, #3143 review
        # P2). "probe timeout" here would falsely claim the query was reached.
        assert result["error"] == "probe setup timeout after 1.0s"
        assert submits["n"] == 1, "the query was submitted past the deadline"

    def test_serve_health_handler_forwards_no_allowance(self, monkeypatch):
        """#3143 review: the standalone ``serve_health`` server is the third
        platform liveness caller. Its handler calls ``metrics()`` with NO
        arguments — pin that end-to-end, because a handler-level
        ``setup_timeout=probe_setup_timeout()`` would otherwise give the
        standalone liveness server a multi-second cold-start with the whole
        suite green."""
        import threading
        import urllib.request
        from http.server import HTTPServer

        from tortoise import auth

        monkeypatch.setattr(auth, "is_dev_mode", lambda: True)
        monitoring.register(FakeSDK(db_ok=True, graph_size=3))
        seen = {}
        real_probe_db = monitoring.probe_db

        def spy_probe_db(target, setup_timeout=None):
            seen["setup_timeout"] = setup_timeout
            return real_probe_db(target, setup_timeout=setup_timeout)

        monkeypatch.setattr(monitoring, "probe_db", spy_probe_db)
        server = HTTPServer(("127.0.0.1", 0), monitoring._Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/health"
            with urllib.request.urlopen(url, timeout=10) as resp:
                assert resp.status == 200
                body = json.loads(resp.read().decode())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert seen["setup_timeout"] is None, seen
        assert body["db"]["ok"] is True
        assert body["graph_size"] == 3

    def test_taxonomy_failure_is_recorded_not_raised(self):
        """#3143 review: post-fix, ``graph_size`` is newly reachable on large
        graphs. A reachable DB whose label COUNT raises must not surface as a
        crash — the report stays ok/0 and the failure is recorded in
        ``errors`` (never raised), which is the only signal distinguishing it
        from a genuinely empty graph."""
        class TaxonomyBoomSDK(FakeSDK):
            def __init__(self):
                super().__init__(db_ok=True)

            def taxonomy(self):
                raise RuntimeError("count failed")

        monitoring._sdk = None
        baseline = monitoring.metrics(sdk=FakeSDK(db_ok=True, graph_size=1))
        result = monitoring.metrics(sdk=TaxonomyBoomSDK())
        assert result["status"] == "ok"
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 0
        assert result["errors"] == baseline["errors"] + 1

    def test_deep_setup_budget_reports_a_reachable_large_graph_ok(
            self, monkeypatch):
        """#3143 regression: with the MCP tool's setup allowance, a graph
        whose projection cold-start exceeds PROBE_TIMEOUT is reported
        reachable with its REAL graph_size — not degraded/0."""
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        result = monitoring.metrics(
            sdk=SlowColdStartSDK(delay=0.2, graph_size=9019),
            setup_timeout=monitoring.PROBE_SETUP_TIMEOUT,
        )
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 9019

    def test_mcp_tortoise_health_uses_the_deep_setup_budget(self, monkeypatch):
        """The fix is only real if the tool onboarding actually calls opts
        into the setup allowance — assert it at the MCP tool boundary.

        Hermetic: pin the knob (the tool resolves it at CALL time, so an
        ambient `.env`/shell value below the 0.2s cold-start would fail this
        on correct code).
        """
        from tortoise import mcp_server
        from tortoise.mcp_auth import _transport_mode

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "20")
        monkeypatch.setattr(
            mcp_server, "_get_org_sdk",
            lambda: SlowColdStartSDK(delay=0.2, graph_size=9019))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 9019


class TestProbeQueueIsolation:
    """#3143 review P1: the shared #3062 probe slot must not charge its QUEUE
    WAIT to the reachability budget.

    The slot is deliberately ONE process-lifetime worker, so a probe can queue
    behind another probe's cold-start. ``Future.result(timeout=…)`` starts its
    clock at SUBMISSION, so before the fix a query queued behind a slow (or
    already-abandoned) cold-start spent the 1.5s reachability budget queued,
    timed out, and reported a REACHABLE graph ``degraded``/0 — the exact #3143
    symptom this PR exists to remove, surviving under concurrency. The
    unrebased head could not hit it (it used a per-call executor); the
    hand-port onto the shared slot introduced it.
    """

    def test_query_queued_behind_a_slow_setup_is_not_reported_degraded(
            self, monkeypatch):
        import threading

        # Tight reachability budget: a query whose queue wait is charged to it
        # fails long before the competing cold-start finishes.
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        setup_running = threading.Event()
        release_setup = threading.Event()

        proj = MagicMock()
        proj.g.query.return_value = MagicMock(result_set=[[1]])

        class CoordinatedSDK:
            """A reachable graph whose cold-start the test can hold open."""

            def _get_proj(self):
                setup_running.set()
                assert release_setup.wait(5.0), "test never released setup"
                return proj

            def taxonomy(self):
                return {"Point": 9019}

        holder: dict = {}

        def _call():
            holder["result"] = monitoring.metrics(
                sdk=CoordinatedSDK(), setup_timeout=20.0)

        caller = threading.Thread(target=_call, daemon=True)
        caller.start()
        try:
            assert setup_running.wait(5.0), "the cold-start never started"

            # While OUR cold-start holds the single slot, ANOTHER probe
            # submits. Its cold-start is FIFO-queued AHEAD of our query, so
            # when our setup returns the query has an occupied slot in front
            # of it (the #3143 P1 interleave). The 0.5s dwell leaves a wide
            # window for the caller thread to submit the query; the pre-fix
            # failure below is evidence the interleave actually happened.
            worker = monitoring._probe_worker()

            def _competing_cold_start():
                time.sleep(0.5)

            worker.submit(_competing_cold_start)
            release_setup.set()
            caller.join(15.0)
        finally:
            release_setup.set()
        assert not caller.is_alive(), "metrics() did not return"
        result = holder["result"]
        # The graph is reachable and `RETURN 1` is instant, so the ONLY way
        # this reports degraded/0 is the queue wait being charged to the
        # reachability budget.
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True, result
        assert result["graph_size"] == 9019

    def test_zero_leftover_with_a_free_slot_reports_the_reachable_graph(
            self, monkeypatch):
        """#3143 P1 (BOUNDARY, opposite pin): at EXACTLY zero leftover with a
        FREE slot, a REACHABLE graph must still be reported ``ok``.

        ``slot_wait_budget = max(0.0, setup_timeout - elapsed)`` is a FLOAT that
        clamps to exactly ``0.0`` when the cold-start finishes at/just past its
        allowance. Firing the guard on ``slot_wait_budget is not None`` rather
        than on truthiness turns that clamp into a false-FAIL: ``wait(0.0)``
        cannot let the worker run (the caller has not yielded the GIL, and a
        zero-timeout lock acquire does not yield), so a query submitted
        microseconds earlier is GUARANTEED to look un-started and the probe
        returns the setup spelling for a perfectly healthy, reachable graph —
        the #3143 symptom this branch exists to remove.

        Determinism: the probe clock is frozen and the cold-start advances it by
        EXACTLY the allowance, so the leftover is exactly ``0.0`` (not merely
        small). NOTHING is interposed ahead of the query, so the #3062 slot is
        FREE: as soon as the caller blocks in ``Future.result`` — which DOES
        release the GIL, unlike ``wait(0.0)`` — the worker runs the query and
        the REAL graph size is reported.

        The slot being free is what makes the zero leftover benign: with no
        queue there is no queue WAIT for the reachability budget to absorb, so
        the guard is not needed at all. (The occupied-slot counterpart, where
        the guard IS needed, is
        ``test_query_queued_behind_a_slow_setup_is_not_reported_degraded``.)
        """
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.4)
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))

        ALLOWANCE = 3.0
        ran: list[float] = []
        proj = MagicMock()

        def _q(*args, **kwargs):
            ran.append(1.0)
            return MagicMock(result_set=[[1]])

        proj.g.query.side_effect = _q

        class BoundarySDK:
            """A REACHABLE graph whose cold-start eats the WHOLE allowance."""

            def _get_proj(self):
                clock.t += ALLOWANCE  # leftover becomes exactly 0.0
                return proj

            def taxonomy(self):
                return {"Point": 9019}

        result = monitoring.metrics(sdk=BoundarySDK(), setup_timeout=ALLOWANCE)

        # THE DISCRIMINATOR: the slot is free, so the query RAN and the graph
        # is reachable with its real size. A guard that fires at a zero
        # leftover fails this same healthy probe with
        # "probe setup timeout after 3.0s" / degraded / graph_size 0.
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True, result
        assert result["db"]["error"] is None, result
        assert result["graph_size"] == 9019, result
        assert ran == [1.0], (
            "the reachability query never ran — a zero leftover must not fail "
            "a FREE-slot probe"
        )

    def test_queued_never_started_query_is_attributed_to_setup(
            self, monkeypatch):
        """#3143 review P2: a query that is QUEUED and never RAN is attributed
        to the SETUP phase — the query phase was never reached.

        The read of ``query_started`` in the ``TimeoutError`` handler resolves
        a genuine race: the single #3062 slot is occupied for the whole
        reachability budget, so the submitted ``_run_query`` never starts and
        ``Future.result`` times out with the event still CLEAR. The phase at
        fault is the wait for the slot, so the error must carry the SETUP
        spelling; "probe timeout after …" would falsely claim the query itself
        was reached and overran.

        Determinism — no wall-clock race:
        * the probe clock is frozen and ``_get_proj`` advances it by EXACTLY
          the cold-start allowance, so the slot-wait leftover is exactly
          ``0.0`` and the truthiness guard above the ``try`` cannot fire (a
          zero leftover must stay benign — the FREE-slot counterpart is
          ``test_zero_leftover_with_a_free_slot_reports_the_reachable_graph``);
        * the slot is occupied by a blocker QUEUED FROM INSIDE ``_get_proj``,
          i.e. put on the single slot's FIFO queue BEFORE the caller submits
          the query. FIFO order — not thread scheduling — guarantees the
          blocker runs first, so the query stays pending for the whole budget.
        """
        import threading

        query_budget = 0.05
        ALLOWANCE = 3.0
        clock = SimpleNamespace(t=0.0)
        monkeypatch.setattr(monitoring, "time", SimpleNamespace(
            monotonic=lambda: clock.t, sleep=lambda _s: None))

        release_slot = threading.Event()
        ran = {"query": 0}
        proj = MagicMock()

        def _q(*args, **kwargs):
            ran["query"] += 1
            return MagicMock(result_set=[[1]])

        proj.g.query.side_effect = _q

        worker = monitoring._probe_worker()

        class OccupiedSlotSDK:
            """A reachable graph whose cold-start QUEUES a blocker ahead of
            the query, occupying the single probe slot for the budget."""

            def _get_proj(self):
                clock.t += ALLOWANCE  # leftover becomes exactly 0.0
                # Queued from INSIDE the slot, so it precedes the caller's
                # query submission in the single slot's FIFO order.
                worker.submit(lambda: release_slot.wait(5.0))
                return proj

        try:
            ok, error, transient = monitoring._probe_once(
                OccupiedSlotSDK(), timeout=query_budget,
                setup_timeout=ALLOWANCE)

            assert ok is False, (ok, error)
            # THE DISCRIMINATOR: the query never started, so the SETUP phase
            # owns the error. The pre-fix fall-through spelled it
            # "probe timeout after 0.05s" (a query that overran).
            assert error == (
                f"{monitoring._PROBE_SETUP_TIMEOUT_MSG}{ALLOWANCE}s"), error
            assert error == "probe setup timeout after 3.0s", error
            assert "probe timeout after" not in error, error
            assert transient is False, transient
            assert ran["query"] == 0, (
                "the queued query RAN — the blocker did not hold the slot")
        finally:
            # Release the blocker, then flush the abandoned query submission so
            # the process-lifetime worker is idle for the next test.
            release_slot.set()
        worker.submit(lambda: None).result(timeout=5.0)


class TestProbeSetupBudgetIntegration:
    """#3143 at the REAL-projection layer — the issue's integration surface.

    The pure-unit class above uses a stub; this one forces the #3143 shape on a
    real FalkorProjection: real connect + `_ensure_indexes()` cold-start, real
    `RETURN 1`, real `taxonomy()` label counts. Only the cold-start TIMING is
    approximated (a sleep) — the measured real cost is graph-size dependent
    (~28 sequential round trips; 229ms on a 3,000-node server graph vs 0.7ms
    for `RETURN 1`), which a unit test cannot reproduce without a large graph.

    This class also PINS the deliberate divergence the fix creates on a
    reachable-but-slow graph: `/health` (tight shared budget) says degraded for
    the #1384 fast-degrade contract, while the on-demand `tortoise_health`
    tool says ok. That is the intended new contract, not an accident. NOTE it
    does NOT satisfy #3143's Indicator 2 ("`tortoise_health` and `/health`
    agree on the same instance within one probe cycle") — the two surfaces
    deliberately DIVERGE here, and that indicator is descoped to #3243. What
    this pins is narrower: the tool's verdict about the graph (reachable, with
    its real graph_size) is no longer a false degraded/0.
    """

    def test_real_projection_slow_cold_start_ok_with_real_graph_size(
            self, sdk_factory, monkeypatch):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("MERGE (p:Point {id:'3143-p1'}) SET p.pointKind='fact'")
        real_size = sum(sdk.taxonomy().values())
        assert real_size > 0

        # The cold-start overruns the liveness budget; the graph answers fine.
        def slow_cold_start():
            time.sleep(0.2)
            return proj

        monkeypatch.setattr(sdk, "_get_proj", slow_cold_start, raising=False)
        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)

        tight = monitoring.probe_db(sdk)  # platform liveness shape — unchanged
        assert tight["ok"] is False
        assert "setup timeout" in tight["error"]

        result = monitoring.metrics(
            sdk=sdk, setup_timeout=monitoring.PROBE_SETUP_TIMEOUT)
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        # The REAL taxonomy count, not a stub attribute — and this is the
        # unbudgeted round-trip documented on monitoring.metrics().
        assert real_size > 0
        assert result["graph_size"] == real_size
        # Issue #3143's third leg: the agent's diagnostic fallback
        # (tortoise_status → sdk.status()) must agree with the health report.
        assert sdk.status()["total_entities"] == result["graph_size"]


class TestRecordFunctions:
    """record_* function tests."""

    def test_record_ingest_sets_timestamp(self):
        monitoring._last_ingest = None
        monitoring.record_ingest()
        assert monitoring._last_ingest is not None
        assert monitoring._last_ingest > 0

    def test_record_error_increments(self):
        before = _counter_value(monitoring.ERROR_COUNT)
        monitoring.record_error()
        monitoring.record_error()
        assert _counter_value(monitoring.ERROR_COUNT) == before + 2

    def test_record_cost_by_team(self):
        before_e = _counter_value(monitoring.TEAM_COST, {"team": "eldato"})
        before_a = _counter_value(monitoring.TEAM_COST, {"team": "app-team"})

        monitoring.record_cost("eldato", 150)
        monitoring.record_cost("eldato", 50)
        monitoring.record_cost("app-team", 75)

        assert _counter_value(monitoring.TEAM_COST, {"team": "eldato"}) == before_e + 200
        assert _counter_value(monitoring.TEAM_COST, {"team": "app-team"}) == before_a + 75


class TestMetricsEndpoint:
    """Prometheus /metrics endpoint content tests."""

    def test_generate_latest_includes_counters(self):
        """Prometheus text output includes our custom counters."""
        from prometheus_client import generate_latest
        body = generate_latest()
        assert b"tortoise_requests_total" in body
        assert b"tortoise_errors_total" in body
        assert b"tortoise_team_cost_cents" in body


class TestProbeWorkerNoLeak:
    """#2850: a hung probe must never leak a thread per call.

    The pre-fix ``_probe_once`` built a fresh ``ThreadPoolExecutor`` per probe
    and abandoned its worker with ``shutdown(wait=False)`` — a black-holed
    FalkorDB leaked one OS thread (and the never-closed DB connection the
    abandoned call held) on EVERY health check. This is the exact regression
    test for the mechanism that made Fly see CHECKS 0/1 while the process was
    still serving localhost.
    """

    def test_hung_probe_does_not_leak_a_thread_per_call(self, monkeypatch):
        import threading
        import time

        class HungSDK:
            def _get_proj(self):
                time.sleep(600)  # black-holed connect — never returns
                raise AssertionError("unreachable")

        monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)
        monitoring._reset_probe_worker()
        before = sum(1 for t in threading.enumerate() if t.is_alive())
        for _ in range(8):
            result = monitoring.probe_db(HungSDK())
            assert result["ok"] is False
        after = sum(1 for t in threading.enumerate() if t.is_alive())
        # Old shape: +8 (one ThreadPoolExecutor worker per call). New shape:
        # at most the single shared daemon probe worker.
        assert after - before <= 1, (
            f"{after - before} threads leaked for 8 hung probes — "
            "per-call ThreadPoolExecutor regressed")


class TestHealthProbe:
    """#2850: single-flight, hard-bounded coordinator read by /health."""

    @staticmethod
    def _hang(calls):
        def _fn():
            calls["n"] += 1
            import time
            time.sleep(600)
            return {"ok": True, "latency_ms": 0.0, "error": None}
        return _fn

    def test_returns_within_bound_when_probe_hangs_forever(self):
        """A probe that NEVER returns must not stop run() from returning.

        Pre-fix /health rode the shared asyncio default executor and could
        queue behind saturated workers; the coordinator never touches that
        pool and bounds itself at ``timeout``.
        """
        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.25,
                                       stale_after=30.0)
        import asyncio
        import time

        start = time.monotonic()
        result = asyncio.run(probe.run())
        elapsed = time.monotonic() - start
        assert result["ok"] is False
        assert elapsed < 1.0, f"run() took {elapsed:.2f}s — not hard-bounded"
        assert probe.info()["in_flight"] is True

    def test_concurrent_reads_coalesce_onto_one_probe(self):
        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.3,
                                       stale_after=30.0)
        import asyncio
        import time

        async def _fan_out():
            start = time.monotonic()
            results = await asyncio.gather(*[probe.run() for _ in range(8)])
            return results, time.monotonic() - start

        results, elapsed = asyncio.run(_fan_out())
        assert calls["n"] == 1, (
            f"{calls['n']} probes ran for 8 concurrent reads — not coalesced")
        assert elapsed < 1.0
        assert all(r["ok"] is False for r in results)

    def test_repeated_reads_do_not_accumulate_threads(self):
        """30 sequential health checks against a wedged DB must not grow
        threads (the 15s-interval checker during the incident)."""
        import asyncio
        import threading

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=30.0)

        async def _repeat():
            for _ in range(30):
                await probe.run()

        before = sum(1 for t in threading.enumerate() if t.is_alive())
        asyncio.run(_repeat())
        after = sum(1 for t in threading.enumerate() if t.is_alive())
        assert calls["n"] == 1, f"{calls['n']} probes — reads accumulated work"
        assert after - before <= 1, f"{after - before} threads accumulated"

    def test_wedged_probe_reports_degraded_once_stale(self):
        import asyncio
        import time

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=0.15, max_supersedes=0)
        first = asyncio.run(probe.run())
        assert first["ok"] is False
        time.sleep(0.16)
        stale = asyncio.run(probe.run())
        assert stale["ok"] is False
        assert "in flight" in stale["error"] or "stale" in stale["error"]

    def test_supersede_is_capped(self):
        """A permanent wedge may start at most max_supersedes extra workers."""
        import asyncio
        import time

        calls = {"n": 0}
        probe = monitoring.HealthProbe(self._hang(calls), timeout=0.05,
                                       stale_after=0.05, max_supersedes=2)
        for _ in range(6):
            asyncio.run(probe.run())
            time.sleep(0.06)
        assert calls["n"] <= 1 + 2, f"supersede cap breached: {calls['n']} probes"
        assert probe.info()["supersedes"] <= 2

    def test_supersede_budget_resets_after_a_live_completion(self):
        """#2850: ``_run`` resets ``_supersedes`` to 0 on a live completion.

        The reset is load-bearing for the layered-timeout rationale: the cap
        bounds ONE wedge EPISODE, not the process lifetime. Delete
        ``self._supersedes = 0`` in ``HealthProbe._run`` and this test fails —
        the counter would stay exhausted after the first episode, so a SECOND
        wedge could never be superseded.

        ``timeout`` is deliberately 1.0s, NOT a value close to
        ``stale_after``: the live completion is served by a freshly started
        daemon thread, so a tight bound races the SCHEDULER rather than the
        logic (measured 83% failure under load at timeout=0.05, always on
        "the live completion was not served"). Staleness is still driven by
        the small ``stale_after``, so this costs ~1s per wedged call, not
        fidelity.
        """
        import asyncio
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] in (1, 3, 4):  # generations that wedge forever
                time.sleep(600)
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=1.0, stale_after=0.05,
                                       max_supersedes=1)
        # Episode 1: wedge generation 1, supersede it with generation 2, which
        # COMPLETES (the live completion that must reset the counter).
        assert asyncio.run(probe.run())["ok"] is False
        time.sleep(0.06)
        assert asyncio.run(probe.run())["ok"] is True, (
            "the live completion was not served")
        assert probe.info()["supersedes"] == 0, (
            "a live completion must reset the supersede counter")
        # Episode 2: a fresh wedge must be able to reach the cap AGAIN.
        time.sleep(0.06)
        asyncio.run(probe.run())  # starts generation 3 (wedged)
        time.sleep(0.06)
        asyncio.run(probe.run())  # supersedes it with generation 4
        assert calls["n"] == 4, (
            f"only {calls['n']} probe generations ran — the supersede budget did "
            "not reset, so the second wedge episode could never supersede")
        assert probe.info()["supersedes"] == 1

    def test_healthy_probe_returns_result_immediately(self):
        import asyncio

        probe = monitoring.HealthProbe(
            lambda: {"ok": True, "latency_ms": 1.0, "error": None}, timeout=1.0)
        result = asyncio.run(probe.run())
        assert result == {"ok": True, "latency_ms": 1.0, "error": None}
        assert probe.info()["in_flight"] is False

    # ── #2850 round-3 review P2: /health must not probe per read ──

    def test_snapshot_does_not_start_a_probe_per_read(self):
        """``/health`` is a SKIP_AUTH route and calls ``snapshot()`` on every
        request. ``begin()`` used to start a fresh probe as soon as the previous
        finished, so the read path amplified into one DB ``RETURN 1`` per
        request and duplicated the background refresher."""
        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, refresh_budget=30.0)
        assert probe.wait()["ok"] is True
        assert calls["n"] == 1
        for _ in range(50):
            assert probe.snapshot()["ok"] is True
        assert calls["n"] == 1, f"/health read amplified to {calls['n']} DB probes"

    def test_snapshot_self_heals_a_dead_refresher_once_stale(self):
        """The self-heal survives — a dead refresher is still recovered, just
        no sooner than the refresh budget (one probe, not one per read)."""
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, refresh_budget=0.3)
        assert probe.wait()["ok"] is True
        assert calls["n"] == 1
        probe.snapshot()  # within budget: no new probe
        assert calls["n"] == 1
        time.sleep(0.35)
        probe.snapshot()  # budget exceeded: self-heal starts one
        deadline = time.monotonic() + 2.0
        while calls["n"] < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert calls["n"] == 2, "a dead refresher was never recovered"

    def test_refresh_budget_callable_is_resolved_live_not_frozen(self):
        """Round-4 review P2: the self-heal gate must follow the refresher's
        RESOLVED period. ``_HEALTH_PROBE`` passes ``_health_probe_interval``
        (a callable) because the operator can set that period anywhere in
        0.5-15s; a frozen float would gate on the import-time default."""
        budget = {"s": 10.0}
        probe = monitoring.HealthProbe(
            lambda: {"ok": True, "latency_ms": 1.0, "error": None},
            refresh_budget=lambda: budget["s"])
        assert probe._refresh_budget_now() == 10.0
        budget["s"] = 15.0
        assert probe._refresh_budget_now() == 15.0, (
            "a callable refresh budget was frozen at first read")

    def test_refresh_budget_callable_failure_falls_back(self):
        """A broken callable must not break the unauthenticated /health read."""
        def _boom():
            raise RuntimeError("nope")

        probe = monitoring.HealthProbe(
            lambda: {"ok": True, "latency_ms": 1.0, "error": None},
            refresh_budget=_boom)
        assert probe._refresh_budget_now() == monitoring.PROBE_STALE_AFTER

    # ── #2850 review P1: readiness must not serve a pre-outage verdict ──

    def test_fresh_only_fails_closed_when_the_budget_expires(self):
        """The readiness fail-open the review caught.

        Prime the coordinator with a completed ``ok: True``, then wedge the
        probe. Without ``fresh_only`` the budget-expiry branch returned
        ``_view_locked()`` — the pre-outage verdict, still inside the 30s
        ``stale_after`` window — so /health/ready answered 200 "connected"
        for seconds after the plane died.
        """
        import asyncio
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)  # black-holed plane: never returns
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.15, stale_after=30.0,
                                       fresh_only=True)
        assert asyncio.run(probe.run())["ok"] is True
        second = asyncio.run(probe.run())
        assert second["ok"] is False, (
            "fresh_only served the completed pre-outage verdict")
        assert "fail" in second["error"] or "old" in second["error"]

    def test_fresh_only_wait_fails_closed_too(self):
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.15, stale_after=30.0,
                                       fresh_only=True)
        assert probe.wait()["ok"] is True
        assert probe.wait()["ok"] is False

    def test_default_mode_still_serves_last_known_good(self):
        """/health keeps the documented "stale but honest" fallback."""
        import asyncio
        import time

        calls = {"n": 0}

        def _fn():
            calls["n"] += 1
            if calls["n"] == 1:
                return {"ok": True, "latency_ms": 1.0, "error": None}
            time.sleep(600)
            return {"ok": True, "latency_ms": 1.0, "error": None}

        probe = monitoring.HealthProbe(_fn, timeout=0.1, stale_after=30.0)
        assert asyncio.run(probe.run())["ok"] is True
        # NOT fresh_only: the last completed result is still inside
        # stale_after, so /health is allowed to report it.
        assert asyncio.run(probe.run())["ok"] is True


@pytest.fixture(autouse=True)
def _clean_heartbeat_and_listeners():
    """#2850: the loop heartbeat, the in-flight gauge and the ``/healthz``
    listener are process-global (the listener is process-lifetime BY DESIGN).
    Keep each test starting from "never ticked"/"no work in flight" and leave
    no listener bound behind."""
    monitoring._reset_heartbeat()
    monitoring._reset_workload()
    yield
    monitoring._reset_heartbeat()
    monitoring._reset_workload()
    monitoring.stop_health_listener()


# ═══════════════════════════════════════════════════════════════════════════
# #2850 — event-loop heartbeat, dedicated /healthz listener, stall watchdog
# ═══════════════════════════════════════════════════════════════════════════


def _http_get(port: int, path: str = "/healthz", host: str = "127.0.0.1"):
    """Raw GET against the dedicated listener; returns ``(status, body)``."""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestLoopHeartbeat:
    """The liveness signal has to be produced BY the loop (#2850 item 3)."""

    def test_never_ticked_is_stale_not_healthy(self):
        """Fail-closed: no evidence of liveness must never read as live."""
        monitoring._reset_heartbeat()
        assert monitoring.loop_heartbeat_age() is None
        assert monitoring.loop_is_stale() is True
        info = monitoring.loop_heartbeat_info()
        assert info["loop_age_ms"] is None
        assert info["loop_stale"] is True
        assert info["loop_ticks"] == 0

    def test_fresh_tick_is_not_stale(self):
        import time

        monitoring.heartbeat_record()
        assert monitoring.loop_heartbeat_age() < 1.0
        assert monitoring.loop_is_stale() is False
        assert monitoring.loop_heartbeat_info()["loop_stale"] is False
        assert monitoring.loop_heartbeat_info()["loop_ticks"] == 1
        # A tick in the past crosses the threshold.
        monitoring.heartbeat_record(at=time.monotonic() - monitoring.LOOP_STALE_AFTER - 1.0)
        assert monitoring.loop_is_stale() is True

    def test_heartbeat_task_ticks_the_loop_and_stops_when_it_is_blocked(self):
        """Two halves of the same claim: the task proves the loop RAN, and it
        stops advancing the moment the loop cannot run (a synchronous block),
        which is exactly what /healthz and the watchdog consume."""
        import asyncio
        import contextlib
        import time

        async def _run() -> tuple[int, int, int]:
            task = asyncio.get_running_loop().create_task(
                monitoring.loop_heartbeat_task(0.02))
            await asyncio.sleep(0.2)
            ticks = monitoring.heartbeat_read()[1]
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            before = monitoring.heartbeat_read()[1]
            time.sleep(0.25)  # synchronous block: the loop cannot schedule
            return ticks, before, monitoring.heartbeat_read()[1]

        ticks, before, after = asyncio.run(_run())
        assert ticks >= 2, f"heartbeat task did not tick (ticks={ticks})"
        assert after == before, "a blocked loop must not advance the heartbeat"

    def test_heartbeat_uses_monotonic_not_wall_clock(self):
        """Pins the documented clock choice (#2850 item 3). systemd's
        WatchdogSec and Erlang's ``heart`` both false-trigger on a wall-clock
        step (NTP/suspend); ``time.time`` must not appear on this path."""
        import inspect

        src = "".join(inspect.getsource(f) for f in (
            monitoring.heartbeat_record,
            monitoring.loop_heartbeat_age,
            monitoring.loop_heartbeat_task,
        ))
        assert "time.monotonic()" in src
        assert "time.time(" not in src


class TestDaemonWorker:
    """#2850: blocking work that must not occupy (or hold open) the default pool."""

    def test_runs_blocking_call_on_a_daemon_thread(self):
        import asyncio
        import threading

        result = asyncio.run(
            monitoring.run_on_daemon_worker(lambda: 42, name="test-daemon-worker"))
        assert result == 42
        assert any(t.name == "test-daemon-worker" and t.daemon
                   for t in threading.enumerate()), \
            "named worker thread is not a daemon"

    def test_wedged_worker_does_not_block_loop_shutdown(self):
        """The reason ``to_thread`` is wrong for the sweeps: ``asyncio.run``
        calls ``loop.shutdown_default_executor()``, which JOINS every
        default-executor worker — a call blocked on a black-holed socket would
        hold uvicorn's process shutdown open. A daemon worker is abandonable.
        """
        import asyncio
        import contextlib
        import time

        async def _run() -> float:
            async def _call():
                return await monitoring.run_on_daemon_worker(
                    lambda: time.sleep(30), name="test-daemon-wedge")

            task = asyncio.get_running_loop().create_task(_call())
            await asyncio.sleep(0.1)
            start = time.monotonic()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            return time.monotonic() - start

        start = time.monotonic()
        cancel_latency = asyncio.run(_run())
        total = time.monotonic() - start
        assert cancel_latency < 1.0, f"cancel took {cancel_latency:.2f}s"
        assert total < 2.0, (
            f"asyncio.run took {total:.2f}s — it joined a wedged worker "
            "(this is what the shared default executor would do)")


class TestHealthzListener:
    """#2850 item 4: the dedicated, off-loop liveness port."""

    def test_default_target_is_the_fixed_contract(self, monkeypatch):
        """Port 9090 on 0.0.0.0 — another agent wires the Fly check to it."""
        monkeypatch.delenv("TORTOISE_HEALTHZ_PORT", raising=False)
        monkeypatch.delenv("TORTOISE_HEALTHZ_BIND", raising=False)
        assert monitoring.HEALTHZ_PORT == 9090
        assert monitoring.HEALTHZ_BIND == "0.0.0.0"
        assert monitoring.resolve_healthz_target() == ("0.0.0.0", 9090)
        # env overrides win (tests/self-host)
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "8123")
        monkeypatch.setenv("TORTOISE_HEALTHZ_BIND", "127.0.0.1")
        assert monitoring.resolve_healthz_target() == ("127.0.0.1", 8123)
        # an explicit argument wins over env
        assert monitoring.resolve_healthz_target(port=1, bind="10.0.0.1") == ("10.0.0.1", 1)

    def test_returns_200_when_the_loop_is_fresh(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        assert server is not None
        monitoring.heartbeat_record()
        status, body = _http_get(server.server_address[1])
        assert status == 200, body
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["loop_stale"] is False
        assert payload["loop_age_ms"] >= 0

    def test_returns_503_when_the_heartbeat_is_stale(self):
        """#2850 item 4 — the stated requirement: 503 when the loop is stale."""
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record(
            at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
        status, body = _http_get(server.server_address[1])
        assert status == 503, body
        payload = json.loads(body)
        assert payload["status"] == "stale"
        assert payload["loop_stale"] is True

    def test_returns_503_when_the_heartbeat_never_ticked(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring._reset_heartbeat()
        status, _ = _http_get(server.server_address[1])
        assert status == 503

    def test_busy_loop_is_not_reported_as_wedged(self):
        """Round-3 review P1: a merely BUSY loop is not a wedged loop.

        ``_capture_session_impl`` runs seconds-long synchronous DB/LLM work on
        the event loop, so a legitimate request can exceed LOOP_STALE_AFTER.
        With the deferred Fly check pointing here and 2xx-means-healthy, a 503
        for that case would de-register the sole machine (total outage). The
        signal must therefore be STALE **AND** IDLE — the same idle predicate
        the stall watchdog uses.
        """
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record(
            at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
        monitoring.workload_enter()
        try:
            status, body = _http_get(server.server_address[1])
        finally:
            monitoring.workload_exit()
        assert status == 200, body
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["loop_stale"] is True
        assert payload["workload_idle"] is False

    def test_unknown_path_is_404(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        status, _ = _http_get(server.server_address[1], path="/metrics")
        assert status == 404

    def test_bind_never_does_a_dns_lookup(self, monkeypatch):
        """#2850 regression: the bind must not resolve the bind address.

        ``HTTPServer.server_bind`` calls ``socket.getfqdn('0.0.0.0')`` for the
        ``Server:`` header — a reverse-DNS round trip measured at **5.0 s** on
        a macOS box with no PTR record, executed on the event loop (the
        lifespan calls ``start_health_listener`` synchronously). Poisoning
        ``getfqdn`` is the deterministic form of "does the bind block on DNS":
        the listener must still come up, and still answer.
        """
        def _boom(*_a, **_k):
            raise AssertionError("socket.getfqdn on the startup path (#2850)")

        monkeypatch.setattr("socket.getfqdn", _boom)
        server = monitoring.start_health_listener(port=0, bind="0.0.0.0")
        assert server is not None
        monitoring.heartbeat_record()
        status, body = _http_get(server.server_address[1])
        assert status == 200, body

    def test_is_idempotent_per_port(self):
        a = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        b = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        assert a is b
        resolved = a.server_address[1]
        assert monitoring.start_health_listener(port=resolved, bind="127.0.0.1") is a

    def test_answers_while_a_sibling_event_loop_is_wedged(self):
        """The listener must not share fate with an asyncio loop (#2850 item 4:
        \"run off the asyncio event loop so it cannot be starved by the app's
        loop\"). A loop wedged inside a synchronous sleep in the same process
        must not delay the answer."""
        import asyncio
        import threading
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()

        def _wedge() -> None:
            async def _spin():
                deadline = time.monotonic() + 1.0
                while time.monotonic() < deadline:
                    time.sleep(0.05)  # never awaits: wedges THIS loop
            asyncio.run(_spin())

        t = threading.Thread(target=_wedge, daemon=True)
        t.start()
        try:
            time.sleep(0.15)  # let the loop wedge
            start = time.monotonic()
            status, body = _http_get(server.server_address[1])
            elapsed = time.monotonic() - start
        finally:
            t.join(timeout=5)
        assert status == 200, body
        assert elapsed < 0.5, f"/healthz took {elapsed:.2f}s behind a wedged loop"

    def test_bind_failure_logs_and_returns_none_by_default(self, monkeypatch, caplog):
        """A busy port must not crash startup by default, but it must NOT be
        silent either — the platform check on that port will fail, loudly."""
        import logging
        import socket

        monkeypatch.delenv("TORTOISE_HEALTHZ_REQUIRED", raising=False)
        port = _free_port()
        squatter = socket.socket()
        squatter.bind(("0.0.0.0", port))
        squatter.listen(1)
        try:
            with caplog.at_level(logging.ERROR, logger="tortoise.monitoring"):
                server = monitoring.start_health_listener(port=port, bind="0.0.0.0")
        finally:
            squatter.close()
        assert server is None
        assert any("could not bind" in r.getMessage() for r in caplog.records)

    def test_bind_failure_is_fatal_when_required(self, monkeypatch):
        """``TORTOISE_HEALTHZ_REQUIRED=1`` turns the deploy-time contract into
        a hard failure instead of a logged degradation."""
        import socket

        monkeypatch.setenv("TORTOISE_HEALTHZ_REQUIRED", "1")
        port = _free_port()
        squatter = socket.socket()
        squatter.bind(("0.0.0.0", port))
        squatter.listen(1)
        try:
            with pytest.raises(RuntimeError, match="could not bind"):
                monitoring.start_health_listener(port=port, bind="0.0.0.0")
        finally:
            squatter.close()


class TestHealthzHardening:
    """#2850 review P1/P2: the unauthenticated liveness port must not become
    the way the machine is exhausted, and must not leak the interpreter."""

    @staticmethod
    def _raw(port: int, request: bytes, *, read_timeout: float = 5.0) -> bytes:
        import socket

        with socket.create_connection(("127.0.0.1", port), timeout=read_timeout) as s:
            s.settimeout(read_timeout)
            s.sendall(request)
            chunks = []
            while True:
                try:
                    data = s.recv(4096)
                except TimeoutError:
                    break
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)

    def test_version_banner_does_not_disclose_python(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        raw = self._raw(server.server_address[1],
                        b"GET /healthz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert b"Server: tortoise-healthz" in raw, raw
        assert b"Python/" not in raw, "the liveness port leaks the Python version"
        assert raw.startswith(b"HTTP/1.1 200"), raw
        assert b"Connection: close" in raw

    def test_404_also_carries_the_fixed_banner(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        raw = self._raw(server.server_address[1],
                        b"GET /secrets HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 404"), raw
        assert b"Python/" not in raw

    def test_request_body_is_rejected_without_being_read(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        raw = self._raw(
            server.server_address[1],
            b"GET /healthz HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n"
            b"Connection: close\r\n\r\nhello")
        assert raw.startswith(b"HTTP/1.1 413"), raw

    def test_non_get_methods_are_405(self):
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        raw = self._raw(
            server.server_address[1],
            b"POST /healthz HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n"
            b"Connection: close\r\n\r\n")
        assert raw.startswith(b"HTTP/1.1 405"), raw

    def test_a_slow_connection_does_not_block_a_sibling_client(self):
        """Slowloris resistance: a half-open request holds a thread but a real
        check must still be answered promptly."""
        import socket
        import time

        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        port = server.server_address[1]
        slow = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            # Handshake started, request never terminated.
            slow.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")
            start = time.monotonic()
            status, body = _http_get(port)
            elapsed = time.monotonic() - start
        finally:
            slow.close()
        assert status == 200, body
        assert elapsed < 2.0, f"a sibling client waited {elapsed:.2f}s"

    def test_saturated_listener_sheds_load_with_503(self, monkeypatch):
        """The thread cap must answer 503 rather than spawn unbounded threads.

        Round-2 review: the pre-fix version set ``HEALTHZ_MAX_THREADS=0``, so
        EVERY connection took the reject branch and no slot was ever ACQUIRED
        — it could not observe a semaphore leak, the failure mode it appeared
        to guard. With cap=2 this asserts, in order:

        1. more than ``cap`` SEQUENTIAL requests all return 200 (proves slots
           are RELEASED, not leaked — a leak starts 503-ing after ``cap``);
        2. ``cap`` concurrently-held partial requests saturate the listener;
        3. every probe issued while saturated gets a DELIVERED 503 (round-2
           fix: unread request bytes used to make the close an RST, so the
           first probe saw 503 and later ones ``ConnectionResetError``);
        4. closing the held requests releases the slots and 200 comes back.
        """
        import socket
        import time

        monkeypatch.setattr(monitoring, "HEALTHZ_MAX_THREADS", 2)
        cap = monitoring.HEALTHZ_MAX_THREADS
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        port = server.server_address[1]
        monitoring.heartbeat_record()

        # (1) cap*2+1 SEQUENTIAL checks must ALL be 200. Any leaked slot would
        # turn these into 503s well before the loop ends.
        for i in range(2 * cap + 1):
            status, body = _http_get(port)
            assert status == 200, (i, status, body)

        # (2) Pin every slot with a partial request: the handler thread blocks
        # reading the request line, so it never releases while held.
        held = []
        try:
            for _ in range(cap):
                s = socket.create_connection(("127.0.0.1", port), timeout=5)
                s.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n")
                held.append(s)

            # (3) Saturated: each probe must get a DELIVERED 503. Poll until
            # the accept loop has handed both held sockets to threads (they
            # hold for the 5s total deadline, so there is ample window), then
            # require a run of clean 503s.
            deadline = time.monotonic() + 3.0
            status, body = None, b""
            while time.monotonic() < deadline:
                status, body = _http_get(port)
                if status == 503:
                    break
                time.sleep(0.02)
            assert status == 503, (status, body)
            for i in range(3):
                status, body = _http_get(port)
                assert status == 503, (
                    f"probe {i} after the first shed got {status} not 503 "
                    f"(an RST would have raised before reaching this assert): "
                    f"{body!r}")
                assert b"overloaded" in body, body
        finally:
            for s in held:
                s.close()

        # (4) Slots return: the listener recovers.
        deadline = time.monotonic() + 5.0
        status, body = None, b""
        while time.monotonic() < deadline:
            status, body = _http_get(port)
            if status == 200:
                break
            time.sleep(0.05)
        assert status == 200, body

    @staticmethod
    def _socketpair():
        import socket

        left, right = socket.socketpair()
        left.settimeout(5)
        return left, right

    def test_reject_path_drains_the_unread_request_before_closing(self):
        """Round-2 review (a): the 503 is written and then the socket is
        closed while the request bytes are STILL QUEUED — the kernel answers
        that close with RST instead of FIN, an RST discards the client's
        receive buffer, and the 503 is LOST (the deploy lane saw "first probe
        503, every later probe ConnectionResetError").

        macOS did not reproduce the RST in this suite, so this asserts the
        MECHANISM deterministically with a socketpair: the request is in the
        rejected socket's receive queue, and after the reject the queue must be
        EMPTY (``MSG_DONTWAIT`` → ``BlockingIOError``), while the peer still
        reads an intact 503. Without the drain the queued request is still
        there and this fails.
        """
        left, right = self._socketpair()
        try:
            left.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n"
                         b"Connection: close\r\n\r\n")
            monitoring._HealthzServer._reject_overloaded(right)

            left.settimeout(2)
            head = left.recv(4096)
            assert head.startswith(b"HTTP/1.1 503"), head
            assert b"overloaded" in head, head

            right.settimeout(0)  # non-blocking probe of the receive queue
            with pytest.raises(BlockingIOError):
                right.recv(1)
        finally:
            left.close()
            right.close()

    def test_reject_overloaded_returns_without_waiting_for_the_client(self):
        """Round-3 review P1: the accept path must not WAIT on the rejected
        client. The old blocking ``_drain_unread(..., 0.2, ...)`` spent its full
        deadline once the queued request had been consumed (the peer stays
        open), costing ~0.2s of accept time per rejected connection — ~5
        conn/s, which lets an unauthenticated flood keep the 5-deep backlog
        full and starve the one endpoint that must never go dark.
        """
        import time

        left, right = self._socketpair()
        try:
            left.sendall(b"GET /healthz HTTP/1.1\r\nHost: x\r\n"
                         b"Connection: close\r\n\r\n")
            start = time.monotonic()
            monitoring._HealthzServer._reject_overloaded(right)
            elapsed = time.monotonic() - start
            # The 503 still reaches the client...
            left.settimeout(2)
            head = left.recv(4096)
            assert head.startswith(b"HTTP/1.1 503"), head
        finally:
            left.close()
            right.close()
        assert elapsed < 0.12, (
            f"reject cost {elapsed:.3f}s of accept time — the drain still waits")

    def test_nonblocking_drain_is_immediate_and_restores_blocking_mode(self):
        import time

        left, right = self._socketpair()
        try:
            left.sendall(b"x" * 4096)
            start = time.monotonic()
            monitoring._drain_unread_now(right, 8192)
            elapsed = time.monotonic() - start
            assert right.gettimeout() is None, "blocking mode was not restored"
            right.settimeout(0)
            with pytest.raises(BlockingIOError):
                right.recv(1)
        finally:
            left.close()
            right.close()
        assert elapsed < 0.12, f"non-blocking drain took {elapsed:.3f}s"

    def test_drain_unread_is_bounded_in_bytes_and_time(self):
        """Round-2 review: the drain must not itself become the slowloris it
        exists to prevent — bounded on BOTH axes (byte cap, total deadline)."""
        import time

        # Byte cap: 4096 queued, cap 1024 → at most 1024 consumed.
        left, right = self._socketpair()
        try:
            left.sendall(b"x" * 4096)
            monitoring._drain_unread(right, 1.0, 1024)
            right.settimeout(0)
            remaining = right.recv(65536)
            assert len(remaining) >= 4096 - 1024, (
                f"drain consumed more than the cap: {len(remaining)} left")
        finally:
            left.close()
            right.close()

        # Time bound: nothing queued and the peer stays open → the drain must
        # return at its own deadline, not block for the handler timeout.
        left, right = self._socketpair()
        try:
            start = time.monotonic()
            monitoring._drain_unread(right, 0.15, 8192)
            elapsed = time.monotonic() - start
        finally:
            left.close()
            right.close()
        assert 0.05 < elapsed < 1.0, f"drain took {elapsed:.2f}s"

    def test_a_trickling_client_is_cut_off_by_the_total_deadline(self, monkeypatch):
        """Round-2 review (b): ``timeout`` is PER-RECV, so a client sending one
        byte every few seconds never trips it and holds a handler thread
        FOREVER. With ``HEALTHZ_MAX_THREADS=8`` a trickle can pin every slot and
        make the liveness signal permanently dark — the worst outcome for the
        endpoint that must never go dark. The total deadline must close it."""
        import socket
        import time

        monkeypatch.setattr(monitoring._HealthzHandler, "total_timeout", 0.3)
        server = monitoring.start_health_listener(port=0, bind="127.0.0.1")
        monitoring.heartbeat_record()
        port = server.server_address[1]
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        start = time.monotonic()
        closed = False
        try:
            while time.monotonic() - start < 4.0:
                try:
                    s.sendall(b"G")  # one byte, never a complete request line
                except OSError:
                    closed = True
                    break
                time.sleep(0.05)
                try:
                    s.settimeout(0.05)
                    if s.recv(1) == b"":
                        closed = True
                        break
                except TimeoutError:
                    continue
                except OSError:
                    closed = True
                    break
        finally:
            s.close()
        elapsed = time.monotonic() - start
        assert closed, "the total deadline never closed a trickling connection"
        assert elapsed < 2.5, f"trickle held the handler for {elapsed:.2f}s"

    def test_default_port_env_typos_fall_back_instead_of_crashing_boot(self, monkeypatch):
        """#2850 review P2: `http`/`-1`/`70000` used to raise ValueError and
        OverflowError out of the UNGUARDED _start_liveness call and abort
        lifespan.startup() — a typo crash-looped the machine at boot."""
        for raw in ("http", "-1", "70000", "9090.5"):
            monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", raw)
            assert monitoring.resolve_healthz_target() == (
                monitoring.HEALTHZ_BIND, monitoring.HEALTHZ_PORT), raw
        # Valid values (including the ephemeral 0 tests rely on) are honoured.
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "0")
        assert monitoring.resolve_healthz_target()[1] == 0
        monkeypatch.setenv("TORTOISE_HEALTHZ_PORT", "8123")
        assert monitoring.resolve_healthz_target()[1] == 8123
        # An explicit out-of-range argument is also clamped, not raised.
        assert monitoring.resolve_healthz_target(port=99999)[1] == monitoring.HEALTHZ_PORT

    def test_non_oserror_bind_failure_is_still_contained(self, monkeypatch):
        """The widened except: OverflowError/ValueError from a bind must be
        logged and contained exactly like an OSError."""

        class _Boom(monitoring._HealthzServer):
            def __init__(self, *a, **k):
                raise OverflowError("port out of range")

        monkeypatch.setattr(monitoring, "_HealthzServer", _Boom)
        monkeypatch.delenv("TORTOISE_HEALTHZ_REQUIRED", raising=False)
        assert monitoring.start_health_listener(port=0, bind="127.0.0.1") is None

    def test_required_accepts_truthy_spellings(self, monkeypatch):
        """`required=true` must not silently do nothing (review P2)."""
        import socket

        for raw in ("1", "true", "TRUE", "yes", "on"):
            monkeypatch.setenv("TORTOISE_HEALTHZ_REQUIRED", raw)
            port = _free_port()
            squatter = socket.socket()
            squatter.bind(("0.0.0.0", port))
            squatter.listen(1)
            try:
                with pytest.raises(RuntimeError, match="could not bind"):
                    monitoring.start_health_listener(port=port, bind="0.0.0.0")
            finally:
                squatter.close()


class TestStallWatchdog:
    """#2850 item 5: exit a genuinely hung process so the platform restarts it."""

    def test_default_threshold_is_conservative_and_env_overridable(
            self, monkeypatch, caplog):
        """#2850 review P0/P2: the self-kill is OFF by default and the enabled
        floor is cross-validated against LOOP_STALE_AFTER."""
        import logging

        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        assert monitoring.LOOP_STALL_EXIT_S == 0.0
        assert monitoring._loop_stall_threshold() == 0.0

        # An enabled but absurdly small value is clamped UP: a sub-second
        # threshold turns a GC pause into a permanent crash loop, and a value
        # at/below LOOP_STALE_AFTER would kill the process before /healthz
        # could ever report. It must strictly exceed the report window.
        floor = max(monitoring.LOOP_STALL_EXIT_FLOOR_S,
                    monitoring.LOOP_STALE_AFTER * 2.0)
        assert floor > monitoring.LOOP_STALE_AFTER
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "0.5")
        assert monitoring._loop_stall_threshold() == floor
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "1")
        assert monitoring._loop_stall_threshold() == floor

        # A genuinely conservative value is honoured unchanged.
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "600")
        assert monitoring._loop_stall_threshold() == 600.0

        # 0 is the documented disable; negative is a typo — disable it too, but
        # LOUDLY (the pre-review code logged only at INFO and silently turned a
        # `-30` typo into "no guard" with no signal).
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "0")
        assert monitoring._loop_stall_threshold() == 0
        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "-30")
        with caplog.at_level(logging.WARNING, logger="tortoise.monitoring"):
            assert monitoring._loop_stall_threshold() == 0
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

        monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", "banana")
        assert monitoring._loop_stall_threshold() == 0

    def test_non_finite_threshold_disables_instead_of_arming(self, monkeypatch,
                                                             caplog):
        """Round-2 review P2: ``float()`` ACCEPTS nan/inf and neither is
        caught by the 0/negative guards (``nan == 0`` and ``nan < 0`` are both
        False). A nan threshold armed the killer while ``age <= nan`` stayed
        False forever, so a healthy ticking loop read as permanently stale and
        the process restart-looped every ~6s from boot — the round-1 P0 back
        through the validation door. Both forms must DISABLE, loudly, and must
        start no killer thread.
        """
        import logging

        for raw in ("nan", "NaN", "inf", "Infinity", "-inf"):
            caplog.clear()
            monkeypatch.setenv("TORTOISE_LOOP_STALL_EXIT_S", raw)
            with caplog.at_level(logging.ERROR, logger="tortoise.monitoring"):
                threshold = monitoring._loop_stall_threshold()
            assert threshold == 0.0, (raw, threshold)
            assert any(r.levelno >= logging.ERROR for r in caplog.records), raw
            assert monitoring.start_stall_watchdog(
                exit_fn=lambda *_: None) is None, (
                f"{raw!r} armed a killer thread")

    def test_unset_or_zero_threshold_starts_no_killer_thread(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        assert monitoring.start_stall_watchdog(exit_fn=lambda *_: None) is None
        assert monitoring.start_stall_watchdog(
            threshold_s=0, exit_fn=lambda *_: None) is None
        assert monitoring.start_stall_watchdog(
            threshold_s=-5, exit_fn=lambda *_: None) is None
        # Round-3 review P2: the PROGRAMMATIC API must reject a non-finite
        # threshold too. ``nan <= 0`` is False, so without the isfinite guard
        # this armed a killer whose ``age <= nan`` comparison is False forever.
        assert monitoring.start_stall_watchdog(
            threshold_s=float("nan"), exit_fn=lambda *_: None) is None
        assert monitoring.start_stall_watchdog(
            threshold_s=float("inf"), exit_fn=lambda *_: None) is None

    @staticmethod
    def _arm(fired, stop, *, threshold=0.2, **kwargs):
        """Arm a watchdog whose loop has demonstrably ticked twice and whose
        workload is idle (the two non-age preconditions, #2850 review P2)."""
        monitoring._reset_heartbeat()
        monitoring._reset_workload()
        monitoring.heartbeat_record()  # synthetic startup tick
        monitoring.heartbeat_record()  # a real loop_heartbeat_task tick
        kwargs.setdefault("poll_interval", 0.05)
        return monitoring.start_stall_watchdog(
            threshold, exit_fn=fired.append, stop_event=stop, **kwargs)

    def test_fires_when_the_loop_is_stale(self):
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        assert thread is not None
        try:
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1], "watchdog did not fire on a stale idle loop"

    def test_requires_consecutive_stale_windows(self):
        """#2850 review P2: a single stale sample must not exit the process."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        windows = 5
        thread = self._arm(fired, stop, consecutive_windows=windows,
                           poll_interval=0.08)
        try:
            time.sleep(0.25)  # ~3 windows in — not enough
            assert fired == [], "watchdog fired before the hysteresis window"
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1]

    def test_in_flight_workload_vetoes_the_kill(self):
        """#2850 review P0: the app legitimately blocks the loop for tens of
        seconds doing synchronous work. It is not a wedge, and the process must
        not be killed while a request is in flight.

        This is the exact production shape: one request blocks the loop past
        any heartbeat threshold while other tenants are waiting on that machine.
        """
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        monitoring.workload_enter()  # the blocking request is in flight
        try:
            time.sleep(0.6)  # ~3x the threshold, with the heartbeat frozen
            assert fired == [], (
                "watchdog killed the process while a request was in flight")
        finally:
            monitoring.workload_exit()
            stop.set()
            thread.join(timeout=2)

    def test_sync_on_loop_block_far_longer_than_threshold_is_not_killed_by_default(
            self, monkeypatch):
        """#2850 review P0 regression: the shipped default must survive the
        app's real (unbounded, synchronous) on-loop work.

        ``_capture_session_impl`` calls the LLM extractor synchronously with a
        60s HTTP timeout and can run thousands of synchronous DB round trips,
        so one free-tier request can freeze the loop far past any plausible
        threshold. Under the DEFAULT configuration the process must not die.
        """
        import asyncio
        import threading
        import time

        monkeypatch.delenv("TORTOISE_LOOP_STALL_EXIT_S", raising=False)
        fired: list = []

        # (a) The default does not even arm a killer.
        assert monitoring.start_stall_watchdog(exit_fn=fired.append) is None
        assert monitoring._loop_stall_threshold() == 0.0

        # (b) And if an operator DID arm one, in-flight work still vetoes it.
        stop = threading.Event()
        thread = self._arm(fired, stop, consecutive_windows=1)
        monitoring.workload_enter()
        try:
            async def _sync_block():
                time.sleep(0.7)  # synchronous: the event loop cannot tick

            asyncio.run(_sync_block())
            assert fired == [], "killed the process during legitimate on-loop work"
        finally:
            monitoring.workload_exit()
            stop.set()
            thread.join(timeout=2)

    def test_does_not_judge_before_a_real_heartbeat_tick(self):
        """#2850 review P2: the startup tick is synthetic. Until the real
        ``loop_heartbeat_task`` has ticked, a slow BOOT (unbounded synchronous
        DB work before the socket is bound) must not be mistaken for a wedge."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        monitoring._reset_heartbeat()
        monitoring._reset_workload()
        monitoring.heartbeat_record()  # ONLY the synthetic startup tick
        thread = monitoring.start_stall_watchdog(
            0.05, exit_fn=fired.append, stop_event=stop, poll_interval=0.02,
            consecutive_windows=1)
        try:
            time.sleep(0.4)
            assert fired == [], "watchdog judged a loop that never ticked"
            monitoring.heartbeat_record()  # the real task finally ticks
            deadline = time.monotonic() + 5.0
            while not fired and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [1], "watchdog never armed after a real tick"

    def test_does_not_fire_on_a_healthy_loop(self):
        """#2850 item 5 — the required negative: a ticking loop must survive."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()

        def _ticker() -> None:
            while not stop.is_set():
                monitoring.heartbeat_record()
                time.sleep(0.02)

        ticker = threading.Thread(target=_ticker, name="test-hb-ticker", daemon=True)
        ticker.start()
        thread = monitoring.start_stall_watchdog(
            0.4, exit_fn=fired.append, stop_event=stop, poll_interval=0.05)
        assert thread is not None
        try:
            time.sleep(1.2)  # ~3x the threshold, with the heartbeat kept fresh
        finally:
            stop.set()
            thread.join(timeout=2)
        assert fired == [], f"watchdog fired on a healthy loop: {fired}"

    def test_watchdog_stops_on_shutdown_not_on_a_stale_heartbeat(self):
        """A clean shutdown cancels the heartbeat task, which makes the
        heartbeat legitimately stale. ``stop_event`` is what keeps the
        watchdog from killing the process mid-drain."""
        import threading
        import time

        fired: list = []
        stop = threading.Event()
        monitoring._reset_heartbeat()
        thread = monitoring.start_stall_watchdog(
            0.3, exit_fn=fired.append, stop_event=stop, poll_interval=0.05)
        stop.set()
        time.sleep(1.0)
        assert fired == []
        assert not thread.is_alive()

    def test_destructive_threshold_floor_is_shared_with_the_env_path(self):
        """The programmatic floor reuses the env parser's minimum so the two
        cannot drift (round-4 review P2)."""
        assert monitoring._loop_stall_floor_s() == max(
            monitoring.LOOP_STALL_EXIT_FLOOR_S,
            monitoring.LOOP_STALE_AFTER * 2.0)

    def test_programmatic_subsecond_threshold_is_floored_on_the_destructive_path(
            self, monkeypatch):
        """Round-4 review P2: a FINITE but absurd programmatic threshold
        (``1e-9``) passed the isfinite/<=0 guard and armed the DESTRUCTIVE
        default. ``poll_interval = max(0.1, min(2.0, threshold/4))`` then fired
        ``os._exit`` after ~0.3s of a stale heartbeat — a GC pause becomes a
        crash loop. With ``exit_fn=None`` the threshold must be floored; an
        injected ``exit_fn`` (the test seam) keeps its exact value.
        """
        import threading
        import time

        monitoring._reset_heartbeat()
        monitoring._reset_workload()
        monitoring.heartbeat_record()  # synthetic startup tick
        monitoring.heartbeat_record()  # a real loop_heartbeat_task tick
        exited: list = []
        # Never let the destructive default actually kill pytest: the watchdog
        # reads ``os._exit`` off the module, so patch that seam (monkeypatch
        # restores it).
        monkeypatch.setattr(monitoring.os, "_exit",
                            lambda code=0: exited.append(code))
        stop = threading.Event()
        thread = monitoring.start_stall_watchdog(1e-9, stop_event=stop)
        assert thread is not None, "a finite positive threshold must still arm"
        try:
            time.sleep(0.8)
            assert exited == [], (
                "a 1e-9s threshold exited the process in under a second; the "
                "destructive default must be floored to the safe minimum")
        finally:
            stop.set()
            thread.join(timeout=2)


def test_oserror_branch_classification():
    """#1565 review: pin the OSError-branch classification — builtin
    TimeoutError is an OSError subclass but must NOT be retried (a hung DB
    stays hung); ConnectionRefusedError and socket.gaierror (DNS) ARE the
    transient connect class the retry targets (a startup DNS race)."""
    import socket

    from tortoise.monitoring import _is_transient_connect_error

    assert _is_transient_connect_error(ConnectionRefusedError()) is True
    assert _is_transient_connect_error(socket.gaierror()) is True
    assert _is_transient_connect_error(TimeoutError()) is False
    assert _is_transient_connect_error(TimeoutError()) is False
    assert _is_transient_connect_error(RuntimeError()) is False


def test_event_retention_interval_validation(monkeypatch):
    """Round-4 review P2 (PRE-EXISTING): the retention interval must be a
    POSITIVE whole number of seconds. ``0``/``-1`` make ``asyncio.sleep()``
    return immediately in the hosted retention loop and the SDK purge gate
    ``now - _EVENT_PURGE_LAST < interval`` always false (a DELETE on every
    ``events_poll``). A non-numeric value raised out of the SDK poll."""
    from tortoise.monitoring import (
        EVENT_RETENTION_INTERVAL_DEFAULT_S,
        event_retention_interval,
    )

    monkeypatch.delenv("TORTOISE_EVENT_RETENTION_INTERVAL", raising=False)
    assert event_retention_interval() == EVENT_RETENTION_INTERVAL_DEFAULT_S

    monkeypatch.setenv("TORTOISE_EVENT_RETENTION_INTERVAL", "900")
    assert event_retention_interval() == 900

    for raw in ("0", "-1", "-3600", "oops", "3.5", "", "   "):
        monkeypatch.setenv("TORTOISE_EVENT_RETENTION_INTERVAL", raw)
        assert event_retention_interval() == EVENT_RETENTION_INTERVAL_DEFAULT_S, raw
