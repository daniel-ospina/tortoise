"""#12766 — does a KEPT FULLTEXT (or RANGE) index block a graph-key RESTORE?

``docs/architecture/SEARCH-ARCHITECTURE.md`` §7 measures the vector index and leaves one
leg open:

> "The window KEEPS the fulltext index in the graph (decision item 2, §6.2), so the index
> that stays is exactly the one that record did not measure: if a kept ``db.idx.fulltext`` —
> or a range — index blocks a restore the way the vector index did, then the window is a
> feasibility question again"

So this harness measures exactly that, in the #7846 record's shape
(``docs/evidence/7846-indexed-graph-restore/README.md``) so the numbers are comparable:

    noindex            the FLOOR — same graph, same payload, no index at all
    fulltext-kept      the index present across DUMP -> DEL -> RESTORE   (the §7 question)
    fulltext-dropped   dropped BEFORE the dump, re-created after         (the mitigation)
    range-kept         the same question for a RANGE index (§7 names both)
    range-dropped      its mitigation

Every arm reports: DUMP / DEL / RESTORE step timings, the WHOLE cycle wall time, the worst
bystander ``PING`` sampled on a second socket throughout, the post-restore index status and
document count, and a post-restore QUERY that must return hits.

Usage::

    python docs/evidence/12766-fulltext-restore/fulltext_restore_cost.py <port> <nodes> \
        [--arms noindex,fulltext-kept,fulltext-dropped,range-kept,range-dropped]

Exit code 2 means INVALID HARNESS — the reading would say nothing about an index-bearing
restore: the index never reached ``OPERATIONAL``, it is not POPULATED (fewer documents than
nodes, or a query returning no hits), or the server reports no graph module. This is the
gate #7846 §1 was written for — that lane's first harness measured an EMPTY index and
therefore measured nothing — and it is a ``SystemExit(2)`` rather than an ``assert``
because an ``assert`` is stripped under ``python -O``.

⚠️ Three harness details are copied from the #7846 record because each was a MEASURED
correction there, not a style choice:

1. **A populated-index gate before any timer starts** (above; README §1).
2. **The bystander's socket timeout is set ABOVE the longest block this harness can
   measure**, so ONE probe spans the block and its latency IS the outage. With a shorter
   timeout, ``redis-py``'s default ``Retry`` lists ``TimeoutError`` in ``supported_errors``,
   so a timed-out PING is silently retried and the call RETURNS with the retry-SUMMED wall
   time — a true number that cannot tell one 3 s stall from four 0.8 s ones (README §4).
3. **The bystander is read AFTER its thread is joined** — the one PING that spans the block
   is still in flight when the restore returns, so reading inside the block reports the
   last COMPLETED probe and understates the outage by everything (README §4).
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import uuid

import redis

#: Not a bound on correctness — a bound on a HUNG engine. The 90 000-node vector-index
#: restore in the #7846 record outlived a 10 s client read bound and surfaced at 53.3 s, so
#: a client timeout anything near that would convert a measurement into a socket error.
CLIENT_TIMEOUT_S = 1800.0

#: Above any block this harness is likely to see, per README §3a: one probe, one number.
BYSTANDER_TIMEOUT_S = 1800.0

#: The index build is a full pass over the restored graph. The #7846 record's re-create
#: path used 180 s at 20 000 nodes; a fulltext index has to tokenise every field and build
#: an inverted index, so this is deliberately generous and the harness PRINTS what it
#: waited for rather than folding it into a single figure.
INDEX_READY_BOUND_S = 1800.0

#: Payload shape. A realistic Point carries prose plus flat identifier tokens
#: (``sparse.POINT_INDEX_TEXT_FIELDS`` — ``content``/``search_keys``/``quote``), and
#: ``flatten_search_keys_prop`` (``projection/entities.py``) is load-bearing here: a
#: LIST-valued ``search_keys`` is NOT indexed by FalkorDB's fulltext index, so writing a
#: list would produce an index populated with half the text and a run that understates
#: the thing it claims to measure.
_WORDS = ["the", "river", "audit", "found", "three", "discrepancies", "in", "the", "ledger", "and", "the", "custodian", "agreed", "to", "reconcile", "them", "before", "the", "quarter", "closed", "while", "the", "board", "reviewed", "the", "schedule", "of", "payments", "and", "the", "auditor", "noted", "that", "the", "evidence", "was", "consistent", "with", "the", "claim"]
SEARCH_KEY_TOKENS = 4
BATCH = 500


# ── engine plumbing ──────────────────────────────────────────────────────────

def _rows(client, graph, cypher):
    out = client.execute_command("GRAPH.QUERY", graph, cypher)
    return out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else []


def _stat(cell, key):
    """Read a ``db.indexes()`` stats field BY KEY, never positionally.

    The stats cell arrives as a dict on some client/engine combinations and as
    FalkorDB's own map rendering (``"{numDocuments: 50, ...}"``) on others — a positional
    read is how a version bump turns this into a silent no-op, and a silent no-op here is
    a populated-index gate that always passes.
    """
    if isinstance(cell, dict):
        value = cell.get(key)
        return int(value) if value is not None else None
    text = cell if isinstance(cell, str) else (
        cell.decode() if isinstance(cell, (bytes, bytearray)) else str(cell)
    )
    match = re.search(rf"\b{re.escape(key)}:\s*(-?\d+)", text)
    return int(match.group(1)) if match else None


def _index_rows(client, graph):
    """Every ``CALL db.indexes()`` row for ``graph``, normalised to dicts."""
    rows = _rows(client, graph, "CALL db.indexes()")
    out = []
    for row in rows or []:
        if not isinstance(row, list):
            continue
        cells = [(c.decode() if isinstance(c, (bytes, bytearray)) else c) for c in row]
        stats = cells[8] if len(cells) > 8 else None
        types = str(cells[2]) if len(cells) > 2 else ""
        out.append({
            "label": cells[0],
            "fields": cells[1],
            "types": types,
            "entity": cells[6] if len(cells) > 6 else None,
            "status": cells[7] if len(cells) > 7 else None,
            "stats_raw": stats,
            "numDocuments": _stat(stats, "numDocuments"),
            "numTerms": _stat(stats, "numTerms"),
            "invertedSize": _stat(stats, "invertedSize"),
            "docTableSize": _stat(stats, "docTableSize"),
            "totalMSRun": _stat(stats, "totalMSRun"),
        })
    return out


def _index_of(client, graph, label, kind):
    """The single catalogue row for ``label`` whose TYPE map names ``kind``."""
    for row in _index_rows(client, graph):
        if row["label"] == label and kind in row["types"]:
            return row
    return None


def await_index(client, graph, label, kind, expected_docs, bound_s=INDEX_READY_BOUND_S):
    """Block until the index is ``OPERATIONAL`` and reports ``expected_docs`` documents.

    A timeout is a REFUSAL, never a proceed: a partially built index understates the leg
    and an understated leg is indistinguishable from a real one in the artifact.
    """
    started = time.perf_counter()
    row = None
    last = None
    while time.perf_counter() - started < bound_s:
        row = _index_of(client, graph, label, kind)
        if row is not None:
            last = (row["status"], row["numDocuments"])
            if row["status"] == "OPERATIONAL" and (row["numDocuments"] or 0) >= expected_docs:
                return row, time.perf_counter() - started
        time.sleep(0.25)
    _invalid_harness(
        f"the {kind} index on :{label} did not reach OPERATIONAL with "
        f"{expected_docs} documents within {bound_s}s (last: {last})"
    )


# ── the graph ───────────────────────────────────────────────────────────────

def doc_text(i):
    """A deterministic per-document text: shared vocabulary + a unique token.

    Shared vocabulary is what gives the inverted index real terms and real postings;
    a unique token per document is what makes a missing document detectable by query.
    """
    start = (i * 7) % (len(_WORDS) - 12)
    phrase = " ".join(_WORDS[start:start + 12])
    return f"{phrase} docmark{i}"


def build(client, graph, nodes):
    """``nodes`` ``:Doc:Num`` rows carrying prose, flat search keys and a numeric score.

    Written through the ``falkordb`` writer rather than the raw client: parameterised
    Cypher is a writer-client feature (``GRAPH.QUERY`` does not take a ``CYPHER``
    parameter tail on this engine — measured, it rejects it as a syntax error), and a
    literal-interpolated ``UNWIND`` of 90 000 rows would be a different measurement
    entirely. The DUMP/RESTORE path below still uses the RAW client, because it must be
    binary-safe (``decode_responses=False``) — the #7846 record's first trap.
    """
    from falkordb import FalkorDB

    writer = FalkorDB(host="127.0.0.1", port=PORT).select_graph(graph)
    for start in range(0, nodes, BATCH):
        rows = [
            {"i": i, "content": doc_text(i),
             "keys": " ".join(f"sk{i}_{k}" for k in range(SEARCH_KEY_TOKENS)),
             "score": i}
            for i in range(start, min(start + BATCH, nodes))
        ]
        writer.query(
            "UNWIND $rows AS r CREATE (p:Doc:Num {id: r.i, content: r.content, "
            "search_keys: r.keys, score: r.score})",
            {"rows": rows},
        )


FULLTEXT_DDL = "CREATE FULLTEXT INDEX FOR (n:Doc) ON (n.content, n.search_keys)"
RANGE_DDL = "CREATE INDEX FOR (n:Num) ON (n.score)"


def create_index(client, graph, kind):
    """Create the index under test. The DDL form first, the procedure as the fallback.

    ``projection/__init__.py`` records why: the historical multi-field PROCEDURE
    (``db.idx.fulltext.createNodeIndex``) is REJECTED by graph module 6.0.0 —
    "Received 3 arguments ... expected at most 1" — while the Cypher-native DDL is
    accepted on 4.16.7 through 6.0.0. A harness that only knew the procedure would leave
    the index silently absent on half the engines, which is #7846 §1 all over again.
    """
    forms = {
        "FULLTEXT": (FULLTEXT_DDL,
                     'CALL db.idx.fulltext.createNodeIndex("Doc", "content", "search_keys")'),
        "RANGE": (RANGE_DDL, None),
    }[kind]
    errors = []
    for form in forms:
        if form is None:
            continue
        try:
            _rows(client, graph, form)
            return form
        except Exception as exc:
            errors.append(f"{form}: {exc}")
    raise RuntimeError(f"no supported {kind} index creation form: " + "; ".join(errors))


def drop_index(client, graph, kind):
    """Drop the index under test, by the form that engine actually registers."""
    if kind == "FULLTEXT":
        _rows(client, graph, 'CALL db.idx.fulltext.drop("Doc")')
    else:
        _rows(client, graph, "DROP INDEX ON :Num(score)")


# ── probes ──────────────────────────────────────────────────────────────────

def fts_hits(client, graph):
    """Rows returned by a fulltext query — the H3 detector.

    A restore that drops the index DATA but keeps its DEFINITION reports ``OPERATIONAL``
    and answers with nothing. Presence is not population; this asks the index a question.
    """
    return len(_rows(client, graph, "CALL db.idx.fulltext.queryNodes('Doc', 'river')"))


def range_hits(client, graph, nodes):
    """Rows in a range window — the same detector for the range index."""
    rows = _rows(
        client, graph,
        f"MATCH (n:Num) WHERE n.score >= 0 AND n.score <= {max(nodes - 1, 0)} "
        "RETURN count(n)",
    )
    return rows[0][0] if rows else 0


def node_count(client, graph):
    rows = _rows(client, graph, "MATCH (n) RETURN count(n)")
    return rows[0][0] if rows else None


class Bystander:
    """A ``PING`` poller on its OWN connection — the instance's serving-ness, measured.

    ``socket_timeout`` is deliberately far ABOVE the longest block this harness can
    measure, so ONE probe spans the block and its latency IS the outage (README §4).
    ``socket_connect_timeout`` stays short: a REFUSED port is a different diagnosis
    (the process died, #8270) and must not be confused with a block.
    """

    def __init__(self, port):
        self.client = redis.Redis(
            host="127.0.0.1", port=port, decode_responses=True,
            socket_timeout=BYSTANDER_TIMEOUT_S, socket_connect_timeout=5.0,
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
        self._thread.join(timeout=BYSTANDER_TIMEOUT_S + 30)
        self.client.close()
        return False

    @property
    def worst_ms(self):
        return max(self.latencies) if self.latencies else 0.0

    def summary(self):
        return {
            "bystander_worst_ping_ms": round(self.worst_ms, 1),
            "bystander_failed_pings": self.failures,
            "bystander_samples": len(self.latencies),
        }


# ── the cycle ───────────────────────────────────────────────────────────────

def _timed(fn, *args):
    started = time.perf_counter()
    value = fn(*args)
    return value, time.perf_counter() - started


def cycle(client, graph, arm, kind, nodes, ready_docs):
    """DUMP -> DEL -> RESTORE (-> re-create when dropped), timed step by step.

    The bystander spans the WHOLE cycle, in both arms — the §7 sentence is about a window
    of silence around the restore, not about the ``RESTORE`` command in isolation, and a
    cost that merely MOVED (e.g. into the re-create) would be invisible if it were only
    sampled around the restore.
    """
    result = {
        "arm": arm, "nodes": nodes, "index": kind,
        "thread_count": _thread_count(client), "engine": ENGINE,
    }
    if kind and arm.endswith("-dropped"):
        # The drop happens BEFORE the bystander window: it is a separate step whose cost
        # is reported, but the bystander is measuring the DUMP/DEL/RESTORE the window
        # performs. Including the drop would credit the arm with a cost the product does
        # not pay at restore time.
        _, result["drop_s"] = _timed(drop_index, client, graph, kind)
        row = _index_of(client, graph, "Doc" if kind == "FULLTEXT" else "Num", kind)
        result["index_present_after_drop"] = row is not None

    with Bystander(PORT) as bystander:
        time.sleep(0.2)
        cycle_started = time.perf_counter()
        payload, result["dump_s"] = _timed(client.execute_command, "DUMP", graph)
        result["dump_mb"] = round(len(payload) / (1024 * 1024), 2)
        _, result["del_s"] = _timed(client.execute_command, "DEL", graph)
        _, result["restore_s"] = _timed(
            client.execute_command, "RESTORE", graph, 0, payload, "REPLACE"
        )
        result["cycle_core_s"] = round(
            result["dump_s"] + result["del_s"] + result["restore_s"], 3
        )
        if kind and arm.endswith("-dropped"):
            _, result["recreate_s"] = _timed(create_index, client, graph, kind)
            row, result["ready_wait_s"] = await_index(
                client, graph, "Doc" if kind == "FULLTEXT" else "Num", kind, ready_docs
            )
            result["recreate_s"] = round(result["recreate_s"], 3)
            result["ready_wait_s"] = round(result["ready_wait_s"], 3)
        # The WHOLE cycle, wall clock: DUMP through the last step this arm needs. Timed
        # here, not summed from the steps, so inter-step overhead cannot go missing.
        result["cycle_s"] = round(time.perf_counter() - cycle_started, 3)
    # ⚠️ read the bystander AFTER its thread is joined (§4).
    result.update(bystander.summary())
    if not bystander.latencies:
        # ⛔ A VACUOUS PASS, refused. `worst_ms` returns 0.0 for an empty sample set, so an
        # engine that dropped mid-run would be recorded as "worst PING 0.0 ms" — i.e. as
        # NO BLOCK — which is the opposite of what happened. This harness's own doctrine is
        # that a missing reading is a REFUSAL, never a proceed (``await_index`` says the
        # same thing about a timeout). The committed runs all carry >= 11 samples.
        _invalid_harness(
            f"the bystander took NO successful samples in arm {arm!r} "
            f"({bystander.failures} failed pings) — an empty sample set would report "
            f"a 0.0 ms worst latency, i.e. no block, which is a false pass"
        )

    result["nodes_after"] = node_count(client, graph)
    if kind:
        label = "Doc" if kind == "FULLTEXT" else "Num"
        row = _index_of(client, graph, label, kind)
        result["index_after"] = row["status"] if row else "ABSENT"
        result["index_documents_after"] = row["numDocuments"] if row else None
        result["index_terms_after"] = row["numTerms"] if row else None
        result["index_inverted_bytes_after"] = row["invertedSize"] if row else None
    if kind == "FULLTEXT":
        result["fts_hits_after"] = fts_hits(client, graph)
    elif kind == "RANGE":
        result["range_rows_after"] = range_hits(client, graph, nodes)
    return result


# ── harness ─────────────────────────────────────────────────────────────────

ENGINE: dict = {}
PORT = 0


def _thread_count(client):
    return ENGINE.get("thread_count")


def _invalid_harness(message):
    print(f"INVALID HARNESS: {message}", file=sys.stderr)
    raise SystemExit(2)


def _text(value):
    """Bytes-to-str for a raw (``decode_responses=False``) client, recursively.

    Recursive because ``MODULE LIST`` yields NESTED values: the graph module's ``args``
    is a list (``['THREAD_COUNT', 8, 'TIMEOUT_MAX', 30000]``), so a flat bytes->str map
    leaves a bytes element inside a list and ``json.dumps`` then dies on the header —
    which is the one line that must never fail, since it is what names the engine.
    """
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    if isinstance(value, dict):
        return {_text(k): _text(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_text(v) for v in value]
    return value


def engine_header(client):
    """A self-describing first line, so a committed log names its own engine.

    #7846's README has to say its ``drop-first-*.log`` files are "operator-asserted in
    this document and NOT recorded in the commit" for exactly this reason.
    """
    info = client.execute_command("INFO", "server")
    version = None
    if isinstance(info, dict):
        version = _text(info.get("redis_version"))
    else:
        text = info.decode() if isinstance(info, (bytes, bytearray)) else str(info)
        for line in text.splitlines():
            if line.startswith("redis_version:"):
                version = line.split(":", 1)[1].strip()
    modules = client.execute_command("MODULE", "LIST")
    graph = {}
    for module in modules or []:
        if isinstance(module, dict):
            tokens = list(module.items())
        else:
            tokens = list(zip(
                [module[i] for i in range(0, len(module), 2)],
                [module[i] for i in range(1, len(module), 2)],
                strict=False,
            ))
        flat = {_text(key): _text(value) for key, value in tokens}
        if flat.get("name") == "graph":
            graph = flat
    save = client.execute_command("CONFIG", "GET", "save")
    appendonly = client.execute_command("CONFIG", "GET", "appendonly")
    return {
        "start": {
            "redis_version": version,
            "graph_module": graph.get("ver"),
            "graph_path": graph.get("path"),
            "thread_count": graph.get("THREAD_COUNT"),
            "graph_module_args": graph.get("args"),
            "persistence": {
                "save": _text(save[1]) if len(save) > 1 else None,
                "appendonly": _text(appendonly[1]) if len(appendonly) > 1 else None,
            },
            "port": PORT,
        }
    }


def prepare(client, nodes, tag):
    """A unique graph built and gated on a POPULATED index (or none, for the floor arm)."""
    graph = f"t_12766_{tag}_{uuid.uuid4().hex[:10]}"
    build(client, graph, nodes)
    return graph


def _cleanup(client, *names):
    for name in names:
        try:
            client.execute_command("DEL", name)
            client.execute_command("DEL", f"telemetry{{{name}}}")
        except Exception:
            pass


def main(argv):
    global PORT, ENGINE
    PORT = int(argv[1])
    nodes = int(argv[2])
    arms = ["noindex", "fulltext-kept", "fulltext-dropped", "range-kept", "range-dropped"]
    if "--arms" in argv:
        arms = argv[argv.index("--arms") + 1].split(",")

    client = redis.Redis(
        host="127.0.0.1", port=PORT, decode_responses=False,
        socket_timeout=CLIENT_TIMEOUT_S, socket_connect_timeout=10.0,
    )
    header = engine_header(client)
    ENGINE.update(header["start"])
    print(json.dumps(header), flush=True)
    if ENGINE.get("graph_module") is None:
        print("no graph module on this server", file=sys.stderr)
        return 2

    results = []
    for arm in arms:
        kind = None
        if arm.startswith("fulltext"):
            kind = "FULLTEXT"
        elif arm.startswith("range"):
            kind = "RANGE"
        graph = prepare(client, nodes, arm.replace("-", "_"))
        try:
            if kind:
                index_ddl = create_index(client, graph, kind)
                label = "Doc" if kind == "FULLTEXT" else "Num"
                row, ready_s = await_index(client, graph, label, kind, nodes)
                # ⛔ THE GATE (#7846 §1): an empty index measures nothing.
                if (row["numDocuments"] or 0) < nodes:
                    _invalid_harness(
                        f"the {kind} index on :{label} is not POPULATED "
                        f"({row['numDocuments']} documents of {nodes})"
                    )
                if kind == "FULLTEXT" and fts_hits(client, graph) == 0:
                    _invalid_harness(
                        "the fulltext index answers no hits BEFORE the restore — "
                        "the index is present but empty"
                    )
                populated = {
                    "pre_documents": row["numDocuments"],
                    "pre_terms": row["numTerms"],
                    "pre_inverted_bytes": row["invertedSize"],
                    "pre_ready_s": round(ready_s, 2),
                }
                if kind == "FULLTEXT":
                    populated["pre_fts_hits"] = fts_hits(client, graph)
                else:
                    populated["pre_range_rows"] = range_hits(client, graph, nodes)
            else:
                populated = {}
            result = cycle(client, graph, arm, kind, nodes, nodes)
            result["populated_before"] = populated
            if kind:
                result["index_ddl"] = index_ddl
        finally:
            _cleanup(client, graph)
        results.append(result)
        print(json.dumps(result), flush=True)

    _summary(results, nodes)
    return 0


def _summary(results, nodes):
    by_arm = {r["arm"]: r for r in results}
    floor = by_arm.get("noindex")
    print(f"\n=== {nodes} nodes · engine graph module {ENGINE.get('graph_module')} ===")
    header = (
        f"{'arm':18s} {'DUMP':>8s} {'DEL':>7s} {'RESTORE':>9s} {'recreate':>9s} "
        f"{'ready':>8s} {'cycle':>9s} {'worst PING':>11s} {'docs after':>11s} {'hits':>6s}"
    )
    print(header)
    print("-" * len(header))
    for arm in ("noindex", "fulltext-kept", "fulltext-dropped", "range-kept", "range-dropped"):
        r = by_arm.get(arm)
        if not r:
            continue
        hits = r.get("fts_hits_after", r.get("range_rows_after", "-"))
        print(
            f"{arm:18s} {r['dump_s']:7.3f}s {r['del_s']:6.3f}s {r['restore_s']:8.3f}s "
            f"{r.get('recreate_s', 0.0):8.3f}s {r.get('ready_wait_s', 0.0):7.2f}s "
            f"{r['cycle_s']:8.3f}s {r['bystander_worst_ping_ms']:10.1f}ms "
            f"{r.get('index_documents_after', '-')!s:>11s} {hits!s:>6s}"
        )
    print()
    for arm in ("fulltext-kept", "fulltext-dropped", "range-kept", "range-dropped"):
        r = by_arm.get(arm)
        if not r or not floor:
            continue
        print(
            f"{arm}: restore {r['restore_s']:.3f}s vs floor {floor['restore_s']:.3f}s "
            f"({r['restore_s'] / max(floor['restore_s'], 1e-9):.1f}x); "
            f"worst bystander {r['bystander_worst_ping_ms']:.1f}ms vs floor "
            f"{floor['bystander_worst_ping_ms']:.1f}ms; "
            f"index after {r.get('index_after')} "
            f"({r.get('index_documents_after')} docs, {r.get('index_terms_after')} terms)"
        )


if __name__ == "__main__":
    sys.exit(main(sys.argv))
