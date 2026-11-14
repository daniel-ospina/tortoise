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
    # #12477 review round 11: the two app-side stamps this module asserts on are
    # process-global, so a test that ran earlier in the same file must not decide
    # this one's verdict.
    hosted_api._LAST_PLANE_SERVED_AT = None
    hosted_api._LIVENESS_LAST_SERVED_AT = None
    hosted_api._HEALTH_PROBE_START_FAILED = False
    # a fresh process for every test: the never-produced arm reads uptime
    hosted_api._PROCESS_BOOT_MONOTONIC = time.monotonic()
    # ⛔ the harness's `asyncio.run(_boot())` CLOSES the loop, so the heartbeat
    # task `_start_liveness` installs is DONE by the time any assertion runs —
    # and `heartbeat_task_is_dead()` would then disarm the wedge term in every
    # test here (correctly: a ticker that cannot tick is not a wedge). Forgetting
    # it makes the tick arm testable again on the same terms the deployed app has
    # (a ticker that is still running), rather than masking a real regression.
    monitoring._reset_heartbeat_task()
    monkeypatch.delenv("TORTOISE_LOOP_BLOCK_RECORD", raising=False)
    yield
    monitoring.stop_health_listener()
    monitoring.set_dataplane_verdict_source(None)
    monitoring.set_served_progress_source(None)
    monitoring.reset_wait_bound_breach_window()
    hosted_api._LAST_PLANE_SERVED_AT = None
    hosted_api._HEALTH_PROBE_START_FAILED = False


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
    # ⛔ `asyncio.run` CLOSED that loop, so the heartbeat task `_start_liveness`
    # installed is DONE — and `heartbeat_task_is_dead()` would then disarm the
    # wedge term in every test here (correctly: a ticker that cannot tick is not
    # a wedge). Forgetting it makes `test_a_DEAD_ticker_is_not_a_wedge…` vacuous
    # AND makes the tick arm untestable; the deployed app's ticker is running, so
    # the harness models that by forgetting the closed-loop one.
    monitoring._reset_heartbeat_task()


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

    # this test OWNS its three inputs: another file in the same session may
    # have left a fresh not-ok probe result behind, which would red the FIRST
    # assertion for a reason that has nothing to do with the injection
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
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


def test_a_served_DATA_PLANE_request_is_what_keeps_a_stale_tick_green():
    """The serve veto must measure SERVING, not who polls `/liveness` (#12477 r11).

    Measured at the head this test was written against: the wedge term's
    "nothing SERVED" half read `_LIVENESS_LAST_SERVED_AT`, which ONLY the
    `/liveness` handler writes — so its age measured the poller, not the app. The
    only production poller is `.github/scripts/availability-watchdog.sh` on a
    `*/5` cron whose own header records ~15 min measured delivery, which is
    longer than `LOOP_STALE_AFTER` (90 s). Two consequences, both wrong:

    * the runbook's promise ("a busy loop that is still serving stays green")
      could not fire, because a busy loop serving data traffic never refreshed
      that stamp; and
    * one unauthenticated `GET /liveness` per 89 s held the veto permanently
      open — a client-drivable gate, which is the class #12477 exists for.

    So the term now reads the age of the last DATA-PLANE request this process
    ANSWERED (stamped at the same seam that decides the refusal share). This test
    pins both directions through the real listener.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    # a fresh DB verdict, so the plane term cannot be what decides this
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
    # a STALE tick, work waiting, and no `/liveness` poll for 5 minutes
    monitoring.heartbeat_record(
        at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
    hosted_api._LIVENESS_LAST_SERVED_AT = time.monotonic() - 300.0
    monitoring.workload_enter()
    try:
        # (a) a data-plane request was ANSWERED a second ago -> a busy box
        hosted_api._LAST_PLANE_SERVED_AT = time.monotonic() - 1.0
        status, body = _get(port)
        assert status == 200, body
        assert body["reasons"] == [], (
            "the app answered a data-plane request 1 s ago; the serve veto must "
            "have seen it, whatever the /liveness poll cadence is")
        assert body["loop_stale"] is True, "still reported, as a diagnostic"
        assert body["served"]["last_plane_serve_age_s"] == pytest.approx(1.0, abs=0.5)

        # (b) nothing served either -> the wedge term fires, naming the missing
        #     SERVE rather than only the missing tick
        hosted_api._LAST_PLANE_SERVED_AT = time.monotonic() - 300.0
        status, body = _get(port)
        assert status == 503, body
        assert body["reasons"] == ["loop-not-serving"]
    finally:
        monitoring.workload_exit()


def test_a_DEAD_ticker_is_not_a_wedge_on_the_deploy_gate(monkeypatch):
    """#2850's own ruling, applied to the ONE consumer that ignored it.

    `start_stall_watchdog` disarms the hard exit when `heartbeat_task_is_dead()`
    ("a dead ticker must not be mistaken for a wedge", monitoring.py), and the
    9090 verdict was the only reader that did not consult it. Measured: a ticker
    that died while the loop kept serving pinned `/healthz` at 503 for as long as
    no serve was stamped — i.e. permanently on the hosted box — which fails every
    deploy with no in-process recovery, because the restart path declines to act
    on a dead ticker BY DESIGN.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
    monkeypatch.setattr(monitoring, "heartbeat_task_is_dead", lambda: True)
    monitoring.heartbeat_record(
        at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
    monitoring.workload_enter()
    try:
        status, body = _get(port)
    finally:
        monitoring.workload_exit()
    assert status == 200, body
    assert body["loop_stale"] is True
    assert body["reasons"] == []


def test_a_fresh_LIVENESS_answer_is_serve_evidence_too():
    """The other datum, pinned on purpose (#12477 review round 12).

    A reviewer read `no_serve = not (fresh(plane) or fresh(liveness))` as
    fail-open because a `/liveness` poll could then hold the term green. It is
    not: the stamp is written by the HANDLER as it runs, so a fresh one proves
    the loop executed a coroutine — during a real wedge neither datum can be
    stamped, because neither handler runs. The states differ only in what they
    measure: the plane stamp moves on real traffic (which is what makes the
    runbook's "a busy loop stays green" promise true), the `/liveness` stamp moves
    only when something polls it.

    This pins the direction so a later change cannot turn the OR into a silent
    preference: with a stale plane age and a fresh `/liveness` age the box is
    SERVING, and the term must not claim otherwise.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    hosted_api._HEALTH_PROBE._result = {
        "ok": True, "observed": True, "latency_ms": 5.0, "error": None}
    hosted_api._HEALTH_PROBE._completed_at = time.monotonic()
    monitoring.heartbeat_record(
        at=time.monotonic() - (monitoring.LOOP_STALE_AFTER + 2.0))
    hosted_api._LAST_PLANE_SERVED_AT = time.monotonic() - 300.0
    hosted_api._LIVENESS_LAST_SERVED_AT = time.monotonic() - 1.0
    monitoring.workload_enter()
    try:
        status, body = _get(port)
    finally:
        monitoring.workload_exit()
    assert status == 200, body
    assert body["reasons"] == []
    assert body["loop_stale"] is True


def test_a_process_past_the_bound_with_NO_probe_result_is_a_FAILURE():
    """The third never-produced shape: nothing is running, and nothing ran.

    The start-failure flag covers a refresher whose `create_task` raised. It does
    NOT cover a lifespan that failed later, a task cancelled by a re-entered
    lifespan, or a probe that never got its turn — all of which report
    `in_flight: False, result_age_s: None`, the shape the `in_flight` arm cannot
    see. Measured: a reset coordinator beside a FRESH heartbeat answered 200 with
    `reasons: []` forever. Past the uptime bound that is a failure; inside it, it
    is still a cold start.
    """
    app = _FakeApp()
    _start(app)
    hosted_api._HEALTH_PROBE._result = None
    hosted_api._HEALTH_PROBE._completed_at = None
    hosted_api._HEALTH_PROBE_START_FAILED = False
    assert hosted_api._liveness_dataplane_verdict()["state"] == "unknown"
    # the assertion above is the COLD START. Age the process past the bound and
    # the same state must read as a failure, not as `unknown` for ever.
    hosted_api._PROCESS_BOOT_MONOTONIC = time.monotonic() - (
        hosted_api._health_probe_never_produced_bound_s() + 5.0)
    verdict = hosted_api._liveness_dataplane_verdict()
    assert verdict["state"] == "failed", verdict
    assert verdict["detail"]["reason"] == "probe-never-produced-a-result"
    assert verdict["detail"]["uptime_s"] > 0


def test_the_boot_clock_is_a_float_and_not_the_identity_string():
    """A name collision made a healthy cold start fail its own deploy gate.

    Round 12 added the uptime bound on a global called `_PROCESS_STARTED_AT` — the
    SAME name `_build_identity()` publishes as an ISO string 6,000 lines below,
    assigned after it at import. The float was therefore replaced by a string and
    every read raised `TypeError: unsupported operand type(s) for -: 'float' and
    'str'`, which the verdict reader reports as `failed`: measured on the real
    listener, a HEALTHY freshly-booted process answered
    `503 reasons=['data-plane-failed']`. The suites could not see it because the
    fixture hard-set the global to a float.

    Pinning the TYPE is what makes the collision unrepeatable: nothing here
    asserts the identity string's value, only that the two are different globals
    with different shapes.
    """
    assert isinstance(hosted_api._PROCESS_BOOT_MONOTONIC, float)
    assert isinstance(hosted_api._PROCESS_STARTED_AT, str), (
        "_PROCESS_STARTED_AT is the ISO string `_build_identity()` publishes; if "
        "it is ever a float, an uptime subtraction has been pointed at it again")


def test_a_cold_start_with_no_probe_result_does_not_fail_the_deploy_gate():
    """The reachable false red round 12 introduced, through the real listener.

    `_start_liveness` binds the 9090 listener BEFORE the refresher task is
    created, so a poll in that window sees `in_flight: False` and no result. That
    state must be `unknown` (green). It answered `503 data-plane-failed` because
    the uptime read raised, which is the direction that fails every deploy.
    """
    app = _FakeApp()
    _start(app)
    port = monitoring.start_health_listener(port=0, bind="127.0.0.1").server_address[1]
    hosted_api._HEALTH_PROBE.reset()
    hosted_api._HEALTH_PROBE_START_FAILED = False
    hosted_api._PROCESS_BOOT_MONOTONIC = time.monotonic()
    monitoring.heartbeat_record()
    status, body = _get(port)
    assert status == 200, body
    assert body["reasons"] == []
    assert body["dataplane"]["state"] == "unknown"
    assert body["dataplane"]["detail"]["reason"] == "no-probe-result-yet"


def test_the_refresher_latch_is_CLEARED_by_a_successful_re_arm(monkeypatch):
    """A transient start failure must not pin `data-plane-failed` for ever.

    `_lifespan` may legally run more than once in one process (in-process
    restart, `TestClient` reuse — `_start_liveness` documents re-entry as
    supported), so a latch set by one failed `create_task` and never cleared
    makes a HEALTHY re-entered process report `data-plane-failed` until the
    process is replaced, with no in-process recovery. Measured before the fix:
    nothing in production ever wrote the flag back to False.
    """
    app = _FakeApp()

    class _DeadLoop:
        def create_task(self, coro):
            coro.close()          # no "never awaited" warning from the fake
            raise RuntimeError("event loop is closed")

    hosted_api._HEALTH_PROBE_START_FAILED = False
    with monkeypatch.context() as patch:
        patch.setattr(hosted_api.asyncio, "get_event_loop", lambda: _DeadLoop())
        hosted_api._arm_health_probe_refresher(app)
    assert hosted_api._HEALTH_PROBE_START_FAILED is True
    assert hosted_api._liveness_dataplane_verdict()["detail"]["reason"] == \
        "probe-refresher-never-started"

    # now a real loop, i.e. the SECOND lifespan this process runs
    async def _arm_for_real():
        hosted_api._arm_health_probe_refresher(app)
        task = app.state._health_probe_task
        task.cancel()
        with __import__("contextlib").suppress(BaseException):
            await task

    asyncio.run(_arm_for_real())
    assert hosted_api._HEALTH_PROBE_START_FAILED is False, (
        "a SUCCESSFUL re-arm must clear the latch; otherwise one transient "
        "failure pins `data-plane-failed` for the life of the process")


def test_a_NEVER_started_probe_refresher_is_a_FAILURE_not_unknown():
    """One of the three terms must not be silently inert (#12477 r11).

    `fly.toml` and the runbook both say the term fires when the probe "has never
    produced a result at all past that window", but the code's never-produced arm
    was guarded on `in_flight` — so a process whose refresher task was never
    created (the startup `try/except` logs and continues) reported `unknown`, and
    `unknown` is GREEN on the listener, FOREVER. The flag is set where the failure
    happens, so this is evidence rather than a boot-timing heuristic.
    """
    app = _FakeApp()
    _start(app)
    hosted_api._HEALTH_PROBE._result = None
    hosted_api._HEALTH_PROBE._completed_at = None
    # the control: a boot that has not produced a result YET is not a failure
    assert hosted_api._liveness_dataplane_verdict()["state"] == "unknown"
    hosted_api._HEALTH_PROBE_START_FAILED = True
    verdict = hosted_api._liveness_dataplane_verdict()
    assert verdict["state"] == "failed", verdict
    assert verdict["detail"]["reason"] == "probe-refresher-never-started"


def test_health_reports_the_refusal_window_without_touching_status(monkeypatch):
    """The reading that was missing on 2026-11-13 (#12477), on its own key.

    `wait_bound_breaches.total` reached 18 while `/health` answered
    `status: "ok"` — the fact was unreadable. It is now readable as `refusals`,
    and `status` is NOT derived from it: `#10185`/`#10620` recorded that a
    refusal counter must never pin a deployment degraded, and the pinned test
    `test_10602_wait_bound_breach_visible` asserts `status: "ok"` with breaches
    present. The verdict lives on the liveness listener, which is allowed to say
    "not now" where `/health` is not.
    """
    # ⛔ PIN THE THREE TERMS `status` IS DERIVED FROM. They are ambient process
    # state (db probe, backup watcher, record), and in a shared pytest process
    # they can flip between the two readings for a reason that has nothing to do
    # with refusals — measured: this test failed once in a 10-file run with
    # `clean="ok"` and `body="degraded"` while passing in isolation AND in the
    # same run 2 minutes earlier. Pinning them is what makes the assertion about
    # the FOLD (#10185/#10620) instead of about the ambient state.
    hosting = {
        "_HEALTH_PROBE": type("P", (), {
            "snapshot": staticmethod(lambda: {"ok": True, "latency_ms": 1.0,
                                              "error": None, "observed": True})})(),
        "_backup_watcher_health": staticmethod(
            lambda: {"ok": True, "state": "running"}),
        "_record_health": staticmethod(
            lambda: {"ok": True, "degraded_reasons": None}),
    }
    for name, value in hosting.items():
        monkeypatch.setattr(hosted_api, name, value)
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


def test_health_answers_with_the_full_refusals_shape_when_the_window_RAISES(
        monkeypatch):
    """The degraded path must carry every key the healthy path carries.

    Round 5 measured this arm with no test: reverting it to an ad-hoc
    `{"error", "refusing"}` dict (2 of 13 keys) left the whole suite green, so a
    dashboard reading `refusals["share"]` — the field that makes this degraded
    case diagnosable — would `KeyError` on exactly the response where it matters.
    """
    def _boom(*_a, **_k):
        raise RuntimeError("simulated unreadable window")

    monkeypatch.setattr(monitoring, "wait_bound_breach_window", _boom)
    body = asyncio.run(hosted_api.health())
    healthy = monitoring._wait_bound_error("x", window=120.0)
    assert set(body["refusals"]) == set(healthy), (
        "the error arm must republish the resolver's full shape")
    assert body["refusals"]["refusing"] is None
    assert body["status"] in ("ok", "degraded"), (
        "/health must still ANSWER, and must not derive `status` from a "
        "degraded monitoring read")
    assert body["refusals"]["share"] is None


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
