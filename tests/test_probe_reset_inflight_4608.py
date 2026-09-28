"""#4608 — a probe reset/rebuild must not close a handle an in-flight probe holds.

``hosted_api._probe_sdk_reset()`` and the selfhost mirror used to CLEAR the
cached probe SDK and immediately ``close()`` it. A probe fetches that handle,
releases the cache lock and then queries it (``probe_db``) with no lock held, so
a reset or a target change landing in that window pulled the connection out from
under a live query. The query failed and its ``{ok: False}`` was recorded as the
CURRENT generation — a spurious ``degraded`` for a reachable graph.

``HealthProbe``'s ``seq == self._seq`` check discards a SUPERSEDED worker's
write; it cannot stop the close from landing under a current-generation worker
that already holds the handle. ``hosted_api`` is the worse surface: its
``_HEALTH_PROBE`` and ``_READY_PROBE`` share ONE ``_PROBE_SDK_CACHE`` but keep
INDEPENDENT ``_seq`` counters, so a reset driven by either can close a handle
the other's in-flight worker is using with no seq protection at all.

The interleaving is made DETERMINISTIC with a gate on the query itself — the
probe signals "mid-query" and blocks until the test releases it. No sleeps, no
timing assumptions (this repo has a history of load-sensitive flakes here).
"""

from __future__ import annotations

import threading

import pytest

import tortoise.hosted_api as ha_mod
import tortoise.monitoring as mon
import tortoise.sdk as sdk_mod
import tortoise.selfhost as sh_mod


class _FakeConn:
    """One fake DB connection, with a gate that makes "mid-query" observable."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.closed = False
        self.gate = gate
        self.entered = threading.Event()

    def query(self, _query: str):
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(10), "the test gate was never released"
        if self.closed:
            # Exactly what a real closed FalkorDB connection does to an
            # in-flight query.
            raise RuntimeError("Connection closed")
        return [[1]]


class _FakeProj:
    def __init__(self, conn: _FakeConn) -> None:
        self.g = conn


class _FakeSDK:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def _get_proj(self) -> _FakeProj:
        return _FakeProj(self._conn)

    def close(self) -> None:
        self._conn.closed = True


class _FakeSDKFactory:
    """``_make_sdk`` / ``TortoiseSDK`` stand-in handing out pre-built conns."""

    def __init__(self, conns: list[_FakeConn]) -> None:
        self._conns = list(conns)
        self.built: list[_FakeSDK] = []

    def __call__(self, namespace=None) -> _FakeSDK:
        sdk = _FakeSDK(self._conns.pop(0))
        self.built.append(sdk)
        return sdk


def _fake_probe_db(sdk, setup_timeout=None) -> dict:
    """Stand-in for ``monitoring.probe_db`` on the cached handle.

    Mirrors the real function's never-raise contract: a query on a handle that
    was closed underneath it surfaces as ``{ok: False}`` — the verdict that used
    to be recorded as the CURRENT generation.
    """
    try:
        sdk._get_proj().g.query("RETURN 1")
    except Exception as exc:
        return {"ok": False, "latency_ms": 0.0,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"ok": True, "latency_ms": 0.1, "error": None}


@pytest.fixture
def probe_state():
    """Clean probe state for the two modules this file drives, before and after."""
    for mod in (ha_mod, sh_mod):
        mod._HEALTH_PROBE.reset()
        mod._probe_sdk_reset()
    ha_mod._READY_PROBE.reset()
    yield
    for mod in (ha_mod, sh_mod):
        mod._HEALTH_PROBE.reset()
        mod._probe_sdk_reset()
        assert getattr(mod, "_PROBE_SDK_EPISODES", 0) == 0, (
            "a probe episode leaked its count")
        assert getattr(mod, "_PROBE_SDK_DEFERRED", []) == [], (
            "a displaced handle was never closed")
    ha_mod._READY_PROBE.reset()


def _start_probe(probe) -> tuple[threading.Thread, dict]:
    """Run ``probe.wait()`` on a daemon thread; its verdict lands in the dict."""
    verdict: dict = {}

    def _run() -> None:
        verdict.update(probe.wait(timeout=10))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, verdict


def test_selfhost_reset_mid_query_keeps_ok(monkeypatch, probe_state):
    """``selfhost``: a reset while the probe is mid-query must not degrade it."""
    gated = _FakeConn(gate=threading.Event())
    factory = _FakeSDKFactory([gated, _FakeConn()])
    monkeypatch.setattr(sdk_mod, "TortoiseSDK", factory)
    monkeypatch.setattr(sh_mod, "_probe_sdk_key", lambda: ("pinned-selfhost",))
    monkeypatch.setattr(mon, "probe_db", _fake_probe_db)

    thread, verdict = _start_probe(sh_mod._HEALTH_PROBE)
    assert gated.entered.wait(5), "the probe never reached its query"

    sh_mod._probe_sdk_reset()          # the reset under test
    assert gated.closed is False, (
        "the reset closed a handle the in-flight probe is still querying")
    gated.gate.set()
    thread.join(10)
    assert not thread.is_alive()

    assert verdict["ok"] is True, verdict
    # The deferred close still happens once the probe releases the handle —
    # the fix defers the close, it does not drop it.
    assert gated.closed is True, "the displaced handle was never closed"


def test_hosted_health_reset_mid_query_keeps_ok(monkeypatch, probe_state):
    """``hosted_api`` / ``_HEALTH_PROBE``: same reset hazard, same verdict."""
    gated = _FakeConn(gate=threading.Event())
    factory = _FakeSDKFactory([gated, _FakeConn()])
    monkeypatch.setattr(ha_mod, "_make_sdk", factory)
    monkeypatch.setattr(ha_mod, "_probe_sdk_key", lambda: ("pinned-hosted",))
    monkeypatch.setattr(mon, "probe_db", _fake_probe_db)

    thread, verdict = _start_probe(ha_mod._HEALTH_PROBE)
    assert gated.entered.wait(5), "the probe never reached its query"

    ha_mod._probe_sdk_reset()
    assert gated.closed is False, (
        "the reset closed a handle the in-flight probe is still querying")
    gated.gate.set()
    thread.join(10)
    assert not thread.is_alive()

    assert verdict["ok"] is True, verdict
    assert gated.closed is True, "the displaced handle was never closed"


def test_hosted_ready_target_change_does_not_sink_health(monkeypatch, probe_state):
    """``hosted_api``: the UNPROTECTED pair — a rebuild via ``_READY_PROBE``.

    ``_HEALTH_PROBE`` and ``_READY_PROBE`` share one ``_PROBE_SDK_CACHE`` but
    keep independent ``_seq`` counters, so the readiness-driven rebuild used to
    close a handle the liveness worker was mid-query on — with no seq check on
    either side able to attribute the failure.
    """
    gated = _FakeConn(gate=threading.Event())
    factory = _FakeSDKFactory([gated, _FakeConn()])
    key = {"target": "a"}
    monkeypatch.setattr(ha_mod, "_make_sdk", factory)
    monkeypatch.setattr(ha_mod, "_probe_sdk_key", lambda: (key["target"],))
    monkeypatch.setattr(mon, "probe_db", _fake_probe_db)

    thread, health_verdict = _start_probe(ha_mod._HEALTH_PROBE)
    assert gated.entered.wait(5), "the liveness probe never reached its query"

    # A runtime target change, driven by the OTHER coordinator's probe.
    key["target"] = "b"
    ready_verdict = ha_mod._READY_PROBE.wait(timeout=10)

    assert ready_verdict["ok"] is True, ready_verdict
    assert gated.closed is False, (
        "the readiness-driven rebuild closed the handle the liveness probe "
        "is still querying")

    gated.gate.set()
    thread.join(10)
    assert not thread.is_alive()

    assert health_verdict["ok"] is True, health_verdict
    assert gated.closed is True, "the displaced handle was never closed"
