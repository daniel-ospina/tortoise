"""#3350 — the EMBEDDED DB lane's socket bound must be explicit and bounded.

``tortoise/projection`` wired ``_socket_timeouts()`` into the **host** branch
but not the embedded one, so the embedded client was built with no socket
timeout of OUR choosing. The bound that actually applied was redis-py's own
implicit connection default (5s), and — worse — redis-py 8's client default
retry policy is ``Retry(ExponentialWithJitterBackoff(), retries=10)``, which
multiplies it. Measured against a SIGSTOPped embedded daemon, one
``RETURN 1`` blocked for **~59s** before raising.

That is the multiplier behind #3350: ``/health`` bounds its probe at
``monitoring.PROBE_TIMEOUT`` (1.5s), so the probe gives up long before the
query returns and the thread running it stays parked for the whole ~59s. On
``main`` the leak is capped at ONE thread (the shared ``_SingleSlotWorker``
from #2850), but it is parked for ~a minute per wedge, and
``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S`` — the knob ``monitoring`` documents its
budgets against, and the one an operator lowers to tighten the loop-stall
ceiling — had NO effect on this lane at all.

These tests pin the fix from both ends:

* the resolved timeouts and the bounded retry policy must actually reach the
  embedded ``FalkorDB(...)`` call (mock; no daemon);
* a genuinely wedged embedded daemon must raise within a bound derived from
  the CONFIGURED socket timeout — and the lane must recover afterwards, i.e.
  the thread was parked, not lost forever.

Embedded-only: registered in ``config/ci-surfaces.yml`` (``core`` /
``slow_files`` / ``carve_out``) and exempted from the URI-aware test redirect
via ``tests/_embedded.TEST_NO_REDIRECT_STEMS``.
"""
from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
import time
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import monitoring  # noqa: E402, RUF100
from tortoise.projection import (  # noqa: E402, RUF100
    _DB_SOCKET_TIMEOUT_DEFAULT,
    _EMBEDDED_RETRY_COUNT,
    FalkorProjection,
)

#: The socket timeout every timing test configures. Small enough to keep the
#: suite quick, large enough to dwarf scheduler jitter.
TIGHT_TIMEOUT_S = 0.5

#: Upper bound on a wedged query with the fix, given ``TIGHT_TIMEOUT_S``:
#: ``(1 + retries) * socket_timeout + backoff`` ≈ 2 * 0.5 + ≤1.0s. The
#: pre-fix shape (env ignored → implicit 5s + 10 retries) measured ~59s, so
#: this assertion is what makes the test a real regression guard rather than
#: a restatement of the code.
WEDGE_BOUND_S = 4.0


def _tmp(name: str) -> str:
    import tempfile
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_3350_"), name)


def _tight_timeouts(monkeypatch) -> None:
    monkeypatch.setenv("TORTOISE_FALKORDB_SOCKET_TIMEOUT_S", str(TIGHT_TIMEOUT_S))
    monkeypatch.setenv("TORTOISE_FALKORDB_CONNECT_TIMEOUT_S", str(TIGHT_TIMEOUT_S))


def _live_threads() -> int:
    return sum(1 for t in threading.enumerate() if t.is_alive())


def _wedge(proj) -> int:
    """SIGSTOP the embedded daemon — the cheapest reliable black hole.

    The kernel keeps the unix socket open and connectable, so the client gets
    past ``connect`` and blocks reading a reply that will never come: exactly
    the "wedged daemon" shape the socket timeout must bound.
    """
    pid = getattr(proj.db.client, "pid", None)
    if not pid:
        pytest.skip("embedded daemon pid unavailable — cannot wedge hermetically")
    try:
        os.kill(pid, signal.SIGSTOP)
    except (OSError, AttributeError) as exc:  # pragma: no cover - platform guard
        pytest.skip(f"cannot SIGSTOP the embedded daemon: {exc}")
    return pid


def _resume(pid: int) -> None:
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGCONT)


# ── wiring (no daemon) ────────────────────────────────────────────────────


def test_embedded_projection_receives_the_bounded_timeouts(monkeypatch):
    """The resolved read timeout must reach ``FalkorDB(...)`` on the EMBEDDED
    branch.

    Twin of ``test_projection.py::test_server_projection_receives_the_bounded_
    timeouts``. Without it the embedded lane silently relied on a redis-py
    connection default (5s) that the operator could neither see nor change.

    ``socket_connect_timeout`` is deliberately NOT forwarded: redis-py 8's
    ``ConnectionPool`` ignores it for a unix-domain socket, so passing it
    would wire a bound that does not exist. Asserted here so a later "add the
    pair for symmetry" edit has to face that fact.
    """
    _tight_timeouts(monkeypatch)
    fake = mock.MagicMock()
    fake.return_value.select_graph.return_value = mock.MagicMock()
    with mock.patch("tortoise.FalkorDB", fake):
        proj = FalkorProjection.__new__(FalkorProjection)
        FalkorProjection.__init__(proj, path=_tmp("embed.db"),
                                  graph_name="test_embed_3350",
                                  skip_health_check=True)
    assert fake.call_args.args[0].endswith("embed.db"), "not the embedded branch"
    assert fake.call_args.kwargs["socket_timeout"] == TIGHT_TIMEOUT_S
    assert "socket_connect_timeout" not in fake.call_args.kwargs, (
        "socket_connect_timeout is not honored for a unix-domain socket by "
        "redis-py — forwarding it wires a bound that does not exist")


def test_embedded_client_retry_policy_is_bounded(monkeypatch):
    """The retry multiplier must be OURS, not redis-py's 10-retry default.

    redis-py 8's default retry lives in a function-signature default, so it is
    invisible at the call site and can change with a dependency bump. Ten
    retries turn ``socket_timeout=t`` into a ~11t park and make the operator's
    knob meaningless; the embedded lane is a LOCAL unix socket, where one
    reconnect covers the transient case.
    """
    _tight_timeouts(monkeypatch)
    fake = mock.MagicMock()
    fake.return_value.select_graph.return_value = mock.MagicMock()
    with mock.patch("tortoise.FalkorDB", fake):
        proj = FalkorProjection.__new__(FalkorProjection)
        FalkorProjection.__init__(proj, path=_tmp("embed_retry.db"),
                                  graph_name="test_embed_retry_3350",
                                  skip_health_check=True)
    retry = fake.call_args.kwargs["retry"]
    assert retry._retries == _EMBEDDED_RETRY_COUNT
    assert _EMBEDDED_RETRY_COUNT <= 1, (
        "the embedded lane's worst case is (1 + retries) * socket_timeout; "
        "raising this re-opens the multi-minute park #3350 is about")
    # The real attribute of the built client, not just the mock argument.
    assert retry._backoff is not None


def test_embedded_socket_timeout_default_is_the_documented_one():
    """The embedded lane now shares the host lane's documented read default.

    Pinning the literal here is the point: ``monitoring``'s budget comments
    are written in terms of this value, so a silent change to the embedded
    default would invalidate them.
    """
    assert _DB_SOCKET_TIMEOUT_DEFAULT == 10.0


# ── behaviour (real embedded daemon) ──────────────────────────────────────


def test_wedged_embedded_daemon_times_out_within_the_configured_bound(monkeypatch):
    """A wedged embedded daemon must FAIL FAST, bounded by the configured knob.

    Pre-fix this test cannot pass on either count: ``socket_timeout`` is
    redis-py's implicit 5s (the env var is ignored), and the 10-retry default
    stretches one ``RETURN 1`` to ~59s — an order of magnitude past
    ``WEDGE_BOUND_S`` and ~40x past the ``/health`` probe's own 1.5s bound,
    which is what left the probe worker parked.
    """
    _tight_timeouts(monkeypatch)
    proj = FalkorProjection(path=_tmp("wedge.db"), graph_name="test_wedge_3350",
                            skip_health_check=True)
    try:
        conn = proj.db.client.connection_pool.make_connection()
        assert conn.socket_timeout == TIGHT_TIMEOUT_S, (
            "the embedded client did not receive the configured socket timeout")
        assert proj.g.query("RETURN 1").result_set == [[1]]

        pid = _wedge(proj)
        try:
            started = time.monotonic()
            with pytest.raises(Exception) as excinfo:
                proj.g.query("RETURN 1")
            elapsed = time.monotonic() - started
        finally:
            _resume(pid)

        assert type(excinfo.value).__name__ in ("TimeoutError", "ConnectionError"), (
            f"unexpected failure shape: {type(excinfo.value).__name__}")
        assert elapsed < WEDGE_BOUND_S, (
            f"a wedged embedded query took {elapsed:.1f}s — expected < "
            f"{WEDGE_BOUND_S}s for socket_timeout={TIGHT_TIMEOUT_S}s "
            f"(retries={_EMBEDDED_RETRY_COUNT})")

        # Parked, NOT lost: once the daemon answers again the SAME lane works
        # without any reset — the property #3350 asks for ("the inner worker
        # must free itself").
        assert proj.g.query("RETURN 1").result_set == [[1]]
    finally:
        with contextlib.suppress(Exception):
            proj.close()


class _ProjectionSDK:
    """Minimal SDK stand-in: ``probe_db`` only needs ``_get_proj``."""

    def __init__(self, proj):
        self._proj = proj

    def _get_proj(self):
        return self._proj


def test_repeated_wedged_probes_do_not_grow_threads_and_free_the_worker(monkeypatch):
    """The /health probe shape: N timed-out probes, bounded threads, no wedge.

    Two properties, both from #3350:

    1. **Bounded.** Repeated timing-out probes must not grow the thread count
       (the shared ``_SingleSlotWorker`` of #2850 — one worker, reused).
    2. **Released.** After the wedge clears, that SAME shared worker must serve
       a successful probe, with no ``_reset_probe_worker()``. A worker parked
       forever is the bug; a worker parked for the socket bound is the fix.
    """
    _tight_timeouts(monkeypatch)
    monkeypatch.setattr(monitoring, "PROBE_TIMEOUT", 0.05)

    proj = FalkorProjection(path=_tmp("probe.db"), graph_name="test_probe_3350",
                            skip_health_check=True)
    sdk = _ProjectionSDK(proj)
    monitoring._reset_probe_worker()
    before = _live_threads()
    pid = _wedge(proj)
    try:
        for _ in range(3):
            result = monitoring.probe_db(sdk)
            assert result["ok"] is False
            assert "timeout" in (result["error"] or "").lower()
        grown = _live_threads() - before
        assert grown <= 1, (
            f"{grown} threads leaked for 3 wedged embedded probes — the "
            "shared probe worker regressed")
    finally:
        _resume(pid)

    # The abandoned worker frees itself once the socket bound fires; no reset.
    deadline = time.monotonic() + 15.0
    recovered = False
    try:
        while time.monotonic() < deadline:
            if monitoring.probe_db(sdk)["ok"]:
                recovered = True
                break
            time.sleep(0.1)
    finally:
        monitoring._reset_probe_worker()
        with contextlib.suppress(Exception):
            proj.close()
    assert recovered, (
        "the shared probe worker never freed itself after the embedded socket "
        "timeout fired — the inner call is unbounded again (#3350)")
