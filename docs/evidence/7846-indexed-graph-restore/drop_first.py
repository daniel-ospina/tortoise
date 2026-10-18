"""#7846 — the index-drop-first restore, measured against the raw graph-key restore.

The premise of ``tortoise/graph_key_restore.py::restore_graph_key_safe``: a graph-key
``RESTORE`` of a graph carrying an in-graph VECTOR INDEX rebuilds that index on the
engine's SERVING thread, so the whole instance answers nothing for the duration of the
rebuild. Dropping the index BEFORE the ``DUMP`` makes the payload index-free (there is no
index in it to rebuild); re-creating the index from the definition read off the catalogue
restores the same index afterwards.

Three readings, same graph size, same client, one engine, a bystander PING polled on its
own socket throughout:

    guard        restore_graph_key(index_guard=...)  -> REFUSED, nothing deleted
    raw          restore_graph_key(...)              -> the restore production issues today
    safe         restore_graph_key_safe(...)         -> drop -> dump/del/restore -> recreate

Usage::

    python docs/evidence/7846-indexed-graph-restore/drop_first.py <port> <nodes>

Exit code 2 means INVALID HARNESS — the reading would say nothing about an index-bearing
restore: the vector index never reached ``OPERATIONAL``, it is not POPULATED (fewer than K
neighbours, i.e. the issue's own index-free control case — see README §1), or the server
reports no graph module (no reading is taken at all). ``SystemExit(2)``, not ``assert``: an
``assert`` is STRIPPED under ``python -O`` (leaving no vacuity gate at all) and exits 1,
so a consumer keying on the documented code got the case it was guarding against under the
wrong one.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid

import redis

from tortoise.graph_key_restore import (
    IndexedGraphRestoreRefused,
    restore_graph_key,
    restore_graph_key_safe,
    vector_index_definitions,
)

DIM = 384
K = 3
BATCH = 500
INDEX_READY_BOUND_S = 180.0


def _rows(client, graph, cypher):
    out = client.execute_command("GRAPH.QUERY", graph, cypher)
    return out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else []


def _scalar(client, graph, cypher):
    rows = _rows(client, graph, cypher)
    return rows[0][0] if rows else None


def _vector(i, dim):
    return [float((i * 7919 + j * 104729) % 1000) / 1000.0 for j in range(dim)]


def _knn(client, graph, dim):
    vec = ",".join(["0.5"] * dim)
    return len(
        _rows(
            client,
            graph,
            f"CALL db.idx.vector.queryNodes('Point','embedding',{K},vecf32([{vec}]))",
        )
    )


def _index_status(client, graph):
    rows = _rows(client, graph, "CALL db.indexes()")
    if not rows:
        return "NO_INDEX"
    status = rows[0][7]
    return status.decode() if isinstance(status, bytes) else str(status)


class Bystander:
    """A PING poller on its OWN connection — the instance's serving-ness, measured."""

    def __init__(self, port):
        # ⛔ The socket timeout is deliberately ABOVE the longest block this harness
        # measures, so ONE probe spans the block and its latency IS the outage. With a
        # shorter timeout the reading is not a single probe's latency: redis-py's default
        # `Retry` lists `TimeoutError` in `supported_errors`, so a timed-out PING is
        # retried internally and the call RETURNS with the RETRY-SUMMED wall time (that is
        # how an earlier revision of this file produced "0 failed pings, worst 3 330 ms"
        # through a 1 s socket). A retry-summed number is true but ambiguous — the same
        # 3 330 ms could be one 3.3 s block or four 0.8 s stalls — and this file exists to
        # report a measurement, not a plausible one. `socket_connect_timeout` stays short:
        # a refused port is the OTHER thing this harness must not confuse with a block.
        self.client = redis.Redis(
            host="127.0.0.1", port=port, decode_responses=True,
            socket_timeout=30.0, socket_connect_timeout=5.0,
        )
        self.latencies: list[float] = []
        self.failures = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                self.client.ping()
            except Exception:
                self.failures += 1
            else:
                self.latencies.append((time.perf_counter() - started) * 1000)
            time.sleep(0.05)

    def __enter__(self):
        self._thread.start()
        time.sleep(0.3)
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5)
        self.client.close()
        return False

    @property
    def worst_ms(self):
        return max(self.latencies) if self.latencies else 0.0


def port_of(client):
    """The port a raw client dialled (so the falkordb writer hits the same engine)."""
    return client.connection_pool.connection_kwargs["port"]


def build(client, graph, nodes):
    """``nodes`` ``:Point`` rows with a REAL vector, then the index production uses."""
    from falkordb import FalkorDB

    writer = FalkorDB(host="127.0.0.1", port=port_of(client)).select_graph(graph)
    for start in range(0, nodes, BATCH):
        # `vecf32(...)` is load-bearing: a plain float-listed `embedding` is NOT indexed,
        # and every reading below would then be about an EMPTY index (README §1).
        writer.query(
            "UNWIND $rows AS r CREATE (p:Point {id: r.id}) SET p.embedding = vecf32(r.emb)",
            {"rows": [{"id": i, "emb": _vector(i, DIM)}
                      for i in range(start, min(start + BATCH, nodes))]},
        )
    writer.query(
        "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
        f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'}}"
    )


def await_operational(client, graph):
    deadline = time.monotonic() + INDEX_READY_BOUND_S
    status = None
    while time.monotonic() < deadline:
        status = _index_status(client, graph)
        if status == "OPERATIONAL":
            return status
        time.sleep(0.25)
    return status


def _indexes(client, graph):
    """The graph's ``CALL db.indexes()`` rows — the seam the production code parses."""
    return _rows(client, graph, "CALL db.indexes()")


def _seams(client, graph):
    def _drop(definition):
        client.execute_command("GRAPH.QUERY", graph, definition.drop_cypher())

    def _create(definition):
        client.execute_command("GRAPH.QUERY", graph, definition.create_cypher())

    def _ready():
        return _index_status(client, graph) == "OPERATIONAL"

    return {
        "dump": lambda: client.execute_command("DUMP", graph),
        "delete": lambda: client.execute_command("DEL", graph),
        "restore": lambda payload: client.execute_command(
            "RESTORE", graph, 0, payload, "REPLACE"
        ),
        "list_indexes": lambda: _indexes(client, graph),
        "drop_index": _drop,
        "create_index": _create,
        "index_ready": _ready,
    }


def _invalid_harness(message: str):
    """Exit 2: this reading would not be about an index-bearing restore (README §1)."""
    print(f"INVALID HARNESS: {message}", file=sys.stderr)
    raise SystemExit(2)


def _prepare(client, nodes, tag):
    """A unique graph with ``nodes`` indexed nodes, gated on a POPULATED index."""
    graph = f"t_7846_df_{tag}_{uuid.uuid4().hex[:10]}"
    bystander_graph = f"{graph}_bystander"
    build(client, graph, nodes)
    status = await_operational(client, graph)
    if status != "OPERATIONAL":
        _invalid_harness(f"the vector index never reached OPERATIONAL: {status}")
    populated = _knn(client, graph, DIM)
    if populated != K:
        _invalid_harness(
            f"the index is not POPULATED ({K} requested, {populated} returned) — the "
            f"restore below would be the issue's own index-free control case"
        )
    client.execute_command("GRAPH.QUERY", bystander_graph, "CREATE (n:Bystander {id: 1})")
    return graph, bystander_graph


def _cleanup(client, *names):
    for name in names:
        try:
            client.execute_command("DEL", name)
            client.execute_command("DEL", f"telemetry{{{name}}}")
        except Exception:
            pass


def arm_guard(client, port, nodes):
    """``restore_graph_key(index_guard=...)`` on an indexed graph: REFUSED."""
    graph, bystander_graph = _prepare(client, nodes, "guard")
    result = {"arm": "guard (index_guard=...)", "nodes": nodes}
    try:
        calls = {"dump": 0, "delete": 0, "restore": 0}

        def _counted(name, fn):
            def _call(*args):
                calls[name] += 1
                return fn(*args)
            return _call

        seams = _seams(client, graph)
        try:
            restore_graph_key(
                graph=graph,
                dump=_counted("dump", seams["dump"]),
                delete=_counted("delete", seams["delete"]),
                restore=_counted("restore", seams["restore"]),
                ping=redis.Redis(host="127.0.0.1", port=port, decode_responses=True).ping,
                index_guard=seams["list_indexes"],
                bound_s=90.0,
            )
        except IndexedGraphRestoreRefused as exc:
            result["refused"] = True
            result["destructive_calls_before_refusal"] = dict(calls)
            result["message_head"] = str(exc).split(".")[0]
        else:
            result["refused"] = False
            raise AssertionError("the guard did not fire on an indexed graph")
        result["nodes_still_there"] = _scalar(client, graph, "MATCH (n) RETURN count(n)")
        result["index_still_there"] = _index_status(client, graph)
        result["knn_still_there"] = _knn(client, graph, DIM)
    finally:
        _cleanup(client, graph, bystander_graph)
    return result


def arm_raw(client, port, nodes):
    """``restore_graph_key`` with no guard — the restore production issues today."""
    graph, bystander_graph = _prepare(client, nodes, "raw")
    result = {"arm": "raw (restore_graph_key, no guard)", "nodes": nodes}
    try:
        seams = _seams(client, graph)
        definition = vector_index_definitions(_indexes(client, graph), graph=graph)[0]
        result["index_definition"] = definition.as_dict()
        witness = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
        with Bystander(port) as bystander:
            time.sleep(0.2)
            observation = restore_graph_key(
                graph=graph,
                dump=seams["dump"],
                delete=seams["delete"],
                restore=seams["restore"],
                ping=witness.ping,
                bound_s=90.0,
            )
        # ⚠️ read the bystander AFTER the `with` exits, i.e. after its thread is joined:
        # the ONE PING that spans the block is still in flight when the restore returns,
        # so reading inside the block reported "worst 55 ms" about a 3.1 s block.
        result["bystander_worst_ping_ms"] = round(bystander.worst_ms, 1)
        result["bystander_failed_pings"] = bystander.failures
        result["bystander_samples"] = len(bystander.latencies)
        result["restore_s"] = round(observation.restore_s, 3)
        result["status"] = observation.status
        result["blocked_ms"] = round(observation.blocked_ms, 1)
        result["index_after"] = await_operational(client, graph)
        result["knn_after"] = _knn(client, graph, DIM)
        result["nodes_after"] = _scalar(client, graph, "MATCH (n) RETURN count(n)")
    finally:
        _cleanup(client, graph, bystander_graph)
    return result


def arm_safe(client, port, nodes):
    """``restore_graph_key_safe`` — drop the index, restore, re-create it."""
    graph, bystander_graph = _prepare(client, nodes, "safe")
    result = {"arm": "safe (restore_graph_key_safe)", "nodes": nodes}
    try:
        seams = _seams(client, graph)
        witness = redis.Redis(host="127.0.0.1", port=port, decode_responses=True)
        with Bystander(port) as bystander:
            time.sleep(0.2)
            safe = restore_graph_key_safe(
                graph=graph,
                dump=seams["dump"],
                delete=seams["delete"],
                restore=seams["restore"],
                ping=witness.ping,
                list_indexes=seams["list_indexes"],
                drop_index=seams["drop_index"],
                create_index=seams["create_index"],
                index_ready=seams["index_ready"],
                index_ready_bound_s=INDEX_READY_BOUND_S,
                bound_s=90.0,
            )
        result["bystander_worst_ping_ms"] = round(bystander.worst_ms, 1)
        result["bystander_failed_pings"] = bystander.failures
        result["bystander_samples"] = len(bystander.latencies)
        result["restore_s"] = round(safe.observation.restore_s, 3)
        result["status"] = safe.observation.status
        result["blocked_ms"] = round(safe.observation.blocked_ms, 1)
        result["indexes"] = safe.indexes.as_dict()
        result["index_after"] = _index_status(client, graph)
        result["knn_after"] = _knn(client, graph, DIM)
        result["nodes_after"] = _scalar(client, graph, "MATCH (n) RETURN count(n)")
    finally:
        _cleanup(client, graph, bystander_graph)
    return result


def main(argv):
    port = int(argv[1])
    nodes = int(argv[2])
    client = redis.Redis(
        host="127.0.0.1", port=port, decode_responses=False,
        socket_timeout=600.0, socket_connect_timeout=10.0,
    )
    probe = client.execute_command("MODULE", "LIST")
    flat = b" ".join(
        token if isinstance(token, bytes) else str(token).encode()
        for row in probe for token in (row if isinstance(row, (list, tuple)) else [row])
    )
    if b"graph" not in flat:
        print("no graph module on this server", file=sys.stderr)
        return 2

    readings = [
        arm_guard(client, port, nodes),
        arm_raw(client, port, nodes),
        arm_safe(client, port, nodes),
    ]
    for reading in readings:
        print(json.dumps(reading))

    guard, raw, safe = readings
    print(
        f"\nGUARD: refused={guard['refused']}, destructive calls before the refusal = "
        f"{guard['destructive_calls_before_refusal']}, graph intact "
        f"({guard['nodes_still_there']} nodes, index {guard['index_still_there']}, "
        f"KNN {guard['knn_still_there']})\n"
        f"RESTORE step: raw {raw['restore_s']:.2f} s vs safe {safe['restore_s']:.2f} s "
        f"({raw['restore_s'] / max(safe['restore_s'], 1e-9):.1f}x)\n"
        f"worst bystander PING: raw {raw['bystander_worst_ping_ms']:.0f} ms vs safe "
        f"{safe['bystander_worst_ping_ms']:.0f} ms\n"
        f"safe: index dropped={safe['indexes']['dropped']}, recreated="
        f"{safe['indexes']['recreated']}, verified={safe['indexes']['verified']}, ready="
        f"{safe['indexes']['index_ready']}, {safe['index_after']}, KNN "
        f"{safe['knn_after']}, nodes {safe['nodes_after']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
