"""#3498 — the synchronous control-plane/auth HTTP must not run on the loop.

``tortoise/supabase_control.py`` is synchronous end to end (0 ``async def``
across ~100 functions, an ``httpx.Client`` transport), so every async handler
that needs auth bridges to a blocking API. Before this change the bridge was
ad hoc — some sites wrapped a call in ``asyncio.to_thread``, most did not — and
the auth/DI seams ran their PostgREST round-trips directly in the coroutine.
One slow dependency call therefore held the event loop and delayed every other
request in the process, including ``/health`` (the #2850/#3060 de-registration
signature).

These tests are the design review's FALSIFIER (§F): they assert the seam calls
run OFF ``MainThread``, that the pool is genuinely multi-worker (the rejected
single-slot option would serialise auth to ~one request), and that a missed
bound / saturated backlog is fail-closed. Delete the offload and the site test
fails.

The structural pin lives in ``tests/test_health_ready_nonblocking.py``
(``test_control_plane_seam_calls_are_all_offloaded``).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import tortoise.hosted_api as ha
import tortoise.monitoring as monitoring
import tortoise.supabase_control as sc

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_offload_state():
    """Bounded record buffers are process-wide — isolate every test."""
    monitoring.reset_control_plane_records()
    yield
    monitoring.reset_control_plane_records()


class _StubTransport:
    """Records the THREAD NAME of every control-plane HTTP call.

    ``_record_client_call`` in ``supabase_control`` records the calling thread
    at the real HTTP choke point; this stub stands in for ``httpx.Client`` so a
    test can prove a *site* left the loop without a live PostgREST.
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.threads: list[str] = []

    def _reply(self):
        self.threads.append(threading.current_thread().name)

        class _Resp:
            status_code = 200
            content = b"[]"

            def __init__(self, rows):
                self._rows = rows

            def json(self):
                return self._rows

        return _Resp(self.rows)

    def get(self, url, **kwargs):
        return self._reply()

    def post(self, url, **kwargs):
        return self._reply()

    def patch(self, url, **kwargs):
        return self._reply()

    def delete(self, url, **kwargs):
        return self._reply()


def _stub_control_plane(monkeypatch, rows):
    cp = sc.SupabaseControlPlane(url="https://stub.supabase.co",
                                 service_key="svc-3498")
    transport = _StubTransport(rows)
    cp._http = transport
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return cp, transport


# ── the seam ────────────────────────────────────────────────────────────────


def test_control_plane_call_runs_off_main_thread():
    """The unit of the falsifier: an offloaded call is not on ``MainThread``."""
    thread_name = asyncio.run(
        monitoring.run_control_plane_call(
            lambda: threading.current_thread().name, op="probe")
    )
    assert thread_name != "MainThread"
    assert thread_name.startswith(monitoring.CONTROL_PLANE_WORKER_NAME)


def test_control_plane_pool_is_multi_worker():
    """A single slot would collapse auth concurrency to ~one request (the
    design review's rejected option); the declared pool is multi-worker."""
    assert monitoring.CONTROL_PLANE_WORKERS >= 2
    assert monitoring.control_plane_worker().workers == monitoring.CONTROL_PLANE_WORKERS


def test_control_plane_pool_runs_calls_concurrently():
    """Four calls must be in flight at once — impossible on one worker slot."""
    barrier = threading.Barrier(4, timeout=5)

    def _wait_at_barrier():
        barrier.wait()
        return threading.current_thread().name

    async def _run():
        return await asyncio.gather(*[
            monitoring.run_control_plane_call(_wait_at_barrier, op="concurrent")
            for _ in range(4)
        ])

    names = asyncio.run(_run())
    assert len(set(names)) >= 2, f"pool serialised four calls onto {names}"


def test_offload_records_op_and_duration():
    async def _run():
        return await monitoring.run_control_plane_call(lambda: "ok", op="unit")

    assert asyncio.run(_run()) == "ok"
    records = monitoring.control_plane_offload_records()
    assert records and records[-1][0] == "unit"
    assert records[-1][1] >= 0.0


def test_run_on_daemon_worker_honours_an_explicit_bound():
    """#3498: ``run_on_daemon_worker`` gains an OPTIONAL wait bound; ``None``
    (the default) keeps the historical unbounded probe/sweep await."""
    async def _run():
        await monitoring.run_on_daemon_worker(
            lambda: time.sleep(0.4), name="test-bounded-sweep", timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(_run())


# ── fail-closed error mapping ───────────────────────────────────────────────


def test_missed_wait_bound_raises_control_plane_offload_error(monkeypatch):
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        await monitoring.run_control_plane_call(
            lambda: time.sleep(0.4), op="slow")

    with pytest.raises(monitoring.ControlPlaneOffloadError):
        asyncio.run(_run())


def test_missed_wait_bound_maps_to_the_standard_503(monkeypatch):
    """The hosted seam converts the offload failure into the repo-standard
    ``control_plane_unavailable`` 503 — never a hang, never a raw 500."""
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        await ha._cp_offload(lambda: time.sleep(0.4), op="slow")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error_code"] == "control_plane_unavailable"


def test_best_effort_offload_swallows_a_missed_bound(monkeypatch):
    """#3498 review P1: telemetry (``update_last_used``, the analytics emit)
    must never gate the request path — an offload failure on a best-effort
    call is swallowed, not turned into a 503."""
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        return await ha._cp_offload(
            lambda: time.sleep(0.4), op="telemetry", best_effort=True)

    assert asyncio.run(_run()) is None


def test_best_effort_leaves_helper_errors_visible():
    """Only the OFFLOAD failure is swallowed — a domain exception from the
    helper (e.g. strict-mode ``UnregisteredTelemetryKey``) still propagates."""
    class _Boom(RuntimeError):
        pass

    def _raise_boom():
        raise _Boom()

    async def _run():
        await ha._cp_offload(_raise_boom, op="telemetry", best_effort=True)

    with pytest.raises(_Boom):
        asyncio.run(_run())


def test_saturated_backlog_fails_closed(monkeypatch):
    """A wedged pool fails fast instead of buffering without bound.

    ``max_backlog=1``: the worker takes the first submission, one more fills
    the queue, and the third must fail fast rather than queue without bound.
    """
    tiny = monitoring._SingleSlotWorker("test-cp-saturated", workers=1,
                                        max_backlog=1)
    monkeypatch.setattr(monitoring, "control_plane_worker", lambda: tiny)
    gate = threading.Event()

    async def _run():
        first = asyncio.ensure_future(
            monitoring.run_control_plane_call(gate.wait, op="hold-1"))
        await asyncio.sleep(0.05)  # let the single slot pick up `first`
        second = asyncio.ensure_future(
            monitoring.run_control_plane_call(gate.wait, op="hold-2"))
        await asyncio.sleep(0.05)  # `second` now fills the one-slot queue
        with pytest.raises(monitoring.ControlPlaneOffloadError):
            await monitoring.run_control_plane_call(lambda: None, op="full")
        gate.set()
        await asyncio.gather(first, second)

    asyncio.run(_run())


# ── the rerouted SITE (this fails without the fix) ──────────────────────────


def test_user_memberships_site_reads_off_main_thread(monkeypatch):
    """#3498 §A1 item 6: ``_user_memberships`` is an async session-lane seam.

    Without the offload, ``user_memberships(...)`` runs in the coroutine and
    the transport records ``MainThread`` — the exact bug. With it, every call
    is recorded from a pool thread.
    """
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "role": "owner"}])

    rows = asyncio.run(ha._user_memberships("user-1"))

    assert rows == [{"org_id": "org-1", "role": "owner"}]
    assert transport.threads, "the control-plane transport was never called"
    assert all(name != "MainThread" for name in transport.threads), (
        f"the session membership read ran on the event loop: {transport.threads}"
    )
    recorded = monitoring.control_plane_client_records()
    assert recorded and all(thread != "MainThread" for _dur, thread in recorded)


def test_membership_org_seam_reads_off_main_thread(monkeypatch):
    """The design's §B ``_membership_team`` seam (``_membership_org``) — it
    is reached by every membership-gated session endpoint."""
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "role": "owner"}])

    result = asyncio.run(ha._membership_org("user-1", "org-1"))

    assert result == {"org_id": "org-1", "role": "owner"}
    assert transport.threads and all(n != "MainThread" for n in transport.threads), (
        f"the membership seam ran on the event loop: {transport.threads}"
    )


def test_org_node_seam_reads_off_main_thread(monkeypatch):
    """``_org_node`` is the orgs-row seam reached by ~10 session endpoints
    (and by ``_require_owner_admin``'s suspension-stamp check)."""
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "tier": "pro"}])

    row = asyncio.run(ha._org_node("org-1"))
    assert row is not None and row["org_id"] == "org-1" and row["tier"] == "pro"
    assert transport.threads and all(n != "MainThread" for n in transport.threads), (
        f"the orgs-row seam ran on the event loop: {transport.threads}"
    )


# ── #3498 item 1: the loop-lag baseline ─────────────────────────────────────


def test_loop_lag_stats_empty_is_none_never_zero():
    monitoring.reset_loop_lag()
    stats = monitoring.loop_lag_stats()
    assert stats == {"samples": 0, "max_ms": None, "p99_ms": None, "mean_ms": None}


def test_loop_lag_stats_reports_max_and_p99():
    monitoring.reset_loop_lag()
    for lag in (0.001, 0.002, 0.003, 5.0):
        monitoring.record_loop_lag(lag)
    stats = monitoring.loop_lag_stats()
    assert stats["samples"] == 4
    assert stats["max_ms"] == 5000.0
    assert stats["p99_ms"] == 5000.0  # nearest-rank: the top value
    assert stats["mean_ms"] > 0


def test_loop_lag_clamps_negative_drift():
    monitoring.reset_loop_lag()
    monitoring.record_loop_lag(-1.0)
    assert monitoring.loop_lag_stats()["max_ms"] == 0.0


def test_heartbeat_info_exposes_the_loop_lag_baseline():
    monitoring.reset_loop_lag()
    monitoring.record_loop_lag(0.01)
    info = monitoring.loop_heartbeat_info()
    assert info["loop_lag_max_ms"] == pytest.approx(10.0, abs=0.5)
    assert info["loop_lag_samples"] == 1


def test_heartbeat_task_records_lag_each_tick():
    monitoring.reset_loop_lag()
    monitoring._reset_heartbeat()

    async def _run():
        task = asyncio.ensure_future(monitoring.loop_heartbeat_task(interval=0.01))
        await asyncio.sleep(0.06)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert monitoring.loop_lag_stats()["samples"] >= 1
    monitoring._reset_heartbeat()


# ── premise guard ───────────────────────────────────────────────────────────


def test_control_plane_module_is_still_synchronous_by_construction():
    """The seam exists because ``supabase_control`` is sync end-to-end. If a
    future change made the helpers async, the offload's premise (and this
    file's) would be wrong — fail loudly rather than keep a no-op seam."""
    import ast

    tree = ast.parse(Path(sc.__file__).read_text())
    async_defs = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert async_defs == [], (
        f"supabase_control gained async defs {async_defs} — revisit the #3498 "
        "offload seam (it assumes a synchronous control plane)"
    )
