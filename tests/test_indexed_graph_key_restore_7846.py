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
(*"a wedged instance must not be silent"*) are therefore still UNMET **upstream**:
no in-repo change can make a blocked engine serve. What this file CAN do, and now
does, is stop the silence being silent — the restore is driven through
``tortoise/graph_key_restore.py``, which bounds it, watches the instance from a
second connection throughout, and raises a typed error that names which of
*blocked / dead / loaded* actually happened. The "other graphs" assertion below
still runs AFTER the restore, never through it; the silence measured DURING it is
asserted to be the restore's own (it ends when the restore does).

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
3. **The block is measured, not inferred** (criterion 5, at our boundary). The
   witness samples below are the evidence: if the instance stops answering, the
   observation says for how long and that it came back — and if it never comes
   back, the driver fails with ``GraphRestoreSilentError`` naming the silence
   instead of a bare socket timeout.

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
from tortoise.graph_key_restore import (
    BLOCKED,
    SERVED,
    RestoreObservation,
    restore_graph_key,
    restore_graph_key_safe,
    tcp_is_listening,
)

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
#:
#: The bound is now the DRIVER's, not the socket's: `_RESTORE_BOUND_S` is passed to
#: `graph_key_restore.restore_graph_key` as `bound_s`, and `_RESTORE_TIMEOUT_S`
#: stays the `restore` client's socket timeout — the backstop that stops an
#: abandoned worker thread from living forever.
_CONNECT_TIMEOUT_S = 10.0
_RESTORE_TIMEOUT_S = 120.0
_RESTORE_BOUND_S = 90.0
_INDEX_READY_BOUND_S = 180.0
_BATCH = 500

#: The witness probe: its own connection, its own (short) socket timeout, sampled
#: every `_WITNESS_POLL_S`. A blocked instance answers NOTHING, so this probe's
#: timeout is the resolution of the measured silence.
_WITNESS_TIMEOUT_S = 1.0
_WITNESS_POLL_S = 0.1

#: How far the measured outage may exceed the restore's own duration before it is
#: no longer attributable to the restore. Two measured facts set the size:
#:
#: * a witness PING in flight when the restore starts only fails when ITS socket
#:   timeout expires, and the first answered sample comes up to one poll later;
#: * an ANSWERED witness PING can outlast the restoring client's own call — measured
#:   30 000 nodes: restore 4.12 s, slowest answered PING 5 798 ms — because the client
#:   sees the reply while the instance is still finishing the work the reply covers.
#:
#: So the allowance is `elapsed + 2 s`, and at least 1.5x `elapsed`, whichever is
#: larger. This slack guards the ATTRIBUTION claim only: a genuine wedge never reaches
#: this assertion at all, because `_RESTORE_BOUND_S` has already failed the restore.
_SILENCE_SLACK_S = _WITNESS_TIMEOUT_S + 1.0


def _max_attributable_outage_ms(observation) -> float:
    """The largest outage still attributable to this restore (see `_SILENCE_SLACK_S`)."""
    allowed_s = max(observation.elapsed_s * 1.5, observation.elapsed_s + _SILENCE_SLACK_S)
    return allowed_s * 1000


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


def _dump_delete_restore(client, graph: str) -> RestoreObservation:
    """``DUMP`` → ``DEL`` → ``RESTORE ... REPLACE``: WITNESSED throughout, with the
    RESTORE step BOUNDED (``_RESTORE_BOUND_S``) — and only that step.

    Delegates to ``tortoise.graph_key_restore.restore_graph_key`` (#7846 criterion 5):
    the restore runs on a worker thread while a witness on its OWN connection polls
    ``PING``, so the instance's silence is measured rather than inferred from the
    operation's duration. On an overrun the driver raises
    ``GraphRestoreSilentError`` (port still accepts → blocked, not load, not a crash)
    or ``GraphRestoreLostError`` (port refused → the process died), each carrying the
    measurement and a recovery instruction — the actionable replacement for the bare
    ``redis.exceptions.TimeoutError`` this helper used to surface.

    ⛔ ``_RESTORE_BOUND_S`` does NOT cover ``DUMP`` or ``DEL``: they run inline on this
    thread before the driver's worker starts (#8827 review), so their only limit is
    ``client``'s own socket timeout — ``_RESTORE_TIMEOUT_S`` (120 s), deliberately
    above the 90 s restore bound (`test_7846_restore_doc_and_bounds.py` pins that
    ordering). A caller wiring this into product code must give ITS client a socket
    timeout of its own; the driver reports the two durations separately
    (``elapsed_s`` = the whole sequence, ``restore_s`` = the bounded step).

    ``REPLACE`` is not optional: the engine keeps the graph in its module registry
    after the Redis key is deleted, so a bare ``RESTORE`` fails ``BUSYKEY`` — the
    second trap the issue lists. ``DUMP`` is binary and every client here is
    binary-safe (``decode_responses=False``), the first trap.
    """
    host, port, _username, _password, _ssl = _TARGET
    witness = _redis(timeout=_WITNESS_TIMEOUT_S)
    try:
        return restore_graph_key(
            graph=graph,
            dump=lambda: client.execute_command("DUMP", graph),
            delete=lambda: client.execute_command("DEL", graph),
            restore=lambda payload: client.execute_command(
                "RESTORE", graph, 0, payload, "REPLACE"
            ),
            ping=witness.ping,
            listening=lambda: tcp_is_listening(
                host, port, timeout_s=_WITNESS_TIMEOUT_S
            ),
            bound_s=_RESTORE_BOUND_S,
            poll_interval_s=_WITNESS_POLL_S,
        )
    finally:
        with contextlib.suppress(Exception):
            witness.close()


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
        observation = _dump_delete_restore(raw, graph)
    except Exception as exc:
        pytest.fail(
            f"the graph-key RESTORE of an index-bearing graph did not return "
            f"({type(exc).__name__}: {exc}). That is #7846. The driver's message "
            f"above is the measurement, not a wrapper: it says whether the "
            f"instance was blocked (port accepts, nothing answers), gone (port "
            f"refused) or merely slow, so this failure is not the silent wedge "
            f"the issue was filed for."
        )

    assert observation.payload_bytes > 0, "DUMP produced an empty payload"
    # `bound_s` bounds the RESTORE STEP, not `elapsed_s`: the latter is the caller-held
    # time for the WHOLE sequence (DUMP + DEL + the bounded restore + the witness join +
    # the recovery probe), so a restore that returns at 89 s under a 90 s bound
    # legitimately reports elapsed_s > 90 while status == SERVED. Asserting on
    # `elapsed_s` here failed with "it should have raised instead" and blamed the driver
    # for behaving exactly as documented — a misfire that would send whoever raises
    # TORTOISE_7846_NODES or runs on a slow engine after a non-bug (#8827 review).
    assert observation.restore_s < _RESTORE_BOUND_S, (
        f"the driver returned a RESTORE step of {observation.restore_s:.1f}s under a "
        f"{_RESTORE_BOUND_S:.0f}s bound — it should have raised instead"
    )
    assert observation.restore_returned is True
    assert observation.status in (SERVED, BLOCKED), (
        f"a completed restore reported status={observation.status!r}"
    )
    assert observation.witness_samples > 0, (
        "the witness took no samples: the instance was never actually watched "
        "through the restore, so every claim below would be unmeasured"
    )

    # ⛔ Criterion 5, at our boundary: whatever silence the restore caused is
    # MEASURED, and it is ATTRIBUTABLE — it ends when the restore does. A silence
    # that outlasts the restore is not the index rebuild and must not be reported
    # as one.
    if observation.witness_failures:
        assert observation.silence_started_s is not None
        assert observation.silence_ended_s is not None, (
            f"the instance never answered again inside the observation window "
            f"({observation.witness_failures} unanswered PINGs, no recovery): the "
            f"silence outlasted the restore, so it cannot be attributed to the "
            f"index rebuild and may be a wedge"
        )
        assert observation.silent_ms <= _max_attributable_outage_ms(observation), (
            f"the instance was silent for {observation.silent_ms:.0f} ms while the "
            f"restore took {observation.elapsed_s:.2f}s: the silence is longer "
            f"than the operation that supposedly caused it"
        )
    if observation.status == BLOCKED:
        # Either signal (a run of unanswered PINGs, or a PING queued behind the
        # rebuild) is the #7846 block, and neither may outlast the restore that
        # caused it.
        assert observation.blocked_ms <= _max_attributable_outage_ms(observation), (
            f"the instance was unavailable for {observation.blocked_ms:.0f} ms "
            f"(slowest answered PING {observation.witness_max_latency_ms:.0f} ms, "
            f"silence {observation.silent_ms:.0f} ms) while the restore took "
            f"{observation.elapsed_s:.2f}s: the outage is longer than the "
            f"operation that supposedly caused it"
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


def _safe_restore_seams(raw, graph, *, witness, index_at_create):
    """Real engine seams for ``restore_graph_key_safe`` — plus the load-bearing probe.

    ``index_at_create`` is filled with the index status observed ON THE ENGINE at the
    moment the re-create step runs. It is the deterministic detector for the mutation
    this test exists to catch: **skip the index drop** and the payload carries the index,
    the ``RESTORE`` rebuilds it on the serving thread, and the re-create step then finds
    an index already present. A duration assertion could not catch that — at this file's
    default 3 000 nodes the indexed rebuild is ~0.4 s, far inside any bound generous
    enough not to false-red a slower engine — so the sequence is asserted instead of
    inferring it from timing.
    """

    def _create(definition):
        # Sampled BEFORE the CREATE: with the drop skipped, this is where the defect shows
        # up, and an engine `Attribute ... is already indexed` raised by the CREATE would
        # bury it under a message that names the symptom instead of the cause.
        status = _index_status(raw, graph)
        index_at_create.append(status)
        assert status == "NO_INDEX", (
            f"the DROP before the DUMP did not take: the graph already carries an index "
            f"({status}) when it is being re-created, so the DUMPed payload carried the "
            f"index and the RESTORE rebuilt it on the serving thread — the #7846 block "
            f"this path exists to avoid"
        )
        raw.execute_command("GRAPH.QUERY", graph, definition.create_cypher())

    return {
        "dump": lambda: raw.execute_command("DUMP", graph),
        "delete": lambda: raw.execute_command("DEL", graph),
        "restore": lambda payload: raw.execute_command(
            "RESTORE", graph, 0, payload, "REPLACE"
        ),
        "ping": witness.ping,
        "list_indexes": lambda: _rows(raw, graph, "CALL db.indexes()"),
        "drop_index": lambda definition: raw.execute_command(
            "GRAPH.QUERY", graph, definition.drop_cypher()
        ),
        "create_index": _create,
    }


def test_the_safe_path_restores_an_indexed_graph_without_the_index_in_the_payload(
    indexed_graph,
):
    """#7846 — the safe path: index dropped BEFORE the DUMP, restored, re-created.

    This is the shape the fix ships: a graph-key ``RESTORE`` of an index-bearing graph is
    only safe if the index is not in the payload, because the engine rebuilds it INSIDE
    the RESTORE on its serving thread (measured: 0.17 s index-free vs 2.62 s indexed at
    30 000 nodes, worst bystander PING == the restore duration).

    The four assertions the issue asks for, plus the one that makes the guard
    LOAD-BEARING: the index must be ABSENT on the engine when the re-create step runs. Skip
    the drop and that fails at any graph size, whereas a duration assertion would pass —
    at 3 000 nodes the indexed rebuild is ~0.4 s, well inside any bound that does not
    false-red a slow runner.
    """
    raw = indexed_graph.raw
    graph = indexed_graph.graph
    dim = indexed_graph.dim
    witness = _redis(timeout=_WITNESS_TIMEOUT_S)
    index_at_create: list[str] = []
    seams = _safe_restore_seams(
        raw, graph, witness=witness, index_at_create=index_at_create
    )

    try:
        safe = restore_graph_key_safe(
            graph=graph,
            dump=seams["dump"],
            delete=seams["delete"],
            restore=seams["restore"],
            ping=seams["ping"],
            list_indexes=seams["list_indexes"],
            drop_index=seams["drop_index"],
            create_index=seams["create_index"],
            index_ready=lambda: _index_status(raw, graph) == "OPERATIONAL",
            index_ready_bound_s=_INDEX_READY_BOUND_S,
            bound_s=_RESTORE_BOUND_S,
            poll_interval_s=_WITNESS_POLL_S,
            listening=lambda: tcp_is_listening(
                _TARGET[0], _TARGET[1], timeout_s=_WITNESS_TIMEOUT_S
            ),
        )
    finally:
        with contextlib.suppress(Exception):
            witness.close()

    observation = safe.observation
    report = safe.indexes

    # 1. The restore returned inside the bound (the socket timeout stays above it —
    #    `test_7846_restore_doc_and_bounds.py` pins that ordering).
    assert observation.restore_s < _RESTORE_BOUND_S, (
        f"the RESTORE step took {observation.restore_s:.1f}s under a "
        f"{_RESTORE_BOUND_S:.0f}s bound — it should have raised instead"
    )
    assert observation.restore_returned is True
    assert observation.status in (SERVED, BLOCKED), (
        f"a completed safe restore reported status={observation.status!r}"
    )

    # 2. The index was read off the catalogue, dropped before the DUMP, re-created
    #    after the RESTORE, and the re-created definition matches the read one.
    assert report.dropped is True, "the safe path did not report dropping the index"
    assert report.recreated is True, "the safe path did not re-create the index"
    assert report.verified is True, (
        "the re-created index does not match the definition read before the drop"
    )
    assert report.definitions, "no definition was read off the catalogue"
    assert report.definitions[0].dimension == dim, (
        f"the definition's dimension is {report.definitions[0].dimension}, the graph's "
        f"vectors are {dim}-wide"
    )
    assert report.definitions[0].similarity_function == "cosine", (
        f"the definition's metric was not read faithfully: "
        f"{report.definitions[0].similarity_function!r}"
    )

    # 3. THE LOAD-BEARING ASSERTION: no index was on the graph when it was re-created,
    #    i.e. the payload carried none and the RESTORE had no index to rebuild.
    assert index_at_create == ["NO_INDEX"], (
        f"the re-create step found the index already on the graph ({index_at_create}) — "
        f"the DROP before the DUMP did not happen, so the RESTORE carried an index and "
        f"rebuilt it on the serving thread (the #7846 block)"
    )

    # 4. The graph's data came back.
    assert _scalar(raw, graph, "MATCH (n) RETURN count(n)") == indexed_graph.nodes, (
        "the restored graph is missing nodes — the restore did not carry the graph"
    )

    # 5. The index is QUERYABLE afterwards — the acceptance criterion. `verified` only
    #    proves the definition round-tripped; this proves the rebuilt index answers.
    status = _index_status(raw, graph)
    assert status == "OPERATIONAL", (
        f"the index is {status} ({report.index_ready=}) after the safe restore"
    )
    assert _knn_rows(raw, graph, dim) == K, (
        "the re-created vector index returns no neighbours — the restored graph would "
        "answer vector search with an empty result"
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

    _observation = _dump_delete_restore(raw, graph)
    assert _observation.payload_bytes > 0

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
