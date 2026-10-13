"""#7357 — a test session must never be pointed at the CANONICAL store.

THE FAILURE THIS GUARDS AGAINST (measured, four recurrences)
------------------------------------------------------------
The fleet's map is the single FalkorDB instance on loopback port ``16379``
(``docker://:@127.0.0.1:16379/tortoise``; IPv4-pinned per #6666, the
``resolve_db_path`` product default). A test suite pointed at THAT INSTANCE
does not merely read the map — it MINTS graphs on it. Measured 2026-10-04
(graph node ``1a105c5705f``, lane #6238): a 25-second ``MONITOR`` window on
the canonical store showed the ONLY writes in the instance belonged to a test
run, against ``test_mcp_*`` / ``test_registry_*`` graphs, with zero commands
against the map graph. The writer resolved to
``cd .worktrees/f5890 && TORTOISE_DB_URI='docker://:@127.0.0.1:16379/tortoise'
uv run python -m pytest tests/test_capture_session.py`` — a test suite with no
guard on the URI.

WHY MINTS ON THAT INSTANCE ARE NOT A NEUTRAL ACT
------------------------------------------------
FalkorDB's graph cache is bounded: ``GRAPH.CONFIG GET CACHE_SIZE`` = **25**
(the default). The canonical instance holds far more graphs than that, and
every additional one increases the eviction churn. When an eviction races an
in-flight query the engine aborts the statement with

    ResponseError: graph was deleted or replaced while the query was running, aborting

which is exactly the error #7357 is titled after, and which the issue body
attributes to a container restart. It is not: the map graph is INTACT and reads
always succeed (a fast read wins the race, a write loses it), and recurrences
were measured while the container was STABLE — ``RestartCount=0``, uptime 100
min, zero module reloads (graph nodes ``1a1014f95fc``, ``1a105c5705f``,
``1a10b6af842``; issue comments 2026-10-05T11:13Z and 2026-10-07T22:43Z, where
a plain ``docker restart`` did NOT clear the refusal). The fleet then loses its
write-back path for every lane at once.

RE-MEASURED 2026-10-13 (this guard's author, live): the instance holds **301**
graphs against ``CACHE_SIZE`` **25** — 12x over the cache. Four documented
recurrences (2026-10-02/03/04/05); the graph's own note records the durable fix
as the owner's call (resize the cache, or prune), and names the open gap this
module closes: *"NOTHING GUARDS a test suite from being pointed at the
canonical store."*

WHAT THIS MODULE DOES, AND WHAT IT DOES NOT
-------------------------------------------
It REFUSES the session (fail-closed) when ``TORTOISE_DB_URI`` targets the
canonical store, and names the reason. It does not resize the cache, prune
graphs, or touch the container — those are the owner's, and this guard is what
keeps the test suite from re-creating the pressure meanwhile.

The discriminator is the INSTANCE (loopback host + the canonical port), not the
graph name: the measured harm is minting graphs on that instance, whatever
graph a test names. ``from_uri`` resolves a portless loopback URI to 16379
(``tortoise/projection/__init__.py``), so a portless canonical target is
refused too. Every sanctioned test lane uses a different port — ``6379``
(``docker-compose.yml`` sidecar), the ``#6673`` ephemeral CI port, or this
repo's private ``tools/test_lane.py`` container (16390+) — so none of them is
affected. The escape hatch is explicit, in the style of
``TORTOISE_TEST_ALLOW_REMOTE``.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from tortoise.config import is_loopback_uri
from tortoise.env_truthy import is_truthy

#: The canonical/product FalkorDB port. NOT a test-lane port: it is the
#: `resolve_db_path` default (tortoise/__main__.py) and the IPv4-pinned
#: canonical address the issue records. The test lanes live on 6379, the
#: #6673 ephemeral port, or tools/test_lane.py's 16390+ range.
CANONICAL_DB_PORT = 16379

#: Explicit opt-in to run against the canonical store anyway — the escape hatch
#: an operator reaching for the map deliberately needs. Read at CALL time so a
#: test can exercise both directions.
ALLOW_CANONICAL_ENV = "TORTOISE_TEST_ALLOW_CANONICAL"

#: The port `FalkorProjection.from_uri` resolves when the URI omits one — so a
#: portless loopback URI IS a canonical target, not an ambiguous one.
_URI_DEFAULT_PORT = 16379


def _canonical_target(uri: str | None) -> str | None:
    """``'host:port'`` when *uri* addresses the canonical store, else ``None``.

    Returns only the credential-free projection (`host:port`): the raw URI can
    carry a password, and pytest renders a failure's locals.
    """
    if not uri or not is_loopback_uri(uri):
        return None
    parsed = urlparse(uri)
    try:
        port = parsed.port
    except ValueError:
        # A non-numeric port never resolves to the canonical store; the
        # malformed-port diagnosis belongs to the #6073 probe, not here.
        return None
    if (port or _URI_DEFAULT_PORT) != CANONICAL_DB_PORT:
        return None
    return f"{parsed.hostname or 'localhost'}:{CANONICAL_DB_PORT}"


def is_canonical_store_uri(uri: str | None) -> bool:
    """True when *uri* targets the canonical (map) FalkorDB instance.

    Loopback only, deliberately: a REMOTE host on this port number is a
    different machine's database, and refusing it here would punish a
    deliberate ``TORTOISE_TEST_ALLOW_REMOTE=1`` session for an unrelated
    coincidence.
    """
    return _canonical_target(uri) is not None


def canonical_store_refusal(uri: str | None) -> str | None:
    """The fail-closed session message, or ``None`` when nothing is refused.

    ``None`` for every non-canonical target (the embedded/carve-out lane, the
    sanctioned test lanes) and for an explicit ``TORTOISE_TEST_ALLOW_CANONICAL``
    opt-in. The message never echoes the URI — only its ``host:port``.
    """
    target = _canonical_target(uri)
    if target is None:
        return None
    if is_truthy(os.environ.get(ALLOW_CANONICAL_ENV)):
        return None
    return (
        f"TORTOISE_DB_URI targets the CANONICAL Tortoise store ({target}) — the "
        f"fleet's only map instance. Running the test suite against it MINTS "
        f"test/org graphs on that instance, and each additional graph increases "
        f"this instance's cache eviction churn: measured 2026-10-13 it holds 301 "
        f"graphs against FalkorDB's default CACHE_SIZE=25, and an eviction racing "
        f"an in-flight query aborts EVERY fleet write with 'graph was deleted or "
        f"replaced while the query was running, aborting' — the failure this "
        f"guard exists to prevent (#7357; recurrences 2026-10-02/03/04/05). The "
        f"map data is intact; the fleet's write-back path is not. THIS IS NOT "
        f"YOUR DIFF. Point the run at a test store instead:\n"
        f"  TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise_test_matrix\n"
        f'  uri="$(uv run python tools/test_lane.py uri)"   # private container\n'
        f"If you deliberately mean to run against the canonical store, set "
        f"{ALLOW_CANONICAL_ENV}=1. The URI is not echoed here because it may "
        f"carry a credential."
    )
