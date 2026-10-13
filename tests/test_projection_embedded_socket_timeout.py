"""#3350 — the EMBEDDED DB lane's socket bound must be explicit and bounded.

``tortoise/projection`` wired ``_socket_timeouts()`` into the **host** branch
but not the embedded one, so the embedded client was built with no socket
timeout of OUR choosing. At that earlier pin the bound that actually applied
was redis-py's then-current connection default (believed 5s — a dependency-era
value, NOT a fact about the client this repo resolves today, where a bare
``redislite.Redis`` measures ``socket_timeout is None``), and — worse —
redis-py 8's client default retry policy is
``Retry(ExponentialWithJitterBackoff(), retries=10)``, which multiplies it.
Measured against a SIGSTOPped embedded daemon, one ``RETURN 1`` blocked for
**~59s** before raising.

That is the multiplier behind #3350: ``/health`` bounds its probe at
``monitoring.PROBE_TIMEOUT`` (1.5s), so the probe gives up long before the
query returns and the thread running it stays parked for the whole ~59s. On
``main`` the leak is capped at ONE thread (the shared ``_SingleSlotWorker``
from #2850), but it is parked for ~a minute per wedge, and
``TORTOISE_FALKORDB_SOCKET_TIMEOUT_S`` — the knob ``monitoring`` documents its
budgets against, and the one an operator lowers to tighten the loop-stall
ceiling — had NO effect on this lane at all.

#3421 closes the other half of the same divergence: the **connect** leg.
``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` had no effect on the embedded lane
either, because ``redis.Redis.__init__`` adds ``socket_connect_timeout`` in its
TCP branch ALONE — on the unix-socket branch redislite drives, the value never
reaches the pool kwargs and ``UnixDomainSocketConnection._connect`` ran
``sock.settimeout(None)`` (unbounded; NOT the "redis-py 5s default" an earlier
comment claimed). The connect bound is therefore delivered to the POOL, after
construction — see ``_apply_embedded_connect_timeout``.

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
import types
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise import monitoring  # noqa: E402, RUF100
from tortoise.projection import (  # noqa: E402, RUF100
    _DB_CONNECT_TIMEOUT_DEFAULT,
    _DB_SOCKET_TIMEOUT_DEFAULT,
    _EMBEDDED_RETRY_COUNT,
    FalkorProjection,
)

#: The socket timeout every timing test configures. Small enough to keep the
#: suite quick, large enough to dwarf scheduler jitter.
TIGHT_TIMEOUT_S = 0.5

#: Upper bound on a wedged query with the fix, given ``TIGHT_TIMEOUT_S``:
#: ``(1 + retries) * socket_timeout + backoff`` ≈ 2 * 0.5 + ≤1.0s. The
#: pre-fix shape (env ignored → the then-current implicit 5s default + 10
#: retries) measured ~59s, so
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

    ``socket_connect_timeout`` is deliberately NOT forwarded HERE: redis-py's
    ``Redis.__init__`` builds its ``ConnectionPool`` kwargs as an explicit dict
    and adds ``socket_connect_timeout`` in the TCP/SSL branch alone, so on the
    unix-socket branch this lane uses, the kwarg never reaches the pool
    (measured on the pinned redis 7.4.1). Passing it would wire a bound that
    does not exist. Asserted here so a later "add the pair for symmetry" edit
    has to face that fact — #3421 delivers it to the POOL instead
    (``test_embedded_pool_carries_the_connect_bound``).
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


# ── #3421: the CONNECT bound reaches the embedded pool ────────────────────


def test_embedded_pool_carries_the_connect_bound(monkeypatch):
    """#3421: ``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` must bound the embedded
    CONNECT leg, on the connection the lane actually uses.

    Pre-fix, measured on the pinned redis 7.4.1: the pool kwargs had no
    ``socket_connect_timeout`` at all and the connection ``get_connection()``
    hands out — the one redislite's construction PING created — carried
    ``socket_connect_timeout=None``, i.e. ``sock.settimeout(None)``: an
    UNBOUNDED connect that the operator's knob could not reach.

    Both halves are asserted because EITHER alone is the "bound that looks
    wired but is not" trap: the kwargs cover connections built later, the
    handed-out connection is the one in use. Read and connect are set to
    different values so a mix-up cannot pass.
    """
    monkeypatch.setenv("TORTOISE_FALKORDB_SOCKET_TIMEOUT_S", "0.5")
    monkeypatch.setenv("TORTOISE_FALKORDB_CONNECT_TIMEOUT_S", "0.75")
    proj = FalkorProjection(path=_tmp("connect3421.db"),
                            graph_name="test_connect_3421",
                            skip_health_check=True)
    try:
        pool = proj.db.client.connection_pool
        assert pool.connection_kwargs.get("socket_connect_timeout") == 0.75, (
            "the connect bound never reached the pool's kwargs, so any "
            "connection made after construction is unbounded (#3421)")
        conn = pool.get_connection()
        try:
            assert conn.socket_connect_timeout == 0.75, (
                "the connection the pool HANDS OUT still carries the pre-fix "
                "connect bound — the connection redislite's construction "
                "PING created was reused instead of dropped (#3421)")
            assert conn.socket_timeout == 0.5, (
                "the read bound regressed while wiring the connect one")
        finally:
            pool.release(conn)
        assert proj.g.query("RETURN 1").result_set == [[1]]
    finally:
        with contextlib.suppress(Exception):
            proj.close()


def test_embedded_connect_knob_is_fail_closed(monkeypatch):
    """#3421: this knob has NO ``none``/``off``/``0`` opt-out — it fails closed.

    Corrects an earlier claim (PR body + the seam test's docstring) that the
    embedded lane "keeps the documented ``none``/``off``/``0`` opt-out,
    delivered as ``None``". It does not, and must not: the DECLARED contract
    for ``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` is the opposite one
    (``.env.example``: "Non-positive/non-numeric values fall back to the
    default rather than disabling the bound", pinned at the resolution level
    by ``test_projection.py::test_socket_timeouts_reject_fail_open_values``).
    The ``none``/``off``/``0`` opt-out belongs to the PER-LANE
    ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT`` knob, which this lane does not
    consult for connect at all.

    Asserted on the POOL, not on ``_socket_timeouts()``: the resolution was
    already covered; what #3421 changed — and what was silently invisible
    before it — is whether the resolved value reaches this lane's connect leg.
    Pre-fix every spelling below left the pool WITHOUT a
    ``socket_connect_timeout`` key and the handed-out connection at ``None``,
    so an operator could not even see (let alone change) the bound the
    fail-closed contract promises here.
    """
    for spelling in ("none", "off", "0", "-1", "0.0", "banana"):
        monkeypatch.setenv("TORTOISE_FALKORDB_CONNECT_TIMEOUT_S", spelling)
        proj = FalkorProjection(path=_tmp(f"failclosed_{spelling.replace('.', '_')}.db"),
                                graph_name="test_failclosed_3421",
                                skip_health_check=True)
        try:
            pool = proj.db.client.connection_pool
            bound = pool.connection_kwargs.get("socket_connect_timeout")
            assert bound == _DB_CONNECT_TIMEOUT_DEFAULT, (
                f"TORTOISE_FALKORDB_CONNECT_TIMEOUT_S={spelling!r} did not "
                "resolve to the fail-closed default on the embedded lane's "
                "pool (#3421)")
            conn = pool.get_connection()
            try:
                assert conn.socket_connect_timeout == _DB_CONNECT_TIMEOUT_DEFAULT, (
                    f"{spelling!r} left the connection the pool HANDS OUT "
                    "unbounded — the knob is fail-closed, never an opt-out")
            finally:
                pool.release(conn)
        finally:
            with contextlib.suppress(Exception):
                proj.close()


def test_apply_embedded_connect_timeout_drops_pre_bound_connections():
    """The delivery seam's two writes, on a real (never-connected) pool.

    Daemon-free: the pool fixture holds a connection built WITHOUT the bound
    (what redislite's construction PING leaves behind) and this asserts the
    delivery replaces it rather than repairing it, because mutating
    ``connection_kwargs`` alone looks wired and is not — ``get_connection()``
    hands a pooled connection back before making a new one.

    Also pins that this helper is *not* a second resolution point: it writes
    ``connect_to`` verbatim, so a literal ``None`` argument stays ``None``
    instead of being coerced to the default. That is a property of the SEAM
    only — no spelling of ``TORTOISE_FALKORDB_CONNECT_TIMEOUT_S`` reaches it
    as ``None`` (``_socket_timeouts()`` maps every non-numeric/non-positive
    value to the default), and the fail-closed contract as it lands on the
    pool is pinned by ``test_embedded_connect_knob_is_fail_closed``.
    """
    from redis.connection import ConnectionPool, UnixDomainSocketConnection

    from tortoise.projection import _apply_embedded_connect_timeout

    pool = ConnectionPool(connection_class=UnixDomainSocketConnection,
                          path="/tmp/tortoise-3421.sock", socket_timeout=4.0)
    stale = pool.make_connection()  # stands in for redislite's construction PING
    # The PING is what leaves a connection IN the pool; `get_connection()`
    # cannot model it here because it also CONNECTS (redis 7.4.1), and this
    # socket deliberately does not exist. `make_connection` is the public
    # no-connect constructor; the pool's own bookkeeping is what it is missing.
    pool._available_connections.append(stale)
    assert stale.socket_connect_timeout is None, "pool fixture drifted"

    client = types.SimpleNamespace(connection_pool=pool)
    assert _apply_embedded_connect_timeout(client, 1.5) is True
    try:
        assert pool.connection_kwargs["socket_connect_timeout"] == 1.5
        assert pool._available_connections == [], (
            "the pre-bound connection is still pooled — the next command "
            "would reuse it and keep the unbounded connect leg for the life "
            "of the client (#3421)")
        assert pool.make_connection().socket_connect_timeout == 1.5, (
            "a connection built after the delivery is still unbounded")

        # Write-through, not resolution: the helper does not coerce `None` to
        # the default. No env spelling reaches it as `None` — the fail-closed
        # resolution is `_socket_timeouts()`'s job (see the docstring), and
        # `test_embedded_connect_knob_is_fail_closed` pins what lands on a
        # real pool.
        assert _apply_embedded_connect_timeout(client, None) is True
        assert pool.connection_kwargs["socket_connect_timeout"] is None
    finally:
        pool.disconnect()


def test_apply_embedded_connect_timeout_reports_an_absent_pool():
    """A duck-typed construction reports not-delivered instead of guessing.

    The daemon-backed test above is what makes this skip-only-in-tests rather
    than a production no-op: a real client always exposes a pool.
    """
    from tortoise.projection import _apply_embedded_connect_timeout

    assert _apply_embedded_connect_timeout(mock.MagicMock(), 1.5) is False


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
