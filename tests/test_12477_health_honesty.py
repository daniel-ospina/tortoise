"""#12477 — the health surfaces must not read green over a failing data plane.

Measured 2026-11-13 on `tortoise-y4mjjq` (build 43db5c0f5): every data request
either timed out at 30-45 s or was refused with the transport wait bound while

  * `GET /health` answered `status: "ok"`, `db.ok: true`;
  * `GET /healthz` (the Fly check) answered 200; and
  * `flyctl` reported `2/2 checks passing`.

Those are three separate defects and this module pins the fixes at the level
where they can regress silently: the app's own registration of the readers the
9090 listener reads, and the `/health` body's verdict.

What is asserted here is deliberately the WIRING, not a stub: `_start_liveness`
is called for real, and the failure is then injected by moving the probe's own
recorded result — the shape a stopped FalkorDB produces (#1381/#2850) — so a
reader that is written but never registered, or a registration that is dropped
by a refactor, turns this red.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

import pytest

from tortoise import hosted_api, monitoring


class _FakeState:
    pass


class _FakeApp:
    def __init__(self):
        self.state = _FakeState()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monitoring._reset_heartbeat()
    monitoring._reset_workload()
    monitoring.reset_wait_bound_breach_window()
    monitoring._reset_loop_blocks()
    monitoring.set_dataplane_verdict_source(None)
    monitoring.set_served_progress_source(None)
    monkeypatch.delenv("TORTOISE_LOOP_BLOCK_RECORD", raising=False)
    yield
    monitoring.stop_health_listener()
    monitoring.set_dataplane_verdict_source(None)
    monitoring.set_served_progress_source(None)
    monitoring.reset_wait_bound_breach_window()


def _get(port: int) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                    timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


_APPS: list = []


def _start(app) -> None:
    """Run the app's REAL liveness startup on a throwaway loop."""
    _APPS.append(app)

    async def _boot():
        hosted_api._start_liveness(app)
        await asyncio.sleep(0)

    asyncio.run(_boot())


@pytest.fixture(autouse=True)
def _stop_lifespans():
    """Stop what `_start_liveness` armed — a leaked sampler would poll forever.

    #12477 review P2: these tests run the REAL startup once per test, so without
    the matching shutdown every test leaks a sampler thread that keeps
    incrementing the process-global episode counters `test_monitoring.py` asserts
    exact values on. Green-by-ordering-luck is not green.
    """
    yield
    for app in _APPS:
        asyncio.run(hosted_api._stop_liveness(app))
    _APPS.clear()


def test_start_liveness_registers_both_readers():
    """A reader that is written but never registered is an inert instrument.

    This is the #4241 shape (the sweep that shipped with no caller): the 9090
    listener would keep reporting `unknown` forever while every piece of the
    fix existed. Registered here = the listener can see it.
    """
    app = _FakeApp()
    _start(app)
    assert monitoring.dataplane_verdict()["registered"] is True
    assert monitoring.served_progress()["registered"] is True


def test_the_fly_check_goes_red_through_the_real_wiring_when_the_db_fails():
    """Acceptance (2), end to end: inject the failure, watch the check fail.

    The loop is kept FRESH and a served-request counter reports recent progress,
    so neither the tick nor the serve term can be what turns this red — the only
    term left is the data plane, which is the defect the issue is about.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    held: list[tuple[float, dict]] = []

    def _tick():
        # a fresh tick, and a serve one second ago: loop + serve are healthy
        monitoring.heartbeat_record()
        hosted_api._LIVENESS_LAST_SERVED_AT = time.monotonic() - 1.0

    _tick()
    status, body = _get(port)
    assert status == 200, body
    held.append((status, body))

    # INJECT the data-plane failure: a fresh result that says not-ok.
    hosted_api._HEALTH_PROBE._result = {
        "ok": False, "observed": True, "latency_ms": 0.0,
        "error": "ConnectionError: DB is down"}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
    _tick()
    status, body = _get(port)
    assert status == 503, body
    assert body["reasons"] == ["data-plane-failed"]
    assert body["loop_stale"] is False
    assert body["served"]["last_served_age_s"] is not None

    # RECOVER: a fresh ok result clears it.
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 8.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
    _tick()
    status, body = _get(port)
    assert status == 200, body


def test_a_stale_probe_verdict_is_a_failure_not_a_fossil_ok():
    """A verdict that cannot be refreshed vouches for nothing (#10892's lesson).

    The refresher runs ON the loop. A 600 s wedge therefore stops the probe
    refreshing, and the coordinator's "stale but honest" last-known-good would
    otherwise keep reporting `ok` from before the outage — the fossil this whole
    issue is about.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic() - 600.0
    monitoring.heartbeat_record()
    status, body = _get(port)
    assert status == 503, body
    assert body["dataplane"]["detail"]["reason"] == "probe-result-stale"


def test_the_check_cannot_refresh_its_own_evidence():
    """`peek()`, not `snapshot()`: the check must not make its own proof fresh.

    ``snapshot()`` self-heals by starting a probe when the last result is stale.
    Called by the platform every 15 s, that would make a wedged loop read
    `healthy` precisely BECAUSE the check kept asking — the circularity #10892
    recorded as an OVERRIDES decision.
    """
    app = _FakeApp()
    _start(app)
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic() - 600.0
    before = hosted_api._HEALTH_PROBE.info()
    verdict = hosted_api._liveness_dataplane_verdict()
    after = hosted_api._HEALTH_PROBE.info()
    assert verdict["state"] == "failed"
    assert after["in_flight"] is False, (
        "the verdict reader started a probe — it must read, not refresh")
    assert after["result_age_s"] >= before["result_age_s"]


def test_the_served_reader_reports_the_liveness_counter():
    """#10892's instrument, read by the check (no second source of truth)."""
    app = _FakeApp()
    _start(app)
    hosted_api._LIVENESS_SERVED_COUNT = 17
    hosted_api._LIVENESS_LAST_SERVED_AT = time.monotonic() - 2.0
    progress = monitoring.served_progress()
    assert progress["registered"] is True
    assert progress["served"] == 17
    assert progress["last_served_age_s"] == pytest.approx(2.0, abs=0.5)


def test_health_reports_the_refusal_window_without_touching_status():
    """The reading that was missing on 2026-11-13 (#12477), on its own key.

    `wait_bound_breaches.total` reached 18 while `/health` answered
    `status: "ok"` — the fact was unreadable. It is now readable as `refusals`,
    and `status` is NOT derived from it: `#10185`/`#10620` recorded that a
    refusal counter must never pin a deployment degraded, and the pinned test
    `test_10602_wait_bound_breach_visible` asserts `status: "ok"` with breaches
    present. The verdict lives on the liveness listener, which is allowed to say
    "not now" where `/health` is not.
    """
    clean = asyncio.run(hosted_api.health())
    for _ in range(10):
        monitoring.record_wait_bound_attempt(monitoring.WAIT_BOUND_SURFACE_REST)
        monitoring.record_wait_bound_breach(monitoring.WAIT_BOUND_SURFACE_REST)
    body = asyncio.run(hosted_api.health())
    refusals = body["refusals"]
    assert refusals["total"] >= 10
    assert refusals["share"] == 1.0
    assert refusals["by_surface"]["rest"] >= 1
    assert refusals["refusing"] is True
    # ⛔ THE DECISION, PINNED: `status` is IDENTICAL before and after the window
    # goes red. Round 1's P0 (folding `refusing` into `status`) was re-introduced
    # on purpose and the tests that name the property stayed green, because the
    # only one that caught it did so through counter leakage from another file.
    # Comparing against the same box, un-breached, holds regardless of whether
    # this harness can reach `status: "ok"` at all (it cannot without a graph).
    assert body["status"] == clean["status"], (
        "a refusal window is reported, never folded into `status` "
        "(#10185/#10620); the verdict lives on the 9090 listener")
    assert "degraded_reasons" not in body
    # Its OWN key: `degraded_reasons` belongs to `record` (a documented
    # six-value vocabulary), and two schemas under one name is how a consumer
    # reads the wrong one.
    assert "degraded_reasons" not in body
    assert "loop_blocks" in body, (
        "/health must carry the wedge evidence channel (#12477)")


def test_a_historical_breach_does_not_appear_in_the_window():
    """The negative control: the window is a WINDOW (#10185's forever-red)."""
    monitoring.record_wait_bound_breach_recent(
        monitoring.WAIT_BOUND_SURFACE_REST,
        at=time.monotonic() - (monitoring.WAIT_BOUND_HEALTH_WINDOW_S + 30))
    body = asyncio.run(hosted_api.health())
    assert body["refusals"]["total"] == 0
    assert body["refusals"]["refusing"] is None, (
        "no traffic in the window is NOT DECIDED — never a silent False that "
        "reads as a clean measurement")
