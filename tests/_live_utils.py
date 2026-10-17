"""Live-FalkorDB test utilities (#942).

Plain module on purpose: a function defined in conftest.py is auto-collected
as a pytest FIXTURE and cannot be called directly. This module is not
auto-loaded, so _skip_unless_live_uri stays a plain function importable by
test_event_store.py and test_embedded_concurrency.py.
"""
from __future__ import annotations

import contextlib
import os

# #3339: the ONE live-URI skip reason. tools/skip-guard.py keys the
# intentional URI-gate family on this reason's PREFIX, and since #3592 the
# guard's verdict is LANE-AWARE: the family is exempt on a lane that declared
# itself URI-less (or declared nothing) and is a VIOLATION on a lane that
# promised the URI. Anything that needs to skip on a missing TORTOISE_DB_URI
# must still use this constant (directly or via _skip_unless_live_uri below) —
# a hand-written reason is how test_scan_eval_graph_live_smoke redded
# `test (a)` for every PR whose selection landed in the tier-2 URI-less lane.
# An UNDECLARED lane (the carve-out job, a local run) is the case where the
# prefix is still load-bearing, so routing through this constant remains
# required everywhere; the lane awareness only stops a reason from excusing a
# skip the lane had promised to run.
LIVE_URI_SKIP_REASON = (
    "requires TORTOISE_DB_URI (live FalkorDB sidecar; see CI job "
    "test-concurrency-falkor)"
)

# ── #6673: the docker-lane service ports are DYNAMIC (not 6379/16379) ──────
#
# The CI services lanes publish their falkordb containers with
# `docker run -p 127.0.0.1:0:6379` and read the assigned host port back, instead
# of the fixed `6379:6379` / `16379:6379` pair. WHY (#6673): every
# self-hosted runner on this host shares ONE Docker daemon, so a fixed host
# port is a single global namespace — when two services jobs overlapped, the
# second container's `docker start` died with "Bind for 0.0.0.0:6379 failed:
# port is already allocated", the RUNNER aborted in its
# `initialize_containers` pre-step, and the job reported a FALSE RED in ~9s
# with zero tests executed (measured: 6 executions across `test (b)`,
# `test-track-b`, `test-concurrency-falkor`). An ephemeral port cannot
# collide, so N runners can run services jobs concurrently.
#
# The provision step (`.github/actions/falkordb-provision`) writes the two
# assigned ports into $GITHUB_ENV. DEFAULTS ARE THE HISTORICAL LITERALS, so
# a local run, a URI-less run, and the GitHub-hosted lanes behave exactly as
# before — this seam is opt-in by env.
#
# This is a TEST-ONLY seam and deliberately NOT the product's
# FALKORDB_HOST/FALKORDB_PORT pair: those are read by
# tortoise/__main__.py::resolve_db_path (default 16379) to resolve a DB
# target when no URI is given, so overloading them would change what
# `tortoise doctor` etc. connect to. FALKORDB_PORT is honoured here only as
# the LEGACY fallback, which is where test_ingest.py / test_projection.py
# already read it from (their pre-#6673 behaviour, preserved).
# FALKORDB_HOST is not read at all — see service_host().
_PORT_ENV = "TORTOISE_TEST_DOCKER_PORT"
_LEGACY_PORT_ENV = "TORTOISE_TEST_LEGACY_PORT"
_HOST_ENV = "TORTOISE_TEST_DOCKER_HOST"


def docker_port() -> int:
    """The passworded docker-lane service's host port (default 6379)."""
    return int(os.environ.get(_PORT_ENV) or "6379")


def legacy_port() -> int:
    """The passwordless legacy service's host port (default 16379).

    Resolution order mirrors the pre-#6673 probes in test_ingest.py /
    test_projection.py. Read at CALL time so a monkeypatched FALKORDB_PORT
    keeps working.
    """
    return int(
        os.environ.get(_LEGACY_PORT_ENV)
        or os.environ.get("FALKORDB_PORT")
        or "16379"
    )


def service_host() -> str:
    """The host the published service ports are reachable on.

    The #6673 var wins; otherwise ``localhost`` — the historical literal every
    client in this lane was written against, and what the remaining
    ``SimpleNamespace(_host="localhost", …)`` fakes model.

    The product's ``FALKORDB_HOST`` is deliberately NOT consulted. Honouring it
    here made the probe follow the override while those clients did not: the
    probe would pass, the docker leg would be selected, and the client would
    dial a dead localhost — the split-brain this seam exists to remove, moved
    to the host axis. A test-only var cannot be half-threaded that way because
    nothing assumes its default.
    """
    return os.environ.get(_HOST_ENV) or "localhost"


# Import-time snapshots — for the module-level URI constants that were
# literals before #6673 (the workflow writes the env before pytest starts, so
# a module-level read is the same value a call-time read would return).
DOCKER_PORT = docker_port()
LEGACY_PORT = legacy_port()
SERVICE_HOST = service_host()


def docker_base_uri(*, password: str | None = "falkordb",
                    host: str | None = None) -> str:
    """The passworded docker-lane URI WITHOUT a graph path.

    The raw-client / `graph_name=`-argument sites spell the URI this way
    (`docker://:falkordb@localhost:6379` + a separate graph name), so the
    graph-less form needs its own accessor.

    `password` covers the three distinct STRING shapes the suite ships:
    `"falkordb"` → `docker://:falkordb@h:p` (the docker lane), `""` →
    `docker://:@h:p` (empty userinfo, the legacy spelling) and `None` → NO
    userinfo at all (`docker://h:p`, how test_integration_search and
    test_hnsw_vector_index spell their local fallback). These parse alike but
    are different strings, so a site that compares them keeps comparing like
    with like.
    """
    userinfo = "" if password is None else f":{password}@"
    return f"docker://{userinfo}{host or service_host()}:{docker_port()}"


def legacy_base_uri(*, password: str | None = "",
                    host: str | None = None) -> str:
    """The passwordless legacy-lane URI WITHOUT a graph path."""
    userinfo = "" if password is None else f":{password}@"
    return f"docker://{userinfo}{host or service_host()}:{legacy_port()}"


def docker_uri(graph: str, *, password: str | None = "falkordb",
               host: str | None = None) -> str:
    """A passworded docker-lane URI for `graph` at the live service port."""
    return f"{docker_base_uri(password=password, host=host)}/{graph}"


def legacy_uri(graph: str, *, password: str | None = "",
               host: str | None = None) -> str:
    """A passwordless legacy-lane URI for `graph` at the live service port."""
    return f"{legacy_base_uri(password=password, host=host)}/{graph}"


def tcp_reachable(port: int, *, host: str | None = None,
                  timeout: float = 1.0) -> bool:
    """Plain TCP-connect probe (the shape every live probe in the suite uses)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host or service_host(), port)) == 0
    except OSError:  # pragma: no cover - connect_ex returns, never raises
        return False
    finally:
        s.close()


def docker_reachable(*, timeout: float = 1.0) -> bool:
    """True when the provisioned passworded service accepts connections."""
    return tcp_reachable(docker_port(), timeout=timeout)


def legacy_reachable(*, timeout: float = 1.0) -> bool:
    """True when the provisioned passwordless legacy service accepts connections."""
    return tcp_reachable(legacy_port(), timeout=timeout)


def _skip_unless_live_uri():
    """Skip a docker:// live-FalkorDB test when no URI is configured (#942).

    Divergence from _skip_if_no_falkor (probe-based, test_projection.py):
    these tests REQUIRE the real server that CI's test-concurrency-falkor job
    provides (TORTOISE_DB_URI set); in every other surface they must skip
    VISIBLY (pytest.skip), never early-return-green — the vacuity pattern
    #942 exists to kill.
    """
    import pytest

    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip(LIVE_URI_SKIP_REASON)


@contextlib.contextmanager
def mask_embedder():
    """Install the no-embedder double over BOTH embedder seams; restore on exit.

    The ONE owner of the mask (#8057). Every docker-lane module in the assembly
    family pins the dense/vector legs OUT — the evidence path there is
    deterministic text, no model, no network — and used to hand-roll the same
    two ``monkeypatch.setattr`` calls in an autouse ``_no_embedder`` fixture.
    A FIXTURE CANNOT REACH A MODULE-LEVEL PROBE: the availability gate runs at
    module IMPORT, before any fixture, so the probe kept driving
    ``create_point`` with the REAL seam. That path derives an embedding
    (``encode_for_store`` -> ``EmbeddingModel.get``), and a cold
    sentence-transformers/torch load is measured at 50-205s (#6960) — paid at
    import, on a lane that only ever needed the FULLTEXT round-trip. Owning the
    mask in one place is the point: the probe and the fixtures cannot drift
    into disagreeing about which seams are pinned.

    Both seams, because either one alone still loads: ``encode_for_store`` calls
    ``compute_embedding`` (module global, the seam the write paths use) and
    ``_encode`` calls ``EmbeddingModel.get`` (the singleton loader). Both
    doubles absorb any signature (``*args, **kwargs``): the write paths call
    ``compute_embedding`` with exactly one positional argument and
    ``EmbeddingModel.get`` with none, but the warm-up leg passes
    ``load_timeout=`` — a narrow double that raised on the wider call would be
    swallowed by the write path's own ``except Exception`` and degrade to the
    silent no-vector path #4280 is about, i.e. the mask would introduce the
    failure it exists to avoid.

    Restores BOTH attributes in a ``finally``, so an exception in the masked
    block cannot leave the process pinned to the double. The saved values are
    read out of ``__dict__``, not via ``getattr``: ``EmbeddingModel.get`` is a
    ``classmethod``, so ``getattr`` hands back a NEW bound-method object on every
    access and restoring that would replace the descriptor with a plain method —
    a functional-looking but structurally degraded seam (subclass override and
    ``get.__func__`` both stop working) for every test after the mask.
    """
    from tortoise import embeddings as _emb

    _cls = _emb.EmbeddingModel
    _saved_compute = _emb.__dict__["compute_embedding"]
    _saved_get = _cls.__dict__["get"]
    _emb.compute_embedding = staticmethod(lambda *a, **k: None)
    _cls.get = staticmethod(lambda *a, **k: None)
    try:
        yield
    finally:
        _emb.compute_embedding = _saved_compute
        _cls.get = _saved_get
