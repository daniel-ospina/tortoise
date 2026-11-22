"""#14014 — M4: rebuild peak RSS at the REAL window size, and the kept-index restore.

``docs/architecture/SEARCH-ARCHITECTURE.md`` §9's **M4** asks two things, and §7 calls the
first of them *"the #1 production risk"*:

1. **rebuild peak memory and latency per tenant at the real window size**, against the
   **≈6×** headroom §7 reserves — whose lower bound is the *"third-party measurement —
   citation owed"* claim that re-materialising into FalkorDB *"peaks at 4.9–5.9× the
   resting size"*; and
2. **whether a KEPT ``db.idx.fulltext`` (or range) index blocks a restore** the way the
   removed vector index did.

The second leg was measured for the ENGINE at 20 000 / 90 000 nodes by
``docs/evidence/12766-fulltext-restore/`` (RESTORE 0.405 s / 1.585 s kept against
0.011 s / 0.032 s dropped; the bystander's ``PING`` ≈ the restore's duration). This
harness **re-measures it at the window size** instead of at a node count, and it measures
the blast radius with a real READ against a SECOND graph (another tenant's data) rather
than with ``PING`` — the question window arming (`#13103`) needs is *"does tenant A's
re-materialisation block tenant B?"*, and a ``PING`` cannot answer that.

WHAT THE WINDOW SIZE IS, because the whole measurement is relative to it
=======================================================================
§8 derives it from the owner's 30 %-of-price rule: **solo ≈77 MB, pro ≈102 MB,
team ≈615 MB**, and each of those is a ``GRAPH.MEMORY USAGE`` figure — the same
instrument §8 says the ~150 MB complete-graph estimate came from. So this harness
**sizes the graph by ``total_graph_sz_mb``**, building in batches until it reaches the
target, and reports the node count that took. The fulltext index is created EARLY (after
the first batch) so that every subsequent write is indexed as the engine really does —
otherwise ``total_graph_sz_mb`` under-counts the window by the whole ``indices_sz_mb``,
which at the sizes here is most of it.

⚠️ §8 already records that ``GRAPH.MEMORY USAGE`` is a SAMPLING instrument with known
omissions (blind to the HNSW ``vectorset``; excludes per-graph/Redis-key overhead). This
record therefore reports **both** bases side by side — the engine's own
``total_graph_sz_mb`` and the process's RSS increment — because they are not the same
number and the headroom question is about the second one.

WHAT IS MEASURED, AND HOW "PEAK" IS DEFINED
===========================================
The engine process's ``VmRSS`` is sampled every ``SAMPLE_INTERVAL_S`` from the **host**
(``/proc/<pid>/status``, one small read per sample, no ``docker exec`` in the hot path —
a fork per sample would both miss peaks and perturb them). Three readings anchor every
figure, all in one arm on a **freshly restarted** container:

    base_rss_mb    the engine with the tenant-B bystander graph ONLY (no window)
    rest_rss_mb    the window resident and idle, fulltext index OPERATIONAL
    peak_*_rss_mb  the MAX sampled during that phase (build / restore cycle)

and the ratios the question is decided on:

    rest_ratio      = (rest - base)   / window_mb   -- what a resident window costs
    build_peak_ratio= (build_peak - base) / window_mb
    peak_ratio      = (cycle_peak - base) / window_mb  -- the headroom the rebuild demands
    transient_mb    =  cycle_peak - pre_cycle          -- the ADDED bytes during the cycle

``peak_ratio`` ≤ **6** is the doc's reserve. ``rest_ratio`` is reported because §8's cost
model assumes a window costs its ``GRAPH.MEMORY USAGE``; if that ratio is well above 1,
the assumption — not the window — is what the number is about. ⚠️ It is also why
``--marginal`` exists: ``rest_ratio`` is measured against an EMPTY instance, so it
includes fixed per-process arenas a second tenant would not pay again. The marginal
reading (a SECOND window on the same instance) is the per-tenant figure a multi-tenant
cost model wants, and both are printed rather than one being chosen.

ARMS
====
    fulltext-kept      the index present across DUMP -> DEL -> RESTORE       (§7's question)
    fulltext-dropped   dropped BEFORE the dump, re-created after             (the mitigation)

The cycle is ``DUMP -> DEL -> RESTORE``, and the ``DEL`` is NOT optional on this engine
pin: ``RESTORE ... REPLACE`` of a FULLTEXT-indexed payload into the still-existing indexed
graph **SEGFAULTS the instance** — container ``Exited (139)``, the engine's own report
naming ``falkordb.so(Index_SetLanguage+0x14)`` — at **200 nodes**, in every attempt this
lane made, on ``falkordb/falkordb-server:v4.20.6`` (graph module **42006**, the CI pin).
That is the root `#8270` records for a payload restored under a *different key*; reached
through ``REPLACE`` instead of a rename, so what the crash tracks is the **coexisting
source**, not the key name. The ``DEL``-first cycle is the safe variant, and the
"RESTORE validates before it creates the key, so never DEL" shape that
``tools/ram_window_census.py`` chose for PRODUCTION is unavailable to a measurement on
this pin until #8270 is fixed.

REFUSALS (exit 2), because a missing reading is a refusal and never a proceed
=================================================================================
``_invalid_harness`` fires when the reading would say nothing about the thing measured:

* the fulltext index never reached ``OPERATIONAL`` with ``numDocuments`` ≥ node count —
  #7846's first harness measured an EMPTY index and measured nothing;
* a live fulltext query returns no hits (an index whose DATA was lost across the restore);
* the graph does not come back with the same node count;
* the engine DIED during an arm (the #8270 crash class) — no reading of that arm exists;
* a bystander arm took zero successful reads (``max()`` of an empty list would read as
  *"no block"*, the opposite of what happened);
* the RSS sampler took no samples, or the engine process could not be resolved;
* the window was not reached (``total_graph_sz_mb`` below target).

Usage::

    uv run python tools/test_lane.py up                 # a private lane container
    uv run python docs/evidence/14014-m4-rebuild-peak/rebuild_peak.py <port> \
        --window-mb 77 [--arms fulltext-kept,fulltext-dropped] [--marginal]

Exit code 2 = INVALID HARNESS (see above). 0 = every arm measured and gated.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import threading
import time

import redis

#: Above any block this harness can measure, so ONE probe spans the block and its latency
#: IS the outage (#7846 §3a; #12766 §4 — a shorter timeout makes redis-py RETRY a timed-out
#: call internally and return the retry-SUMMED wall time, which cannot tell one 1.5 s stall
#: from two 0.75 s ones).
CLIENT_TIMEOUT_S = 1800.0

#: RSS sample interval. 20 ms is ~50 samples/s: at the window sizes measured here the
#: restore block is hundreds of ms to seconds, so the peak it resolves is far inside it,
#: and the sampler costs one small file read per sample (no fork).
SAMPLE_INTERVAL_S = 0.02

#: The index build is a full pass over the restored graph (#12766 measured 1.51 s at
#: 90 000 nodes). Deliberately generous; what is WAITED is printed, not folded away.
INDEX_READY_BOUND_S = 1800.0

#: Payload shape — a `:Point` carrying what the search leg indexes and what a claim
#: carries: prose (``content``), flat identifier tokens (``search_keys``), the quote, and
#: the belief state. ⚠️ ``search_keys`` and ``quote`` are written as FLAT strings, because
#: FalkorDB's fulltext index does not index array-valued properties
#: (``flatten_search_keys_prop``, ``projection/entities.py``): a list-valued ``search_keys``
#: would leave half the text unindexed and understate the leg being measured.
_WORDS = [
    "the", "river", "audit", "found", "three", "discrepancies", "in", "the",
    "ledger", "and", "the", "custodian", "agreed", "to", "reconcile", "them",
    "before", "the", "quarter", "closed", "while", "the", "board", "reviewed",
    "the", "schedule", "of", "payments", "and", "the", "auditor", "noted",
    "that", "the", "evidence", "was", "consistent", "with", "the", "claim",
]
SEARCH_KEY_TOKENS = 4
BATCH = 2000

#: The three window targets §8 derives (``GRAPH.MEMORY USAGE`` MB). Named here so the
#: number a run is sized against is the doc's, not a convenience.
WINDOW_TARGETS_MB = {"solo": 77, "pro": 102, "team": 615}

FULLTEXT_LABEL = "Point"
FULLTEXT_DDL = (
    f"CREATE FULLTEXT INDEX FOR (n:{FULLTEXT_LABEL}) ON (n.content, n.search_keys, n.quote)"
)

PORT = 0


# ── refusals ────────────────────────────────────────────────────────────────

def _invalid_harness(message):
    """Exit 2 — INVALID HARNESS. ``SystemExit(2)`` and not ``assert`` (an assert is
    stripped under ``python -O`` and exits 1, so a consumer keying on the documented code
    would get the case it was guarding against)."""
    print(f"INVALID HARNESS: {message}", file=sys.stderr)
    raise SystemExit(2)


# ── engine plumbing ─────────────────────────────────────────────────────────

def _rows(client, graph, cypher):
    out = client.execute_command("GRAPH.QUERY", graph, cypher)
    return out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else []


def _stat(cell, key):
    """Read a ``db.indexes()`` stats field BY KEY, never positionally: the stats cell
    arrives as a dict on some client/engine combinations and as FalkorDB's own map
    rendering on others, and a positional read is how a version bump turns a
    populated-index gate into a silent no-op that always passes."""
    if isinstance(cell, dict):
        value = cell.get(key)
        return int(value) if value is not None else None
    text = cell if isinstance(cell, str) else (
        cell.decode() if isinstance(cell, (bytes, bytearray)) else str(cell)
    )
    match = re.search(rf"\b{re.escape(key)}:\s*(-?\d+)", text)
    return int(match.group(1)) if match else None


def _index_of(client, graph, label=FULLTEXT_LABEL):
    """The catalogue row for the FULLTEXT index on ``label``, normalised."""
    for row in _rows(client, graph, "CALL db.indexes()") or []:
        if not isinstance(row, list) or len(row) < 9:
            continue
        cells = [(c.decode() if isinstance(c, (bytes, bytearray)) else c) for c in row]
        if cells[0] != label or "FULLTEXT" not in str(cells[2]):
            continue
        return {
            "status": cells[7],
            "numDocuments": _stat(cells[8], "numDocuments"),
            "numTerms": _stat(cells[8], "numTerms"),
            "invertedSize": _stat(cells[8], "invertedSize"),
            "totalMSRun": _stat(cells[8], "totalMSRun"),
        }
    return None


def await_index(client, graph, label, expected_docs, bound_s=INDEX_READY_BOUND_S):
    """Block until the index is ``OPERATIONAL`` with ``expected_docs`` documents.

    A timeout is a REFUSAL, never a proceed — a partially built index understates the leg
    and an understated leg is indistinguishable from a real one in the artifact."""
    started = time.perf_counter()
    last = None
    while time.perf_counter() - started < bound_s:
        row = _index_of(client, graph, label)
        if row is not None:
            last = (row["status"], row["numDocuments"])
            if row["status"] == "OPERATIONAL" and (row["numDocuments"] or 0) >= expected_docs:
                return row, time.perf_counter() - started
        time.sleep(0.25)
    _invalid_harness(
        f"the FULLTEXT index on :{label} did not reach OPERATIONAL with {expected_docs} "
        f"documents within {bound_s}s (last: {last})"
    )


def fts_hits(client, graph):
    """Rows returned by a fulltext query — presence is not population."""
    return len(_rows(client, graph,
                     f"CALL db.idx.fulltext.queryNodes('{FULLTEXT_LABEL}', 'river')"))


def node_count(client, graph):
    rows = _rows(client, graph, "MATCH (n) RETURN count(n)")
    return rows[0][0] if rows else None


def graph_mem(client, graph):
    """``GRAPH.MEMORY USAGE`` — the instrument §8's window targets are denominated in."""
    try:
        raw = client.execute_command("GRAPH.MEMORY", "USAGE", graph)
    except Exception:
        return {}
    out = {}
    for i in range(0, len(raw) - 1, 2):
        key, value = raw[i], raw[i + 1]
        if isinstance(value, (int, float)):
            out[key] = value
    return out


# ── the engine process, and its RSS ─────────────────────────────────────────

def _sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def resolve_engine(port):
    """The host pid of the engine serving ``port`` — resolved, never guessed.

    Seventeen lane containers run on this box, EVERY ONE with an in-container
    ``redis-server *:6379`` in its cmdline, so a ``pgrep`` on the cmdline cannot tell them
    apart (exactly the first-match artifact #10363 records). The published port
    disambiguates: ``docker ps`` maps it to the container, ``docker top`` maps the
    container to the host pid.
    """
    out = _sh(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    if out.returncode != 0:
        _invalid_harness(f"docker ps failed: {out.stderr.strip()[:300]}")
    needle = f":{port}->6379"
    names = [ln.split("\t")[0] for ln in out.stdout.splitlines() if needle in ln]
    if len(names) != 1:
        _invalid_harness(
            f"no single docker container publishes {needle} (candidates: {names}) — the "
            f"engine's RSS cannot be attributed, and peak RSS is this measurement"
        )
    name = names[0]
    top = _sh(["docker", "top", name, "-eo", "pid,args"])
    if top.returncode != 0:
        _invalid_harness(f"docker top {name} failed: {top.stderr.strip()[:300]}")
    pids = []
    for line in top.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].startswith("redis-server"):
            pids.append(int(parts[0]))
    if len(pids) != 1:
        _invalid_harness(f"could not attribute ONE redis-server pid in {name} (got {pids})")
    return name, pids[0]


class RssSampler:
    """Samples ``/proc/<pid>/status`` ``VmRSS`` from the HOST on its own thread."""

    def __init__(self, pid):
        self.pid = pid
        self.path = f"/proc/{pid}/status"
        self.samples: list[float] = []
        self.failures = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _read(self):
        with open(self.path, encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        raise RuntimeError("VmRSS absent")

    def _run(self):
        while not self._stop.is_set():
            try:
                self.samples.append(self._read())
            except Exception:
                self.failures += 1
            time.sleep(SAMPLE_INTERVAL_S)

    def __enter__(self):
        self._thread.start()
        time.sleep(0.2)
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=30)
        return False

    def mark(self):
        """Sample count so far — phase boundaries are read by index, not by timestamp."""
        return len(self.samples)

    def slice_max(self, start, end=None):
        window = self.samples[start:end]
        return max(window) if window else None

    def slice_mean(self, start, end=None):
        window = self.samples[start:end]
        return statistics.fmean(window) if window else None


def mean_rss(pid, seconds=1.0):
    with RssSampler(pid) as sampler:
        time.sleep(seconds)
    if not sampler.samples:
        _invalid_harness("the RSS sampler took NO samples while measuring a resting reading")
    return statistics.fmean(sampler.samples)


# ── the tenant B bystander: another tenant's READ, not a PING ────────────────

class TenantB:
    """A second graph on the same instance, read on its own connection throughout.

    ``PING`` measures the instance's serving-ness; **this** measures what the window's
    question actually asks — whether one tenant's re-materialisation blocks ANOTHER
    tenant's read. The read is an anchored lookup, so its latency is the engine's.
    """

    GRAPH = "m4_tenant_b"
    SIZE = 200

    def __init__(self, port):
        self.client = redis.Redis(
            host="127.0.0.1", port=port, decode_responses=True,
            socket_timeout=CLIENT_TIMEOUT_S, socket_connect_timeout=5.0,
        )
        self.latencies: list[float] = []
        self.failures = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def seed(cls, client):
        """Give tenant B real data to read, so the probe is not a query on a miss.

        Written through the ``falkordb`` writer: ``GRAPH.QUERY`` takes no ``CYPHER``
        parameter tail on this engine, and an inline map literal spelled as JSON is
        REJECTED (``Invalid input '\"'`` — Cypher map keys are bare identifiers).
        """
        from falkordb import FalkorDB

        writer = FalkorDB(host="127.0.0.1", port=PORT).select_graph(cls.GRAPH)
        for start in range(0, cls.SIZE, 100):
            rows = [{"i": i, "content": doc_text(i),
                     "keys": " ".join(f"b{i}_{k}" for k in range(SEARCH_KEY_TOKENS)),
                     "quote": f"quote {i}"}
                    for i in range(start, min(start + 100, cls.SIZE))]
            writer.query(
                "UNWIND $rows AS r CREATE (p:Point {id: r.i, content: r.content, "
                "search_keys: r.keys, quote: r.quote})",
                {"rows": rows},
            )
        _rows(client, cls.GRAPH, FULLTEXT_DDL)
        await_index(client, cls.GRAPH, FULLTEXT_LABEL, cls.SIZE)

    def _run(self):
        i = 0
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                self.client.execute_command(
                    "GRAPH.QUERY", self.GRAPH,
                    f"MATCH (n:Point) WHERE n.id = {i % self.SIZE} RETURN n.id",
                )
            except Exception:
                self.failures += 1
            else:
                self.latencies.append((time.perf_counter() - started) * 1000)
            i += 1
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        time.sleep(0.3)
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=CLIENT_TIMEOUT_S + 30)
        self.client.close()
        return False

    def summary(self):
        return {
            "tenant_b_worst_read_ms": round(max(self.latencies), 1) if self.latencies else None,
            "tenant_b_samples": len(self.latencies),
            "tenant_b_failed_reads": self.failures,
        }


# ── the graph ───────────────────────────────────────────────────────────────

def doc_text(i):
    """Deterministic per-document prose: shared vocabulary (real terms, real postings)
    plus a unique token, so a missing document is detectable by query."""
    start = (i * 7) % (len(_WORDS) - 12)
    phrase = " ".join(_WORDS[start:start + 12])
    return f"{phrase} docmark{i}"


def build_window(client, graph, target_mb):
    """Build until ``total_graph_sz_mb`` reaches ``target_mb``; return what it took.

    The fulltext index is created after the FIRST batch and maintained by the engine
    through the rest of the ingest — which is how a real window is grown, and the only
    way ``total_graph_sz_mb`` accounts for the index at all (its ``indices_sz_mb`` share is
    most of the total at these sizes: 34 of 55 MB in this lane's smoke run).

    ⚠️ ``total_graph_sz_mb`` is reported in WHOLE MB by this engine, so the loop's
    resolution is one batch. The final node count and the final reading are both reported.
    """
    from falkordb import FalkorDB

    writer = FalkorDB(host="127.0.0.1", port=PORT).select_graph(graph)
    nodes = 0
    started = time.perf_counter()
    steps = []
    index_created = False
    while True:
        rows = [
            {"i": i, "content": doc_text(i),
             "keys": " ".join(f"sk{i}_{k}" for k in range(SEARCH_KEY_TOKENS)),
             "quote": f"quote text {i} consistent with the claim",
             "alpha": 1.0, "beta": 2.0,
             "created": "2026-11-21T00:00:00Z", "src": "s3"}
            for i in range(nodes, nodes + BATCH)
        ]
        writer.query(
            "UNWIND $rows AS r CREATE (p:Point {id: r.i, content: r.content, "
            "search_keys: r.keys, quote: r.quote, ep_alpha: r.alpha, ep_beta: r.beta, "
            "created_at: r.created, source: r.src})",
            {"rows": rows},
        )
        nodes += BATCH
        if not index_created:
            _rows(client, graph, FULLTEXT_DDL)
            index_created = True
        mem = graph_mem(client, graph)
        total = mem.get("total_graph_sz_mb", 0)
        steps.append({"nodes": nodes, "total_graph_sz_mb": total,
                      "indices_sz_mb": mem.get("indices_sz_mb")})
        if total >= target_mb:
            break
        if nodes > 12_000_000:  # a runaway guard, not a threshold
            _invalid_harness(
                f"12 000 000 nodes built and total_graph_sz_mb is still {total} against a "
                f"{target_mb} MB target — the sizing instrument is not what it is assumed to be"
            )
    return {
        "nodes": nodes,
        "build_s": round(time.perf_counter() - started, 2),
        "build_curve": steps[-3:],
    }


# ── the cycle ───────────────────────────────────────────────────────────────

def _timed(fn, *args):
    started = time.perf_counter()
    value = fn(*args)
    return value, time.perf_counter() - started


def cycle(client, raw, graph, rss, arm, nodes, base_rss, window_mb):
    """DUMP -> DEL -> RESTORE (-> re-create when dropped), with RSS + tenant B across it.

    The RSS and bystander windows span the WHOLE cycle in both arms: the §7 sentence is
    about a peak and about a window of silence around the restore, not about the
    ``RESTORE`` command in isolation — a cost that merely MOVED (into the re-create) would
    be invisible if only the restore were sampled.
    """
    result = {"arm": arm, "nodes": nodes, "window_mb": window_mb, "base_rss_mb": round(base_rss, 1)}
    if arm == "fulltext-dropped":
        # Dropped BEFORE the dump. Its cost is reported; the peak window measures the cycle
        # the window performs, and the drop is not part of that.
        _, result["drop_s"] = _timed(
            _rows, client, graph, f'CALL db.idx.fulltext.drop("{FULLTEXT_LABEL}")'
        )
        result["index_present_after_drop"] = _index_of(client, graph) is not None

    pre_rss = rss.slice_mean(rss.mark() - 25)
    tenant_b = TenantB(PORT)
    with tenant_b as bystander:
        time.sleep(0.2)
        cycle_started = time.perf_counter()
        start_mark = rss.mark()
        # ⚠️ BINARY-SAFE client for DUMP/RESTORE — the #7846 record's first trap: a
        # `decode_responses=True` client raises UnicodeDecodeError on the payload.
        payload, result["dump_s"] = _timed(raw.execute_command, "DUMP", graph)
        result["dump_mib"] = round(len(payload) / (1024 * 1024), 2)
        # ⛔ DEL before RESTORE, never `RESTORE ... REPLACE`: on this pin a REPLACE of an
        # indexed payload into the still-existing indexed key SEGFAULTS the instance
        # (#8270's root, reproduced here at 200 nodes — see the module docstring).
        _, result["del_s"] = _timed(raw.execute_command, "DEL", graph)
        _, result["restore_s"] = _timed(raw.execute_command, "RESTORE", graph, 0, payload)
        result["restore_done_mark"] = rss.mark()
        result["cycle_core_s"] = round(
            result["dump_s"] + result["del_s"] + result["restore_s"], 3
        )
        if arm == "fulltext-dropped":
            _, result["recreate_s"] = _timed(_rows, client, graph, FULLTEXT_DDL)
            _, result["ready_wait_s"] = await_index(client, graph, FULLTEXT_LABEL, nodes)
            result["recreate_s"] = round(result["recreate_s"], 3)
            result["ready_wait_s"] = round(result["ready_wait_s"], 3)
        result["cycle_s"] = round(time.perf_counter() - cycle_started, 3)
        end_mark = rss.mark()
        try:
            alive = raw.ping()
        except Exception:
            alive = False
        if not alive:
            _invalid_harness(
                f"the engine DIED during arm {arm!r} — no reading of that arm exists "
                f"(the #8270 crash class; the container's log carries the bug report)"
            )
    # ⚠️ read the bystander AFTER its thread is joined (#7846 §3a): the one probe that
    # spans the block is still in flight when the restore returns, so reading inside the
    # block reports the last COMPLETED probe and understates the outage by everything.
    result.update(bystander.summary())
    peak_cycle = rss.slice_max(start_mark, end_mark)
    peak_at_restore = rss.slice_max(start_mark, result["restore_done_mark"] + 1)
    result["rss_pre_cycle_mb"] = round(pre_rss, 1) if pre_rss else None
    result["rss_peak_cycle_mb"] = round(peak_cycle, 1) if peak_cycle else None
    result["rss_peak_at_restore_mb"] = round(peak_at_restore, 1) if peak_at_restore else None
    result["rss_samples_cycle"] = end_mark - start_mark
    result["transient_rss_mb"] = (
        round(peak_cycle - pre_rss, 1) if peak_cycle and pre_rss else None
    )
    result["peak_ratio_of_window"] = (
        round((peak_cycle - base_rss) / window_mb, 2) if peak_cycle else None
    )

    result["nodes_after"] = node_count(client, graph)
    row = _index_of(client, graph)
    result["index_after"] = row["status"] if row else "ABSENT"
    result["index_documents_after"] = row["numDocuments"] if row else None
    result["index_terms_after"] = row["numTerms"] if row else None
    result["index_inverted_bytes_after"] = row["invertedSize"] if row else None
    result["fts_hits_after"] = fts_hits(client, graph)

    # ⛔ POST-CYCLE REFUSALS — a missing reading is a refusal, never a proceed. Without
    # them an H3 run (index data lost across the restore) or a node-losing restore would
    # exit 0 and print a table, leaving "the graph came back whole" to the reader.
    if result["nodes_after"] != nodes:
        _invalid_harness(
            f"arm {arm!r} came back with {result['nodes_after']} nodes of {nodes} — the "
            f"payload did not return whole, so the figures are not about an index rebuild"
        )
    if result["fts_hits_after"] == 0:
        _invalid_harness(
            f"arm {arm!r} restored a fulltext index that answers NO hits — the index is "
            f"not populated, which is #7846 §1's measured-nothing shape"
        )
    return result


# ── the run ─────────────────────────────────────────────────────────────────

def engine_header(client, container):
    info = client.info("server")
    modules = []
    try:
        for entry in client.execute_command("MODULE", "LIST"):
            pairs = dict(zip(entry[::2], entry[1::2]))
            name = pairs.get("name")
            modules.append({
                "name": name.decode() if isinstance(name, bytes) else name,
                "ver": pairs.get("ver"),
                "args": str(pairs.get("args"))[:120],
            })
    except Exception:
        pass
    return {
        "container": container,
        "redis_version": info.get("redis_version"),
        "graph_modules": modules,
        "persistence": {
            "save": client.config_get("save").get("save"),
            "appendonly": client.config_get("appendonly").get("appendonly"),
        },
    }


def restart(container):
    _sh(["docker", "restart", container])
    deadline = time.time() + 120
    client = redis.Redis(host="127.0.0.1", port=PORT, decode_responses=True,
                         socket_timeout=5.0)
    while time.time() < deadline:
        try:
            if client.ping():
                client.close()
                return
        except Exception:
            time.sleep(0.5)
    _invalid_harness(f"the container {container} did not answer after a restart")


def run(args):
    global PORT
    PORT = args.port
    client = redis.Redis(host="127.0.0.1", port=PORT, decode_responses=True,
                         socket_timeout=CLIENT_TIMEOUT_S)
    #: ``DUMP``/``RESTORE`` payloads are BINARY; a ``decode_responses=True`` client raises
    #: ``UnicodeDecodeError`` on them (the #7846 record's first trap).
    raw = redis.Redis(host="127.0.0.1", port=PORT, decode_responses=False,
                      socket_timeout=CLIENT_TIMEOUT_S)
    container, _ = resolve_engine(PORT)
    print(json.dumps({"engine": engine_header(client, container),
                      "window_target_mb": args.window_mb,
                      "sample_interval_s": SAMPLE_INTERVAL_S,
                      "loadavg": open("/proc/loadavg").read().strip()}))

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for arm in arms:
        # A fresh container per arm: RSS never shrinks, so an arm measured over a process
        # that already carried a window would inherit the previous arm's peak and credit it
        # to its own restore.
        restart(container)
        _, pid = resolve_engine(PORT)
        for g in (args.graph, TenantB.GRAPH, args.graph + "_2"):
            try:
                client.execute_command("DEL", g)
            except Exception:
                pass
        TenantB.seed(client)
        base_rss = mean_rss(pid, 2.0)

        with RssSampler(pid) as sampler:
            time.sleep(0.3)
            build_mark = sampler.mark()
            built = build_window(client, args.graph, args.window_mb)
            build_peak = sampler.slice_max(build_mark)
            _, built["index_ready_wait_s"] = _timed(
                await_index, client, args.graph, FULLTEXT_LABEL, built["nodes"]
            )
            built["index_ready_wait_s"] = round(built["index_ready_wait_s"], 2)
            pre = _index_of(client, args.graph)
            # ⛔ the populated-index gate, before any timer starts: #7846's first harness
            # measured an EMPTY index and therefore measured nothing.
            if not pre or pre["status"] != "OPERATIONAL" or (pre["numDocuments"] or 0) < built["nodes"]:
                _invalid_harness(f"the index was not populated before any timer started: {pre}")
            built["pre_index"] = pre
            built["pre_fts_hits"] = fts_hits(client, args.graph)
            if built["pre_fts_hits"] == 0:
                _invalid_harness("the fulltext index answers NO hits before any timer started")

            time.sleep(2.0)
            rest_mark = sampler.mark()
            time.sleep(1.0)
            rest_rss = sampler.slice_mean(rest_mark)
            mem = graph_mem(client, args.graph)
            window_mb = mem.get("total_graph_sz_mb")
            if not rest_rss or not sampler.samples:
                _invalid_harness("the RSS sampler took NO samples while the window was resident")
            if not window_mb or window_mb < args.window_mb:
                _invalid_harness(
                    f"the graph reports total_graph_sz_mb={window_mb} against a "
                    f"{args.window_mb} MB window target — the window was not reached"
                )
            print(json.dumps({
                "phase": "resident", "arm": arm,
                "nodes": built["nodes"],
                "window_mb": window_mb,
                "indices_sz_mb": mem.get("indices_sz_mb"),
                "graph_memory_usage": mem,
                "base_rss_mb": round(base_rss, 1),
                "rest_rss_mb": round(rest_rss, 1),
                "rest_ratio_of_window": round((rest_rss - base_rss) / window_mb, 2),
                "build_peak_rss_mb": round(build_peak, 1) if build_peak else None,
                "build_peak_ratio_of_window": round((build_peak - base_rss) / window_mb, 2)
                if build_peak else None,
                "build": built,
                "rss_samples": len(sampler.samples),
                "loadavg": open("/proc/loadavg").read().strip(),
            }))

            result = cycle(client, raw, args.graph, sampler, arm, built["nodes"],
                           base_rss, window_mb)
            result["loadavg_after"] = open("/proc/loadavg").read().strip()
            print(json.dumps(result))
            rest_after_cycle = sampler.slice_mean(sampler.mark() - 25)

            if args.marginal:
                # The per-tenant figure a multi-tenant cost model wants: what a SECOND
                # window of the same size costs on the same instance. `rest_ratio` above is
                # measured against an empty instance and so includes fixed per-process
                # arenas a second tenant never pays again.
                second = args.graph + "_2"
                with RssSampler(pid) as marginal_sampler:
                    time.sleep(0.3)
                    second_built = build_window(client, second, args.window_mb)
                    await_index(client, second, FULLTEXT_LABEL, second_built["nodes"])
                    time.sleep(2.0)
                    mark = marginal_sampler.mark()
                    time.sleep(1.0)
                    second_rest = marginal_sampler.slice_mean(mark)
                    second_peak = marginal_sampler.slice_max(mark)
                second_mem = graph_mem(client, second)
                print(json.dumps({
                    "phase": "marginal", "arm": arm,
                    "first_window_rest_rss_mb": round(rest_after_cycle, 1) if rest_after_cycle else None,
                    "second_window_nodes": second_built["nodes"],
                    "second_window_mb": second_mem.get("total_graph_sz_mb"),
                    "two_windows_rest_rss_mb": round(second_rest, 1) if second_rest else None,
                    "marginal_rss_mb": round(second_rest - rest_after_cycle, 1)
                    if second_rest and rest_after_cycle else None,
                    "marginal_ratio_of_window": round(
                        (second_rest - rest_after_cycle) / second_mem.get("total_graph_sz_mb", 1), 2
                    ) if second_rest and rest_after_cycle else None,
                    "second_window_build_peak_rss_mb": round(second_peak, 1) if second_peak else None,
                    "loadavg": open("/proc/loadavg").read().strip(),
                }))

    for g in (args.graph, TenantB.GRAPH, args.graph + "_2"):
        try:
            client.execute_command("DEL", g)
        except Exception:
            pass
    print(json.dumps({"phase": "done", "loadavg": open("/proc/loadavg").read().strip()}))


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("port", type=int)
    parser.add_argument("--window-mb", type=int, default=77,
                        help="window target in GRAPH.MEMORY USAGE MB (solo 77, pro 102, team 615)")
    parser.add_argument("--arms", default="fulltext-kept,fulltext-dropped")
    parser.add_argument("--graph", default="m4_window_a")
    parser.add_argument("--marginal", action="store_true",
                        help="also measure a SECOND window's marginal RSS on the same instance")
    args = parser.parse_args(argv)
    run(args)


if __name__ == "__main__":
    main(sys.argv[1:])
