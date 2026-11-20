"""#13123 — the egress executor must invalidate the degraded-fallback snapshot.

`tortoise/fallback_snapshot.py` keeps a 60 s in-memory copy of the whole ``:Point``
corpus and serves it from the DEGRADED tier — the in-memory TF-IDF path that answers
when the retrieval strategies fail. It is invalidated by ``SDK._mark_dirty``, which
every normal write surface calls **and a raw Cypher ``DETACH DELETE`` does not** — so
``tortoise/cache_evictor.py`` could delete a node and leave it returnable for up to the
snapshot TTL.

Measured by the M2 instrument (#12186): after a bare ``DELETE``, an arm reported
``recall@10`` **0.1133** against ``resident_share`` **0.1100** — more nodes retrievable
than the graph held. That is a FALSE PASS of the eviction: it reports success while the
node it removed is still served, and the degraded tier is active precisely when
retrieval is already failing.

⛔ These run against the PRIVATE LANE CONTAINER (`uv run python tools/test_lane.py run
-- …`), never a URI-less run: the property under test is that a control signal which
looks right against a fake holds against the engine, and the snapshot store is only
reachable through the same projection a degraded read uses.

The suites' split, stated so neither half is mistaken for the whole:

* THIS file — the eviction path end-to-end: delete a node, then ask the cache the
  degraded read path actually consults (`get_or_build`) what it serves now.
* ``tests/test_fallback_snapshot.py`` — the store's own ``invalidate_graph`` contract
  (scope, stamp movement, no-op on a nameless graph).
"""
from __future__ import annotations

import contextlib
import os
import sys
import time

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.cache_eviction import PARITY_INSTRUMENTS, ParityReport, assess, node_total
from tortoise.cache_evictor import Candidate, EvictionPolicy, evict

# ⛔ A module-level `skipif` MARK still COLLECTS its tests (the skip fires at runtime),
# so `allow_module_level=True` is what makes the module honestly collect nothing
# without a live store — the same shape `test_cache_eviction_evictor.py` uses.
if not os.environ.get("TORTOISE_DB_URI") or not _live_utils.docker_reachable():
    pytest.skip(
        "needs a live FalkorDB: set TORTOISE_DB_URI / run under "
        "`uv run python tools/test_lane.py run -- "
        "tests/test_evictor_invalidates_fallback_snapshot_13123.py`",
        allow_module_level=True,
    )

POPULATION = "turn"
TURN_INSTRUMENT = PARITY_INSTRUMENTS["turn"]
NAMESPACE = "issue13123"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the sparse leg, exactly as `test_fallback_snapshot.py` does.

    With an embedding model present the snapshot's document vectors come from a hosted
    provider call, which makes this suite depend on a network service to test a cache
    invalidation. `build_snapshot` degrades to sklearn TF-IDF, which is what the
    snapshot's own parity suite pins — so this changes no assertion here, only the
    encoder.
    """
    from tortoise.embeddings import EmbeddingModel

    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


@pytest.fixture()
def lane():
    """A dedicated lane graph + an SDK projection over it (the read path's own key).

    The SDK is what gives the test the SAME ``snapshot_key`` a degraded read uses; a
    hand-built key would prove the store works and nothing about the pairing under
    test.
    """
    import redis
    from falkordb import FalkorDB

    from tests._embedded import _journal_append
    from tortoise import fallback_snapshot as fs
    from tortoise.sdk import TortoiseSDK

    host = _live_utils.service_host()
    port = _live_utils.docker_port()
    password = _live_utils.docker_password() or None
    client = redis.Redis(
        host=host, port=port, password=password,
        decode_responses=True, socket_connect_timeout=10, socket_timeout=120,
    )
    db = FalkorDB(host=host, port=port, password=password)
    # ⛔ `test_` prefix + a journal entry: the session-end sweep reclaims only names in
    # its OWNED vocabulary (`tests/_embedded.py::owns_by_ownership_record`), and a
    # journaled-but-unowned name is PRESERVED while its journal is consumed — a leak
    # the sweep reports as green (`test_cache_eviction_evictor.py` carries the same
    # note, and `tests/test_derived_names.py` pins the rule).
    graph_name = f"test_evict_{os.urandom(6).hex()}"
    _journal_append(graph_name)
    graph = db.select_graph(graph_name)
    graph.query("MATCH (n) DETACH DELETE n")
    # ⛔ BOTH module caches, not just the store: a flight left by an earlier test would
    # make a reader join a build it did not lead, and `get_or_build`'s wait is unbounded
    # by design. The store is cleared on the way out for the same reason.
    fs._store.clear()
    fs._FLIGHTS.clear()
    sdk = TortoiseSDK(namespace=NAMESPACE, graph_name=graph_name)
    proj = sdk._get_proj()
    assert proj.g.name == graph_name, (
        "the SDK's projection and the raw handle name different graphs, so a snapshot "
        "key taken from one cannot be invalidated by a sweep of the other")
    try:
        yield client, graph, graph_name, proj, sdk
    finally:
        fs._store.clear()
        fs._FLIGHTS.clear()
        try:
            graph.query("MATCH (n) DETACH DELETE n")
            client.delete(graph_name)
        except Exception:  # pragma: no cover - best-effort teardown
            pass
        with contextlib.suppress(Exception):  # best-effort teardown
            sdk.close()


def _seed(graph, count: int) -> list[str]:
    """`count` points, coldest-first by `seq`, each with searchable content."""
    graph.query(
        "UNWIND range(0, $n - 1) AS i "
        "CREATE (p:Point {id: 'n' + toString(i), is_episodic: true, seq: i, "
        "               content: 'alpha beta gamma ' + toString(i), "
        "               pointKind: 'claim', status: 'active'})",
        {"n": count},
    )
    return [f"n{i}" for i in range(count)]


def _selector(graph, limit: int, population: str) -> list[Candidate]:
    """Coldest-first by `seq` — the ordering is the CALLER's policy (#10976)."""
    rows = graph.query(
        "MATCH (n:Point) WHERE n.is_episodic = true "
        "RETURN id(n), n.id ORDER BY n.seq ASC LIMIT $limit",
        {"limit": limit},
    ).result_set
    return [
        Candidate(node_id=int(row[0]), key=str(row[1]), population=population)
        for row in rows
    ]


def _turn_nodes(graph) -> int:
    """The population the parity report must size, counted the census's way."""
    return int(
        graph.query(
            "MATCH (n:Point) WHERE n.is_episodic = true RETURN count(n)"
        ).result_set[0][0]
    )


def _plan_assessment(client, graph, graph_name: str):
    """Drive the REAL census to a PLAN verdict with a proving parity report."""
    total = node_total(graph)
    turn = _turn_nodes(graph)
    report = ParityReport(
        instrument=TURN_INSTRUMENT,
        verdict="CLEAN",
        measured_nodes=turn,
        total_nodes=turn,
        graph_name=graph_name,
        # ⚠️ DATED: `assess` demotes an undated report to `cannot-answer`, because the
        # population count it carries is the executor's runaway cap.
        measured_at=time.time(),
    )
    assessment = assess(
        client, graph, graph_name,
        ceiling_bytes=1,  # any positive ceiling: resident is always over 1 byte
        parity_reports={TURN_INSTRUMENT: report},
    )
    assert assessment.verdict == "PLAN", assessment.problems
    assert assessment.plan["evictable"], assessment.plan
    assert total >= turn
    return assessment


def _served_ids(proj, sdk):
    """What a DEGRADED READ would see from the cache right now — no graph query.

    This is the exact seam `sdk.py` (~47179) uses: ``get_or_build`` for the key the
    degraded tier is keyed on, then ``search_snapshot`` over its points. Reading
    ``_store`` directly would pass on a store that had been invalidated by something
    other than the eviction path.
    """
    from tortoise import fallback_snapshot as fs

    key = fs.snapshot_key(proj, sdk._namespace)
    snap = fs.get_or_build(proj, key)
    assert snap is not None, "the snapshot did not build; this test cannot discriminate"
    return {p["id"] for p in snap["points"]}, key


# --------------------------------------------------------------------------------------
# (1) THE BUG — an evicted node must not come back through the degraded tier
# --------------------------------------------------------------------------------------
def test_an_evicted_node_cannot_be_served_by_the_degraded_snapshot(lane):
    """RED on the pre-#13123 tree: the snapshot names the deleted nodes and is served.

    The chain the bug lived in is short and this test walks all of it: the snapshot is
    BUILT (so it is warm and known to name n0..n4), the evictor REMOVES those nodes
    with a raw ``DETACH DELETE`` that never reaches ``SDK._mark_dirty``, and then the
    degraded tier is asked what it would serve.
    """
    client, graph, graph_name, proj, sdk = lane
    seed_keys = _seed(graph, 10)

    served_before, key = _served_ids(proj, sdk)
    assert set(seed_keys) <= served_before, (
        "the snapshot was not warm over the nodes this test evicts, so a stale serve "
        f"below would prove nothing (snapshot holds {sorted(served_before)})")

    report = evict(
        _plan_assessment(client, graph, graph_name), client, graph,
        population=POPULATION, selector=_selector,
        record_probe=lambda c: c.key is not None,
        dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.verdict == "EVICTED", report.reason
    # ⛔ Derived from the report, not hard-coded: `node_total` counts every node the
    # graph holds (the SDK's `:Meta` included), so the number evicted to reach a budget
    # of 6 is not the number of points seeded. What the test needs is only that the
    # pass removed a NON-EMPTY set of snapshot-named points, and which ones.
    evicted = [c.key for c in report.deleted]
    assert evicted, "the pass evicted nothing; this test cannot discriminate"
    survivors = [k for k in seed_keys if k not in set(evicted)]
    assert set(survivors), "the pass evicted the whole corpus"

    # The graph is the source of truth: those ids are GONE.
    resident = {str(r[0]) for r in graph.query(
        "MATCH (n:Point) WHERE n.is_episodic = true RETURN n.id").result_set}
    assert not (set(evicted) & resident), "the eviction did not remove its own plan"

    # ⛔ THE PIN. Everything below is "the cache agrees the nodes are gone".
    assert report.metric()["degraded_snapshot_invalidated"] is True, (
        "the eviction removed nodes but did not invalidate the degraded snapshot — "
        f"metric={report.metric()['degraded_snapshot_invalidated']!r}, "
        f"reason={report.reason!r}")

    served_after, key_after = _served_ids(proj, sdk)
    assert key_after == key, "the snapshot key moved; this test measures the wrong entry"
    resurrected = sorted(set(evicted) & served_after)
    assert not resurrected, (
        f"the degraded tier still serves evicted node(s) {resurrected} — the snapshot "
        f"outlived the delete (#13123). Served: {sorted(served_after)}")
    # …and the survivors are still there: the invalidation drops a STALE corpus, it
    # does not empty the tier (a rebuild that served nothing would also pass above).
    assert set(survivors) <= served_after, (
        f"the invalidation lost resident points: served {sorted(served_after)}")

    # The same question asked of the SCORER, not the corpus: a query naming an evicted
    # point's content must not surface its id.
    from tortoise.fallback_snapshot import get_or_build, search_snapshot

    snap = get_or_build(proj, key)
    hits = {h["id"] for h in search_snapshot("alpha beta gamma 0", snap,
                                             limit=10, include_terminal=True)}
    assert not (set(evicted) & hits), (
        f"the degraded SCORER returned evicted id(s) {sorted(set(evicted) & hits)}")


# --------------------------------------------------------------------------------------
# (2) THE COUNTER-TEST — a snapshot that is still valid keeps serving, unchanged
# --------------------------------------------------------------------------------------
def test_a_snapshot_that_is_still_valid_keeps_serving(lane):
    """No deletion ⇒ no invalidation, pinned by OBJECT IDENTITY on the cached entry.

    ⛔ Identity, not contents: a rebuild that returned the same ids would satisfy a
    contents check, and a rebuild on every pass is exactly the ``#10602`` regression the
    per-graph stamp was chosen to avoid (a global stamp measured 20 builds / 0 hits).
    """
    client, graph, graph_name, proj, sdk = lane
    _seed(graph, 10)

    from tortoise import fallback_snapshot as fs

    served, key = _served_ids(proj, sdk)
    first = fs.get_or_build(proj, key)
    assert first is not None

    # (a) a CLEAN pass: the cache is already within its budget, so nothing is deleted.
    clean = evict(
        _plan_assessment(client, graph, graph_name), client, graph,
        population=POPULATION, selector=_selector,
        record_probe=lambda c: c.key is not None,
        dry_run=False, policy=EvictionPolicy(max_nodes=len(served) + 1, batch_size=10),
    )
    assert clean.verdict == "CLEAN", clean.reason
    assert clean.metric()["degraded_snapshot_invalidated"] is None, (
        "a CLEAN pass reported an invalidation — `None` is 'not applicable'")
    assert fs.get_or_build(proj, key) is first, (
        "a CLEAN pass dropped a valid snapshot: every pass would then rebuild it")

    # (b) a DRY RUN over budget: plans nodes, deletes none, so nothing may be dropped.
    dry = evict(
        _plan_assessment(client, graph, graph_name), client, graph,
        population=POPULATION, selector=_selector,
        record_probe=lambda c: c.key is not None,
        dry_run=True, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert dry.verdict == "DRY_RUN", dry.reason
    assert dry.deleted == ()
    assert dry.metric()["degraded_snapshot_invalidated"] is None, (
        "a DRY RUN deleted nothing, so it must not invalidate anything")
    assert fs.get_or_build(proj, key) is first, (
        "a DRY RUN dropped a valid snapshot — planning is not deleting")


# --------------------------------------------------------------------------------------
# (3) THE SCOPE — the invalidation reaches this graph, not the whole process store
# --------------------------------------------------------------------------------------
def test_the_invalidation_does_not_blank_another_graphs_snapshot(lane):
    """A neighbour tenant's cached corpus must survive this graph's eviction.

    ``_store`` is a module singleton shared by every graph a process serves, so a
    whole-store reset here is a measurable regression on the one path that only runs
    when the store is already under strain (``#10602`` review round 4: 20 builds, 0
    hits). The entry is seeded through the real key shape — ``(graph, backend, ns)``.
    """
    client, graph, graph_name, proj, sdk = lane
    seed_keys = _seed(graph, 10)

    from tortoise import fallback_snapshot as fs

    neighbour_key = ("test_some_other_graph_13123", ("uri", "/tmp/elsewhere"), None)
    neighbour = {"built_at": time.monotonic(), "dirty": False,
                 "points": [{"id": "keep-me", "content": "x", "pointKind": "",
                             "status": "", "outdated": False, "search_keys": "",
                             "has_answer": False, "quote": ""}],
                 "vectorizer": None, "doc_vecs": None, "model_id": None}
    fs._store.put(neighbour_key, neighbour)
    _served_ids(proj, sdk)
    assert fs._store.get(neighbour_key) is not None

    report = evict(
        _plan_assessment(client, graph, graph_name), client, graph,
        population=POPULATION, selector=_selector,
        record_probe=lambda c: c.key is not None,
        dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.verdict == "EVICTED", report.reason
    assert report.metric()["degraded_snapshot_invalidated"] is True
    assert fs._store.get(neighbour_key) is not None, (
        "another graph's snapshot was blanked by this graph's eviction — the "
        "invalidation is wider than the graph whose nodes were deleted")
    evicted = {c.key for c in report.deleted}
    assert set(k for k in seed_keys if k not in evicted) <= _served_ids(proj, sdk)[0]
