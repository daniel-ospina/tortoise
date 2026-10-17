"""#2969 — the configured socket read bound is the wait the caller actually gets.

The issue's headline defect (a socket read with NO bound at all) was fixed for
the eval lane by #3068 and for every raw product client by #8119/#3065. What
stayed measurable is the BOUND's honesty, and the two vendor constructors the
product builds DISAGREE about it on the pinned ``redis 7.4.1``:

* a bare ``redis.Redis`` (the ACL admin client, ``acl_graph_users.py``)
  carries ``Retry(ExponentialWithJitterBackoff(), retries=3)`` as a
  FUNCTION-SIGNATURE default, and ``TimeoutError`` is one of its supported
  errors — so a stalled read is re-issued three more times and the knob an
  operator sets is multiplied by ~4 (plus backoff);
* ``falkordb.FalkorDB`` (the projection's host branch, ``session_indexer``,
  ``backup``'s BGSAVE, the ``doctor`` probe) declares ``retry=None`` and hands
  that to redis-py explicitly, which lands on ``Retry(NoBackoff(), 0)`` — its
  wait was already exactly ONE read bound.

Measured on the pinned stack against a silent peer (accepts, never writes),
``TORTOISE_DB_SOCKET_TIMEOUT=1``:

    redis.Redis, no retry pinned (origin/main)      4 attempts   11.5s
    redis.Redis, one retry (a wrong fix)            2 attempts    2.2s
    redis.Redis, retry=Retry(NoBackoff(), 0)        1 attempt     1.0s
    FalkorDB,   retry=None (origin/main)            1 attempt     1.0s
    FalkorDB,   one retry (a wrong fix)             2 attempts    2.3s
    FalkorDB,   retry=Retry(NoBackoff(), 0)         1 attempt     1.0s

So ``_bounded_server_socket_kwargs()`` — the ONE resolution point all five
product server-lane clients spread — pins ZERO retries: ``socket_timeout`` is
the wait the caller gets, on every site, which is exactly the property the
owner asked for on #2969 ("184s ~= 4 x 45s": a caller cannot bound its own wait
by setting socket_timeout). One retry is the WRONG fix here even though it
halves the raw client's 4x: it doubles the four ``FalkorDB`` clients' wait and
the eval lane's 120s read budget, on the clients that were already correct.

Reconnects are not removed from the system — they live in the layer that owns
the budget and knows whether the operation is idempotent (the eval's ingest
retry loop: ``retryable_transient`` -> ``ingest:retries_exhausted`` ->
``--retry-failed``, #3068). The EMBEDDED lane keeps its own single reconnect
(``_embedded_retry()``, #3350) because it talks to a local daemon this process
manages; ``test_the_half_open_retry_policy_of_the_embedded_lane_is_not_merged``
pins that the two lanes cannot silently converge again.

No server is needed: a listening socket that accepts and never writes is the
instrument (#3065 established it; a genuinely wedged FalkorDB in CI would be a
flaky 20-minute job). ``test_a_stalled_read_costs_the_product_one_read_bound``
is the measurement that FAILS on ``origin/main`` (4 attempts / 11.5s) and also
fails on the one-retry revision (2 attempts / 2.2s); the ``FalkorDB`` test is
its mirror and pins that the primary client does not GAIN an attempt.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

#: The read bound used by the timing measurements. 1s keeps the test fast while
#: leaving the three candidate policies far apart (4 attempts / 11.5s,
#: 2 attempts / 2.2s, 1 attempt / 1.0s).
_READ_BOUND_S = 1.0
#: Upper limit on the total wait. One attempt is ~1.0s, so 1.8s keeps ~0.8s of
#: slack ABOVE a single attempt (a loaded shard can only push the passing shape
#: up, never down). It is deliberately NOT symmetric: a two-attempt wait is
#: ~2.0-2.2s, leaving only 0.2-0.4s of clearance on the failing side, which is
#: why the ATTEMPT COUNT — not this ceiling — is the load-independent assertion
#: (`test_a_stalled_read_costs_the_product_one_read_bound` asserts both).
_MAX_BOUNDED_WAIT_S = 1.8


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


def _attempts_after(server, held, deadline_s: float = 2.0) -> int:
    """Wait until the peer's accept count settles; no more can arrive after the
    call raised, so the settle is a floor on wall clock, not a guess."""
    deadline = time.monotonic() + deadline_s
    time.sleep(0.05)
    while time.monotonic() < deadline:
        before = len(held)
        time.sleep(0.05)
        if len(held) == before:
            return before
    return len(held)


# ═══════════════════════════════════════════════════════════════════════════
# 1. The contract: ZERO retries, so the bound is the wait
# ═══════════════════════════════════════════════════════════════════════════

def test_the_resolver_pins_the_retry_multiplier_to_zero():
    """The bound must not be multiplied by an invisible dependency default.

    ``Retry(NoBackoff(), 0)`` is the ONE value that holds on both constructors:
    it keeps ``redis.Redis`` from inheriting its 3-retry signature default and
    it is what ``FalkorDB`` already resolved to (its own default is
    ``retry=None``). It also adds no backoff debt to the bound.
    """
    from redis.retry import Retry

    from tortoise.projection import _bounded_server_socket_kwargs

    retry = _bounded_server_socket_kwargs()["retry"]

    assert isinstance(retry, Retry), (
        "the resolved policy must be a redis Retry — a bare None/int never "
        "reaches the vendor constructor as an explicit `retry=`")
    assert retry._retries == 0, (
        "the server lane must not multiply the operator's socket bound: a "
        f"stalled read would wait (1 + {retry._retries}) x socket_timeout")
    assert type(retry._backoff).__name__ == "NoBackoff", (
        "a zero-retry policy must not carry a backoff either — the bound is "
        "the whole wait")


def test_the_half_open_retry_policy_of_the_embedded_lane_is_not_merged():
    """The two lanes are DIFFERENT on purpose, and must stay so.

    The embedded lane keeps #3350's single reconnect (it talks to a local
    daemon this process manages). The server lane pins zero (#2969), because
    its four ``FalkorDB`` clients never had a retry loop and one reconnect
    would double the primary client's stalled-read wait. A future "one policy
    for both" tidy-up is a regression, not a simplification.
    """
    from tortoise.projection import (
        _bounded_server_socket_kwargs,
        _embedded_retry,
    )

    assert _embedded_retry()._retries == 1, (
        "#3350's embedded reconnect was removed — that lane's wedged-daemon "
        "window is measured in tests/test_projection_embedded_socket_timeout.py")
    assert _bounded_server_socket_kwargs()["retry"]._retries == 0, (
        "converging the server lane onto the embedded lane's single retry "
        "doubles every host-lane client's wait and the eval lane's 120s read "
        "budget (#2969) — see _no_server_retry()")


# ═══════════════════════════════════════════════════════════════════════════
# 2. The measurements — the raw client FAILS pre-fix, the vendor client pins
#    that no attempt is ADDED
# ═══════════════════════════════════════════════════════════════════════════

def test_a_stalled_read_costs_the_product_one_read_bound(monkeypatch):
    """Wall clock AND attempt count on a real socket.

    ``redis.Redis`` is the site that carried the multiplier (the ACL admin
    client, ``acl_graph_users.py``), so timing it measures what that client
    waits. Two independent assertions, because wall clock alone is
    load-sensitive:

    * **attempts** — the silent peer counts the connections the client opened.
      One attempt is the policy; ``origin/main`` opened FOUR (redis-py's
      default), and a one-retry fix opened two. This is deterministic and
      independent of box load.
    * **wall clock** — the total wait stays under the 1.8s ceiling. Pre-fix
      behaviour that fails it: 11.5s measured on ``origin/main`` (4 attempts);
      2.2s on the one-retry revision.
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
        attempts = _attempts_after(server, held)
    finally:
        for conn in held:
            conn.close()
        server.close()

    assert attempts == 1, (
        f"one stalled read opened {attempts} connection(s) for a 1s bound — "
        "the socket bound must be the wait; redis-py's default opened 4 and a "
        "one-retry policy opened 2 (#2969)")
    # Lower end: far below one bound means something other than the read timed
    # out (a connect failure or a wrong peer). Upper end: any multiplier.
    assert _READ_BOUND_S * 0.9 <= elapsed < _MAX_BOUNDED_WAIT_S, (
        f"one stalled read against a silent peer waited {elapsed:.2f}s for a "
        f"{_READ_BOUND_S:g}s bound — something is multiplying it (expected "
        f"< {_MAX_BOUNDED_WAIT_S:g}s; origin/main was 11.5s)")


def test_the_vendor_client_does_not_gain_a_second_attempt(monkeypatch):
    """The four ``FalkorDB`` clients must stay at exactly one attempt.

    This is the half of #2969's property that was already true before the fix:
    ``FalkorDB.__init__`` defaults ``retry=None``, so it never multiplied the
    knob. It passes on ``origin/main`` — deliberately: its job is to red on the
    shape that BROKE the property, a policy that installs one retry here (2
    attempts / ~2.3s measured). It also proves the vendor constructor accepts
    the resolved ``retry=`` key and that the read bound reaches it.
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
        attempts = _attempts_after(server, held)
    finally:
        for conn in held:
            conn.close()
        server.close()

    assert attempts == 1, (
        f"the vendor client opened {attempts} connection(s) for a 1s bound — "
        "it had ZERO retries before #2969, so installing one DOUBLES the wait "
        "of the product's primary client (and the eval lane's 120s read "
        "budget) instead of bounding it")
    assert elapsed < _MAX_BOUNDED_WAIT_S, (
        f"the vendor constructor's first read waited {elapsed:.2f}s for a "
        f"{_READ_BOUND_S:g}s bound — the resolved policy did not reach it")


# ═══════════════════════════════════════════════════════════════════════════
# 3. Bounded but still LOUD — the failure keeps its retry classification
# ═══════════════════════════════════════════════════════════════════════════

def test_the_bounded_failure_is_still_a_retryable_transport_timeout():
    """A bound must not become a swallow: #3068 routes this class to
    ``ingest:retries_exhausted`` / ``--retry-failed``.

    Pinning the multiplier to zero moves the RECONNECT to that outer loop, so
    the error it consumes has to keep its transport-transient classification.
    """
    import redis

    from tortoise.retry import retryable_transient

    exc = redis.exceptions.TimeoutError("Timeout reading from socket")
    assert retryable_transient(exc), (
        "the bounded read's TimeoutError must stay retryable-transient — "
        "#3068's ingest:retries_exhausted routing depends on it")
