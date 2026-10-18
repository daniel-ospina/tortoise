"""FalkorDB connection configuration for the graph-viz server (#1079).

Extracted from main.py so the env→kwargs contract is unit-testable without
triggering the import-time connection. Mirrors the tortoise SDK's
``FalkorProjection.from_uri`` semantics: ``username``/``password`` for
FalkorDB Cloud and ACL-auth self-hosted instances, ``ssl`` for rediss-style
TLS endpoints. All defaults preserve the original password-less local dev
behavior (docker compose up -d → localhost:16379).
"""
from __future__ import annotations

import os

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 16379
DEFAULT_GRAPH = "tortoise"

#: The BOUND the client must carry when the product package is not importable.
#:
#: This server is a STANDALONE deployable (its own `requirements.txt` carries no
#: ``tortoise``), so the product resolver cannot be a hard import. The fallback is
#: therefore pinned EQUAL to the product defaults by
#: ``tests/test_bounded_raw_clients_3065.py::test_graph_viz_fallback_matches_the_product_defaults``
#: — a change on either side without the other is a red test, not silent drift
#: (which is the defect #8118 exists to kill: every site re-deriving its own
#: bounds, or deriving none).
FALLBACK_SOCKET_CONNECT_TIMEOUT = 2.0
FALLBACK_SOCKET_TIMEOUT = 10.0


def _bounded_socket_kwargs() -> dict:
    """The socket bounds a client built from these kwargs MUST carry (#8118).

    ``falkordb.FalkorDB.__init__`` defaults ``socket_timeout=None`` and passes it
    to redis-py explicitly, which OVERRIDES redis-py's own default — so a client
    built without this pair is not "bounded by default", it is **unbounded**. A
    server-side stall (the #2969 active-defrag loop, #2838's MERGE lock) then parks
    the read in ``recv()`` forever: alive, 0% CPU, no error, no log. On THIS path
    that is a user-facing boot that never finishes — ``main.py`` retries the
    constructor 30 times, and every one of those attempts can hang the same way.

    The pair comes from the product's ONE resolver (``_bounded_server_socket_kwargs``)
    so the operator knobs ``TORTOISE_DB_SOCKET_TIMEOUT`` /
    ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT`` govern the viz server too. Only the
    SOCKET half is taken: ``FalkorDB`` already declares ``retry=None`` (one
    attempt, so the bound IS the wait) and the fallback path cannot supply a
    redis retry object without importing redis here.
    """
    try:
        from tortoise.projection import _bounded_server_socket_kwargs
    except ImportError:
        # The product package is not importable in THIS deployment (standalone
        # requirements.txt, or a missing optional dep). Boot must not depend on
        # it, and the fallback is still a BOUND — the failure direction is
        # bounded, not unbounded. Deliberately NOT a bare `except`: an
        # installed-but-broken package raises something else, and that should
        # surface rather than be laundered into a silent policy switch.
        return {
            "socket_connect_timeout": FALLBACK_SOCKET_CONNECT_TIMEOUT,
            "socket_timeout": FALLBACK_SOCKET_TIMEOUT,
        }
    return {k: v for k, v in _bounded_server_socket_kwargs().items()
            if k != "retry"}


def build_connection_kwargs() -> dict:
    """Build the FalkorDB client kwargs from env vars.

    Returns a dict suitable for ``FalkorDB(**kwargs)``. Only connection
    params are included; the graph name is returned separately via
    :func:`graph_name` (it selects the graph, not the connection).

    The socket bounds (#8118) live HERE rather than at the ``FalkorDB(**kwargs)``
    call in ``main.py`` because this builder is the seam with a unit test — the
    bound is pinned where it can be observed without booting the connection.
    """
    host = os.environ.get("FALKORDB_HOST", DEFAULT_HOST)
    port = int(os.environ.get("FALKORDB_PORT", str(DEFAULT_PORT)))
    username = os.environ.get("FALKORDB_USERNAME") or None
    password = os.environ.get("FALKORDB_PASSWORD") or None
    ssl = os.environ.get("FALKORDB_SSL", "").strip().lower() in (
        "1", "true", "yes", "on")

    kwargs: dict = {"host": host, "port": port}
    if username is not None:
        kwargs["username"] = username
    if password is not None:
        kwargs["password"] = password
    if ssl:
        kwargs["ssl"] = True
    kwargs.update(_bounded_socket_kwargs())
    return kwargs


def graph_name() -> str:
    """The graph to select (FALKORDB_GRAPH, default 'tortoise')."""
    return os.environ.get("FALKORDB_GRAPH", DEFAULT_GRAPH)
