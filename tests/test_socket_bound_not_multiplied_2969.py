"""#2969 — the configured socket read bound is the wait the caller actually gets.

The issue's headline defect (a socket read with NO bound at all) was fixed for
the eval lane by #3068 and for every raw product client by #8119/#3065. What
stayed measurable on ``origin/main`` is the BOUND's honesty: on the pinned
``redis 7.4.1`` a bare ``redis.Redis`` / ``falkordb.FalkorDB`` carries
``Retry(ExponentialWithJitterBackoff(), retries=3)`` as a FUNCTION-SIGNATURE
default, and ``TimeoutError`` is one of its supported errors. A stalled read is
therefore re-issued three more times, and the knob an operator sets is
multiplied by ~4 (plus backoff):

   measured on origin/main, one PING against a silent peer,
    TORTOISE_DB_SOCKET_TIMEOUT=1:   5.8-14.0s   (bound set: 1s, 4 connections)
    with the shared resolver's retry: ~2.0-2.2s  (2 x 1s + backoff, 2 conns)

That is the same amplification the owner recorded on #2969 as ``184s ~= 4 x
45s`` — "a caller cannot bound its own wait by setting socket_timeout". The
product's own bounds are not lowered here (the eval still gets its 120s read
budget, the product its 10s); only the invisible multiplier is pinned, to the
policy #3350 already installed on the embedded lane (ONE retry, so the worst
case is statically ``(1 + retries) * socket_timeout + backoff``).

No server is needed: a listening socket that accepts and never writes is the
instrument (#3065 established it; a genuinely wedged FalkorDB in CI would be a
flaky 20-minute job). The timing test below is the one that FAILS on
``origin/main`` — the rest are contract pins around it.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

#: The read bound used by the timing measurements. 1s keeps the test fast while
#: leaving the pre-fix (7.57s) and post-fix (~2.2s) readings far apart.
_READ_BOUND_S = 1.0
#: Upper limit on the total wait. Post-fix is `2 x bound + backoff` (~2.2s);
#: pre-fix is `4 x bound + backoff` (7.57s measured), so 4.0s separates them
#: with >1.7s of margin on both sides even on a loaded box.
_MAX_BOUNDED_WAIT_S = 4.0


def _silent_peer() -> tuple[socket.socket, list, int]:
    """A listening socket that accepts and then says nothing (#2969's shape)."""
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    held: list = []

    def accept_and_say_nothing():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            held.append(conn)  # kept open, never written to

    threading.Thread(target=accept_and_say_nothing, daemon=True).start()
    return server, held, server.getsockname()[1]


def _bounded_knobs(monkeypatch) -> None:
    monkeypatch.setenv("TORTOISE_DB_SOCKET_TIMEOUT", str(_READ_BOUND_S))
    monkeypatch.setenv("TORTOISE_DB_SOCKET_CONNECT_TIMEOUT", "2")


# ═══════════════════════════════════════════════════════════════════════════
# 1. The contract: one retry, so the bound is a knowable `2 x socket_timeout`
# ═══════════════════════════════════════════════════════════════════════════

def test_the_resolver_pins_the_retry_multiplier(monkeypatch):
    """The bound must not be multiplied by an invisible dependency default.

    One retry is the product policy #3350 established for the embedded lane:
    it keeps a single reconnect for a real transport blip while making the
    worst case computable from the knob. Nothing at a call site chose
    redis-py's count, and a version bump may change it (the #3350 lesson).
    """
    from redis.retry import Retry

    from tortoise.projection import _bounded_server_socket_kwargs

    retry = _bounded_server_socket_kwargs()["retry"]

    assert isinstance(retry, Retry), (
        "the resolved policy must be a redis Retry — a bare int/N ever reaches "
        "the vendor constructor as `retry=`")
    assert retry._retries == 1, (
        "the server lane must not inherit redis-py's default retry count: a "
        f"stalled read would wait (1 + {retry._retries}) x socket_timeout")
    # The policy is only meaningful if a read timeout is actually retried.
    # `TimeoutError` here is redis' (the class the socket layer raises), and
    # its absence would make the bound exact but the retry vacuous.
    assert any("Timeout" in e.__name__ for e in retry._supported_errors), (
        "the retry policy does not cover TimeoutError — then it is not the "
        "policy #3350 pinned and the multiplier claim above is untested")


def test_the_server_lane_and_the_embedded_lane_share_one_retry_policy():
    """ONE product retry policy, not two that can drift.

    The embedded lane's count is pinned by
    ``test_projection_embedded_socket_timeout.py``; this asserts the server
    lane resolves to the same number rather than a second copy.
    """
    from tortoise.projection import (
        _EMBEDDED_RETRY_COUNT,
        _bounded_server_socket_kwargs,
    )

    assert _bounded_server_socket_kwargs()["retry"]._retries == _EMBEDDED_RETRY_COUNT


# ═══════════════════════════════════════════════════════════════════════════
# 2. The measurement — FAILS on origin/main (7.57s for a 1s bound)
# ═══════════════════════════════════════════════════════════════════════════

def test_a_silent_peer_is_bounded_by_twice_the_read_bound(monkeypatch):
    """Wall clock on a real socket: the total wait, not just one attempt.

    ``redis.Redis`` is the layer that owns the retry loop (``falkordb`` passes
    ``retry=`` straight through to it), so timing it measures exactly what a
    product client that spreads ``_bounded_server_socket_kwargs()`` waits.

    Two independent assertions, because wall clock alone is load-sensitive:

    * **attempts** — the silent peer counts the connections the client opened.
      One retry means exactly TWO attempts; redis-py's default meant FOUR
      (measured on ``origin/main``: 4 connections, 10.40s). This is
      deterministic and independent of box load.
    * **wall clock** — the total wait stays under the 4.0s ceiling. Pre-fix
      behaviour that fails this test: the resolved kwargs carried no
      ``retry``, so the client used redis-py's default (3 retries) and the
      call below returned after 5.81s / 7.57s / 10.40s / 12.91s / 13.97s
      across measurements — every one above the ceiling. Post-fix it raises
      after ~2.0-2.2s.
    """
    import redis

    from tortoise.projection import _bounded_server_socket_kwargs

    _bounded_knobs(monkeypatch)
    server, held, port = _silent_peer()
    try:
        client = redis.Redis(host="127.0.0.1", port=port,
                             **_bounded_server_socket_kwargs())
        started = time.monotonic()
        with pytest.raises(redis.exceptions.TimeoutError):
            client.execute_command("PING")
        elapsed = time.monotonic() - started
        # Let the accept thread register the connections that were made; no
        # more can arrive once the call has raised and the client is closed.
        deadline = time.monotonic() + 2.0
        while len(held) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        attempts = len(held)
    finally:
        for conn in held:
            conn.close()
        server.close()

    assert attempts == 2, (
        f"one stalled read opened {attempts} connection(s) for a 1s bound — "
        "one retry means two attempts; redis-py's default opened 4 (#2969)")
    # Lower end: far below one bound means something other than the read timed
    # out (a connect failure or a wrong peer). Upper end: the multiplied wait
    # the dependency default produced.
    assert _READ_BOUND_S * 0.9 <= elapsed < _MAX_BOUNDED_WAIT_S, (
        f"one stalled read against a silent peer waited {elapsed:.2f}s for a "
        f"{_READ_BOUND_S:g}s bound — the retry policy is multiplying it "
        f"(expected < {_MAX_BOUNDED_WAIT_S:g}s, was 7.57s on origin/main)")


def test_the_vendor_client_accepts_the_resolved_policy(monkeypatch):
    """Wiring: ``FalkorDB.__init__`` takes ``retry=`` and the bound lands.

    ``falkordb`` hands ``socket_timeout`` and ``retry`` to ``redis.Redis``
    explicitly, so this construction is the same client the projection's host
    branch builds. Without the bound it would park in ``recv()`` forever;
    without a matching ``retry`` signature it would raise ``TypeError``.
    """
    import redis
    from falkordb import FalkorDB

    from tortoise.projection import _bounded_server_socket_kwargs

    _bounded_knobs(monkeypatch)
    server, held, port = _silent_peer()
    started = time.monotonic()
    try:
        with pytest.raises(redis.exceptions.TimeoutError):
            # The constructor's own health check is the first read.
            FalkorDB(host="127.0.0.1", port=port,
                     **_bounded_server_socket_kwargs())
        elapsed = time.monotonic() - started
    finally:
        for conn in held:
            conn.close()
        server.close()

    assert elapsed < _MAX_BOUNDED_WAIT_S, (
        f"the vendor constructor's first read waited {elapsed:.2f}s for a "
        f"{_READ_BOUND_S:g}s bound — the resolved policy did not reach it")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Bounded but still LOUD — the failure keeps its retry classification
# ═══════════════════════════════════════════════════════════════════════════

def test_the_bounded_failure_is_still_a_retryable_transport_timeout():
    """A bound must not become a swallow: #3068 routes this class to
    ``ingest:retries_exhausted`` / ``--retry-failed``.

    Pinning the multiplier must not tempt anyone to "fix" the hang by
    downgrading the error — the eval's existing retry channel has to keep
    seeing a transport transient.
    """
    import redis

    from tortoise.retry import retryable_transient

    exc = redis.exceptions.TimeoutError("Timeout reading from socket")
    assert retryable_transient(exc), (
        "the bounded read's TimeoutError must stay retryable-transient — "
        "#3068's ingest:retries_exhausted routing depends on it")
