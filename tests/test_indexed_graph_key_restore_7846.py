"""#7846 — a graph-key RESTORE of a VECTOR-INDEXED graph: bounded, non-fatal, and index-preserving.

THE CLAIM (issue #7846, P0). A plain-Redis ``DUMP`` → ``DEL`` → ``RESTORE`` of a
graph that carries a vector index *permanently wedges* the FalkorDB instance:
the server stays up, stops answering even ``PING``, burns 0 % CPU, and never
recovers — while the same graph without an index restores in 0.38 s. It was
filed as ship-blocking because a RAM-bound graph can be evicted only by dumping
it, and with an in-graph vector index the *restore* step was reported to be the
step that takes the whole instance down.

WHAT WAS MEASURED (2026-10-12; full evidence and raw logs in
``docs/evidence/7846-indexed-graph-restore/``). The **permanent** wedge does not
reproduce, on any image pullable today, at the issue's own scale:

| engine (graph module) | nodes | DUMP | RESTORE | instance after |
|---|---|---|---|---|
| ``falkordb/falkordb:6.0.1`` (60001) | 3 000 | 1.44 MB | 0.42 s | serving |
| ``falkordb/falkordb:6.0.1`` (60001) | 30 000 | 14.42 MB | 2.62 s | serving |
| ``falkordb/falkordb:6.0.1`` (60001) | 90 000 | 43.19 MB | 53.3 s (client read bound; server completed) | serving |
| ``falkordb/falkordb:latest`` (60002) | 30 000 | 14.42 MB | 2.61 s | serving |
| ``falkordb/falkordb-server:v4.20.6`` (42006, the CI pin) | 5 000 | 2.44 MB | 2.25 s | serving |

Four falsification attempts — index ``OPERATIONAL`` *and* index ``UNDER
CONSTRUCTION`` at dump time, three sizes, three graph-module versions — all
restore with the instance still answering. What the same-graph comparison does
show is *why the report read as a wedge*: the restore rebuilds the vector index
on the main thread, so it blocks the whole instance for its duration and the
caller cannot tell a long rebuild from a hang.

| same graph, 30 000 nodes, engine 60001 | restore | CPU burned | worst bystander PING |
|---|---|---|---|
| no vector index | 0.17 s | 0.2 s | 53 ms |
| vector index, ``OPERATIONAL`` | 2.62 s | 2.6 s | 2 497 ms |

⇒ 15× slower, and instance-blocking for the whole rebuild. Acceptance criteria 2
(*"the instance continues to serve other graphs throughout the restore"*) and 5
(*"a wedged instance must not be silent"*) are therefore still UNMET, and
upstream: the caller gets a socket timeout, with no progress, no log line and no
error to act on. Those two are not assertable-green from here and this file does
not pretend otherwise — the "other graphs" assertion below runs AFTER the
restore, not through it.

WHAT THESE TESTS PIN — the issue's two in-repo criteria (3 and 4):

1. **The restore is bounded and non-fatal** (criterion 3). It returns, the
   instance still answers ``PING``, and every node is still there. If the
   reported wedge ever returns, the ``RESTORE`` either overruns its socket
   bound — failing this file with the wedge named — or the post-restore
   liveness probe fails.
2. **The index is not silently dropped** (criterion 4). A graph-key ``DUMP``
   carries the index INSIDE the graph's serialized payload — a graph owns no
   second key for it (the only other key a graph owns is ``telemetry{<graph>}``,
   a Redis stream) — and the restored graph still answers vector queries. The
   issue listed this as an open question (*"if it is dropped, vector search on a
   restored shard is silently empty — a correctness bug on top of this one"*),
   and its own lane could not settle it because its vector probe returned no
   rows either way. Here the POPULATED index is a precondition of the fixture,
   so a restore that dropped it could not pass.

⛔ **Do NOT extend this file with a restore under a DIFFERENT graph name.** The
obvious way to prove payload carriage (restore the same payload as a second
key, which a name-keyed registry could not answer) **SEGFAULTS the engine this
repo's CI pins**: measured 2026-10-12, `falkordb/falkordb-server:v4.20.6`
(graph module 42006) dies with `signal 11` in `Index_SetLanguage` when an
indexed payload is restored under a new key **while the source key still
exists**, taking every other graph in the instance with it. Deleting the source
first, or having no vector index, avoids it; graph modules 60001 and 60002 do
not crash at all. That is a separate defect, filed as **#8270** — it must not be re-triggered
here, where it would red whole CI shards as a lost server.

⚠️ The populated index is not free to assume: a plain float-listed ``embedding``
is not indexed at all (measured — the probe returns 0 rows until the write goes
through ``vecf32(...)``). Without that gate this whole file would pass while
exercising an EMPTY index, which is exactly the vacuity the issue's own lane hit.

Lane: **docker/server FalkorDB only** — the vector index needs the engine's
vector-index API (the embedded lane has no HNSW), so the module skips visibly
with the URI-gate reason when ``TORTOISE_DB_URI`` is unset. On a URI-set run
where the ``graph`` module is missing the skip carries the availability-
regression reason instead, so the skip guard reds a probe regression rather than
silently greening (the #1436 class).

⚠️ **Where this actually executes in CI, so no reader mistakes a skip for a
pass.** A *tier-2* PR runs its whole fast band URI-less (``python-ci.yml``: the
``changes`` job's ``full`` is false, the leg sets ``CARVE_OUT=1`` and leaves
``TORTOISE_DB_URI`` empty), so this file skips there by design and the skip guard
exempts it — the same coverage class as ``test_acl_graph_users.py`` and
``test_hnsw_vector_index.py``. It **runs** on every ``full`` selection: pushes to
``main``, the nightly, and any PR whose diff the selector cannot place (shared
module / unknown path), where the fast band gets the docker URI and this file's
shard exercises it. An in-PR execution therefore has to be confirmed from the
selection (``python tools/ci_selection.py --changed-files - --event pull_request``),
not assumed from a green PR.

⚠️ This file performs a REAL ``DUMP``/``DEL``/``RESTORE`` against the live
instance, so every graph it touches is named from a uuid: it can never delete or
restore another test's graph. The graphs are dropped in teardown.

Run::

    TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
        uv run pytest tests/test_indexed_graph_key_restore_7846.py -v

``TORTOISE_7846_NODES`` raises the graph size (default 3 000). The wedge claim
carried no size threshold (its open question 4), so the default is a faithful
cheap guard; set it to ``30000`` to reproduce the issue's own scale locally.
"""

from __future__ import annotations

import contextlib
import os
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import redis

from tests import _live_utils

#: Node count for the fixture graph. Overridable because the issue's own scale
#: is 30 000 (≈20 s of writes) and this file is registered on the `core`
#: FALLBACK surface (it matches no named surface) — see the registration
#: comment in `config/ci-surfaces.yml`.
NODES = int(os.environ.get("TORTOISE_7846_NODES") or "3000")

#: Neighbours requested from the vector index. A POPULATED index returns all K.
K = 3

#: Bounds. A RESTORE that overruns its read bound IS the reported wedge (the
#: server never answers again), so the client timeout is the assertion's edge.
#:
#: The assertion bound (`_RESTORE_BOUND_S`) is deliberately BELOW the socket
#: timeout (`_RESTORE_TIMEOUT_S`). If the two were equal, the client socket
#: would raise at the same instant the assertion would fire, and "the restore
#: overran its bound" could not be told apart from "the socket timed out" —
#: the two failure modes criterion 3 exists to distinguish.
_CONNECT_TIMEOUT_S = 10.0
_RESTORE_TIMEOUT_S = 120.0
_RESTORE_BOUND_S = 90.0
_INDEX_READY_BOUND_S = 180.0
_BATCH = 500

_SUPPORTED_SCHEMES = ("docker", "redis", "rediss")


def _target():
    """``(host, port, username, password, ssl)`` for the live server, or None.

    Read from ``TORTOISE_DB_URI``. Deliberately NOT mutated: this file needs the
    URI the lane was given, and `tests/test_uri_env_mutations_declared.py`
    requires any test that writes that variable to declare it.

    A URI with no explicit port falls back through the #6673 seam
    (``_live_utils.docker_port()``, i.e. ``$TORTOISE_TEST_DOCKER_PORT``) — NOT a
    ``6379`` literal. That is the split-brain guard in
    ``tests/test_runner_ports_6673.py``: a file that reads the seam must not
    also dial the historical fixed port, or the probe passes while the client
    connects to nothing.
    """
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    parsed = urlparse(uri)
    if parsed.scheme not in _SUPPORTED_SCHEMES or not parsed.hostname:
        return None
    # #3039: the ONE credential-decoding rule — urlparse does not percent-decode.
    from tortoise.config import parse_uri_userinfo

    username, password = parse_uri_userinfo(uri)
    return (
        parsed.hostname,
        parsed.port or _live_utils.docker_port(),
        username,
        password,
        parsed.scheme == "rediss",
    )


_TARGET = _target()

pytestmark = pytest.mark.skipif(
    _TARGET is None,
    reason=_live_utils.LIVE_URI_SKIP_REASON,
)


def _redis(*, decode: bool = False, timeout: float = _RESTORE_TIMEOUT_S):
    """A bounded raw client. ``decode=False`` is REQUIRED for ``DUMP``.

    ``DUMP`` returns a binary payload and a ``decode_responses=True`` client
    raises ``UnicodeDecodeError`` on it — the first of the two traps the issue
    lists. Every client in this module is binary-safe for that reason.
    """
    host, port, username, password, ssl = _TARGET
    return redis.Redis(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=ssl,
        decode_responses=decode,
        socket_timeout=timeout,
        socket_connect_timeout=_CONNECT_TIMEOUT_S,
    )


def _graph_module_present() -> bool:
    """True when the connected server serves the FalkorDB ``graph`` module.

    Retried: a freshly-provisioned container answers ``PING`` before
    ``MODULE LIST`` is populated (the startup race that vacuous-skipped a whole
    ACL file once). Bounded at 5 × 2 s.
    """
    flat = b""
    for _attempt in range(5):
        try:
            rows = _redis(timeout=5.0).execute_command("MODULE", "LIST") or []
        except Exception:  # unreachable server: retry, then skip
            time.sleep(2)
            continue
        flat = b" ".join(
            token if isinstance(token, bytes) else str(token).encode()
            for row in rows
            for token in (row if isinstance(row, (list, tuple)) else [row])
            if token is not None
        )
        if b"graph" in flat:
            return True
        time.sleep(2)
    return False


def _rows(client, graph: str, cypher: str):
    """The result rows of a ``GRAPH.QUERY`` reply (``[header, rows, stats]``)."""
    out = client.execute_command("GRAPH.QUERY", graph, cypher)
    return out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else []


def _scalar(client, graph: str, cypher: str):
    rows = _rows(client, graph, cypher)
    return rows[0][0] if rows else None


def _index_status(client, graph: str) -> str:
    """The engine's own status text for the graph's first index."""
    try:
        rows = _rows(client, graph, "CALL db.indexes()")
    except Exception as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"
    if not rows:
        return "NO_INDEX"
    status = rows[0][7]
    return status.decode() if isinstance(status, bytes) else str(status)


def _knn_rows(client, graph: str, dim: int) -> int:
    """How many of the K nearest neighbours the vector index actually returns."""
    vec = ",".join(["0.5"] * dim)
    return len(
        _rows(
            client,
            graph,
            f"CALL db.idx.vector.queryNodes('Point','embedding',{K},"
            f"vecf32([{vec}]))",
        )
    )


def _vector(i: int, dim: int) -> list[float]:
    """A deterministic vector — no RNG, so a failure is reproducible."""
    return [float((i * 7919 + j * 104729) % 1000) / 1000.0 for j in range(dim)]


def _dump_delete_restore(client, graph: str) -> tuple[bytes, float]:
    """``DUMP`` → ``DEL`` → ``RESTORE ... REPLACE``; returns (payload, seconds).

    ``REPLACE`` is not optional: the engine keeps the graph in its module
    registry after the Redis key is deleted, so a bare ``RESTORE`` fails
    ``BUSYKEY`` — the second trap the issue lists.
    """
    payload = client.execute_command("DUMP", graph)
    client.execute_command("DEL", graph)
    started = time.perf_counter()
    client.execute_command("RESTORE", graph, 0, payload, "REPLACE")
    return payload, time.perf_counter() - started


def _assert_instance_serving(client) -> None:
    """The assertion the issue's criterion 3 asks for, verbatim."""
    for attempt in range(3):
        started = time.perf_counter()
        client.ping()
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < _RESTORE_BOUND_S * 1000, (
            f"PING #{attempt} took {elapsed_ms:.0f} ms after the restore "
            f"(bound {_RESTORE_BOUND_S:.0f}s) — the instance is not serving "
            f"(the #7846 wedge)"
        )


@pytest.fixture(scope="module")
def indexed_graph():
    """A unique graph: ``NODES`` ``:Point`` nodes, each with a REAL vector, and
    a POPULATED vector index. Yields a namespace with the graph names, the
    embedding width and the raw client."""
    if not _graph_module_present():
        pytest.skip(
            "Live FalkorDB (Docker) not available — server-URI run without the "
            "falkordb module (probe regression; must red the skip guard)"
        )

    from falkordb import FalkorDB

    # #4194/#4280: the width is the store's own declared constant, so a rotated
    # embedding width cannot silently bless vectors the index cannot hold.
    from tortoise.embeddings import EMBEDDING_DIM as DIM

    host, port, username, password, ssl = _TARGET
    tag = uuid.uuid4().hex[:16]
    graph = f"t_7846_{tag}"
    bystander = f"t_7846_{tag}_bystander"

    raw = _redis()
    writer = FalkorDB(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=ssl,
        socket_timeout=_RESTORE_TIMEOUT_S,
        socket_connect_timeout=_CONNECT_TIMEOUT_S,
    ).select_graph(graph)

    try:
        # 1. The nodes. `vecf32(...)` is load-bearing, not decoration: a plain
        # float-listed `embedding` is NOT indexed, and the KNN gate below would
        # then be measuring an empty index (measured — it returns 0 rows).
        for start in range(0, NODES, _BATCH):
            writer.query(
                "UNWIND $rows AS r CREATE (p:Point {id: r.id}) "
                "SET p.embedding = vecf32(r.emb)",
                {"rows": [{"id": i, "emb": _vector(i, DIM)}
                          for i in range(start, min(start + _BATCH, NODES))]},
            )

        # 2. The index, in the SAME form production uses
        # (`tortoise/projection/__init__.py::_ensure_vector_index_api`, the
        # Cypher-native branch): CREATE VECTOR INDEX ... OPTIONS.
        writer.query(
            "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
            f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}"
        )

        deadline = time.monotonic() + _INDEX_READY_BOUND_S
        status = None
        while time.monotonic() < deadline:
            status = _index_status(raw, graph)
            if status == "OPERATIONAL":
                break
            time.sleep(0.25)
        assert status == "OPERATIONAL", (
            f"the vector index never reached OPERATIONAL (last status: {status}) "
            f"within {_INDEX_READY_BOUND_S:.0f}s — the restore below would be "
            f"measured against an index that is still being built"
        )

        # 3. THE GATE. Without it the whole file could pass on an empty index.
        found = _knn_rows(raw, graph, DIM)
        assert found == K, (
            f"the vector index is not populated: {K} neighbours requested, "
            f"{found} returned. Every restore assertion below would then be "
            f"vacuous (an unindexed graph-key restore is the issue's own "
            f"control case, not the case it reports)."
        )

        # 4. A second graph in the same instance — the issue's bystander.
        raw.execute_command("GRAPH.QUERY", bystander, "CREATE (n:Bystander {id: 1})")

        yield SimpleNamespace(
            graph=graph,
            bystander=bystander,
            dim=DIM,
            raw=raw,
            nodes=NODES,
        )
    finally:
        with contextlib.suppress(Exception):
            for name in (graph, bystander):
                raw.execute_command("DEL", name)
                raw.execute_command("DEL", f"telemetry{{{name}}}")
        with contextlib.suppress(Exception):
            raw.close()


def test_graph_key_restore_of_an_indexed_graph_is_bounded_and_keeps_serving(
    indexed_graph,
):
    """Criterion 3 — the restore returns, the instance answers, the data is there.

    This is the regression: the reported failure is a ``RESTORE`` that never
    returns and leaves the instance unable to answer ``PING``.
    """
    raw = indexed_graph.raw
    graph = indexed_graph.graph

    try:
        payload, elapsed = _dump_delete_restore(raw, graph)
    except Exception as exc:
        pytest.fail(
            f"the graph-key RESTORE of an index-bearing graph did not return "
            f"({type(exc).__name__}: {exc}). That is #7846: the instance stops "
            f"serving and never recovers — the wedged instance is silent, so "
            f"this failure message is the only signal there is."
        )

    assert len(payload) > 0, "DUMP produced an empty payload"
    assert elapsed < _RESTORE_BOUND_S, (
        f"RESTORE took {elapsed:.1f}s (bound {_RESTORE_BOUND_S:.0f}s, socket "
        f"timeout {_RESTORE_TIMEOUT_S:.0f}s)"
    )

    # ⛔ Criterion 3's own assertion.
    _assert_instance_serving(raw)

    assert _scalar(raw, graph, "MATCH (n) RETURN count(n)") == indexed_graph.nodes, (
        "the restored graph is missing nodes — the restore did not carry the graph"
    )

    # The sibling graph is still there. NOTE: this runs AFTER the restore, not
    # through it — the restore blocks the instance for its duration (measured),
    # so "keeps serving other graphs THROUGHOUT" is not assertable-green here.
    assert _scalar(raw, indexed_graph.bystander, "MATCH (n) RETURN count(n)") == 1, (
        "the bystander graph did not survive the restore of its sibling"
    )


def test_the_vector_index_survives_a_graph_key_restore(indexed_graph):
    """Criterion 4 — the index is PRESERVED across the restore, not dropped.

    The issue's open question: the index lives in the separately-loaded
    ``vectorset`` module, so its lane suspected a graph-key ``DUMP`` could not
    carry it, and that a restored shard would then answer vector search with an
    empty result. It could not settle this (its probe returned no rows either
    way). Measured here: the index travels INSIDE the graph key's payload — a
    graph owns no separate index key — and the restored graph answers the SAME
    vector query with the same number of rows.

    The assertion is SAME-NAME by design — see the ⛔ note in the module
    docstring: proving payload carriage by restoring under a new name segfaults
    the pinned engine, so that probe lives in
    ``docs/evidence/7846-indexed-graph-restore/crash_isolate.py``, OUTSIDE CI.
    """
    raw = indexed_graph.raw
    graph = indexed_graph.graph
    dim = indexed_graph.dim

    before = _knn_rows(raw, graph, dim)
    assert before == K, f"precondition: {before} neighbours before the restore"

    _payload, _elapsed = _dump_delete_restore(raw, graph)

    status = _index_status(raw, graph)
    assert status == "OPERATIONAL", (
        f"the vector index is gone or damaged after the restore ({status}) — "
        f"it was silently dropped"
    )
    after = _knn_rows(raw, graph, dim)
    assert after == before, (
        f"the restored graph answers {after} neighbours where the original "
        f"answered {before} — the index did not survive the graph-key restore"
    )
