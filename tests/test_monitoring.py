"""Tests for monitoring — health checks, Prometheus metrics, cost tracking."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tortoise import monitoring


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
        "1e-9",   # …and this reads as "valid" without the low clamp
        "300.001",  # above the clamp
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
        monkeypatch.setattr(mcp_server, "_get_team_sdk",
                            lambda: FakeSDK(db_ok=True, graph_size=3))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert captured["probe_setup_timeout"] == 7.0


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
    ``graph_size=0``: the onboarding gate lie. The platform liveness gate
    keeps the tight bound (a fast-degrade gate, #1384); the on-demand MCP
    health tool gets an explicit setup allowance.
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
        """#3143 review: the platform liveness surface that reaches the probe
        through ``metrics()`` (the standalone ``serve_health`` server) passes NO
        allowance, so the #1384 fast-degrade contract holds. The two surfaces
        that call ``probe_db`` DIRECTLY are pinned in their own files —
        selfhost in ``tests/test_selfhost.py``, hosted ``_probe_db`` in
        ``tests/test_hosted_api.py``. A refactor that had ``metrics()`` resolve
        the allowance itself (the natural 'make all callers benefit' change)
        would give that surface a multi-second cold-start; this pins the
        explicit ``setup_timeout=None`` it forwards AND the resulting degraded
        status."""
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
        import concurrent.futures
        submits = {"n": 0}
        real_executor = concurrent.futures.ThreadPoolExecutor

        class CountingExecutor:
            def __init__(self, *args, **kwargs):
                self._ex = real_executor(*args, **kwargs)

            def submit(self, *args, **kwargs):
                submits["n"] += 1
                return self._ex.submit(*args, **kwargs)

            def shutdown(self, *args, **kwargs):
                return self._ex.shutdown(*args, **kwargs)

        monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor",
                            CountingExecutor)

        class SetupHogSDK:
            def _get_proj(self):
                clock.t += 1.5  # overruns the whole shared budget
                proj = MagicMock()
                proj.g.query.return_value = MagicMock(result_set=[[1]])
                return proj

        result = monitoring.probe_db(SetupHogSDK())
        assert result["ok"] is False
        assert result["error"] == "probe timeout after 1.0s"
        assert submits["n"] == 1, "the query was submitted past the deadline"

    def test_serve_health_handler_forwards_no_allowance(self, monkeypatch):
        """#3143 review: the standalone ``serve_health`` server is the third
        platform liveness caller. Its handler calls ``metrics()`` with NO
        arguments — pin that end-to-end, because a handler-level
        ``probe_setup_timeout=probe_setup_timeout()`` would otherwise give the
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
            probe_setup_timeout=monitoring.PROBE_SETUP_TIMEOUT,
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
            mcp_server, "_get_team_sdk",
            lambda: SlowColdStartSDK(delay=0.2, graph_size=9019))
        token = _transport_mode.set("http")
        try:
            result = mcp_server.tortoise_health()
        finally:
            _transport_mode.reset(token)
        assert result["status"] == "ok", result
        assert result["db"]["ok"] is True
        assert result["graph_size"] == 9019


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
    tool says ok. That is the intended new contract, not an accident — the
    issue's "health and /health agree" indicator is satisfied on the *verdict
    about the graph* (reachable, real graph_size), not on the latency policy.
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
            sdk=sdk, probe_setup_timeout=monitoring.PROBE_SETUP_TIMEOUT)
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
