"""#10976 — the egress EXECUTOR, red-first, on a real FalkorDB.

Four cases, each one discriminating on its own:

(a) the cache stays bounded under repeated writes,
(b) an entry past the bound is evicted and RE-READS from the record — and a node the
    record cannot serve is never evicted, however cold it is,
(c) the bound is configurable and FAILS CLOSED on an invalid value — a bad value
    refuses the pass, it never disables eviction,
(d) eviction does not break an in-flight read.

Plus the gates the issue's ordering constraint requires: a REFUSED census and an
unlicensed population both refuse with **zero mutating statements**, and a dry run
plans without deleting.

⛔ These run against the PRIVATE LANE CONTAINER (`tools/test_lane.py run -- …`), never
a URI-less run: the point of the suite is that a control signal which looks right
against a fake can be wrong against the engine (that is how the byte-vs-entry bound
question was settled — see `tortoise/cache_evictor.py`).
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from tests import _live_utils
from tortoise.cache_eviction import PARITY_INSTRUMENTS, ParityReport, assess, node_total
from tortoise.cache_evictor import (
    EVICTION_BATCH_ENV,
    EVICTION_MAX_NODES_ENV,
    Candidate,
    EvictionConfigError,
    EvictionPolicy,
    ReadLeases,
    evict,
)

# ⛔ A module-level `skipif` MARK still COLLECTS its tests (the skip fires at runtime),
# so the `uri_requiring` observation — which asks which modules "collect nothing" with an
# EMPTY `TORTOISE_DB_URI` — would never see this file, and the manifest entry would read
# as stale. `allow_module_level=True` is what makes the module honestly collect nothing.
if not os.environ.get("TORTOISE_DB_URI") or not _live_utils.docker_reachable():
    pytest.skip(
        "needs a live FalkorDB: set TORTOISE_DB_URI / run under "
        "`uv run python tools/test_lane.py run -- tests/test_cache_eviction_evictor.py`",
        allow_module_level=True,
    )

#: ⛔ The BARE population name — the word the census puts in the LICENCE
#: (`plan["evictable"][i]["population"]`), not its display label
#: (`ClassReading.label` = ``f"{population} (population)"``). The executor's
#: population gate compares for EQUALITY, so these two were not interchangeable:
#: keying the constant on the label refused the very sweep the licence named.
POPULATION = "turn"
TURN_INSTRUMENT = PARITY_INSTRUMENTS["turn"]

#: The measured shape on falkordb 4.20.6: ~156 B/node added, of which only ~44 % comes
#: back when the nodes are deleted. Kept as a comment rather than an assertion — it is
#: a property of the engine, not of this suite.
PAYLOAD = "x" * 40


class _Record:
    """A stand-in record: the SOURCE the cache must be able to re-read from."""

    def __init__(self, keys):
        self.rows = {k: {"content": f"row-for-{k}", "len": len(k)} for k in keys}

    def can_serve(self, candidate: Candidate) -> bool:
        return candidate.key in self.rows

    def read(self, key: str) -> dict:
        # ⛔ A KeyError here is the point of case (b): if the executor evicted a node
        # the record cannot serve, the re-read FAILS rather than returning a stale hit.
        return self.rows[key]

    def __call__(self, candidate: Candidate) -> bool:
        return self.can_serve(candidate)


@pytest.fixture()
def lane():
    """A dedicated graph on the live lane container, dropped afterwards."""
    import redis
    from falkordb import FalkorDB

    from tests._embedded import _journal_append

    host = _live_utils.service_host()
    port = _live_utils.docker_port()
    password = _live_utils.docker_password() or None
    client = redis.Redis(
        host=host, port=port, password=password,
        decode_responses=True, socket_connect_timeout=10, socket_timeout=120,
    )
    db = FalkorDB(host=host, port=port, password=password)
    # Epic #1647 (T7, cycle-6 P1-3): a raw client bypasses `_assert_test_graph`
    # AND the per-test wipe scope, so the handle must be PER-TEST-UNIQUE, in an
    # OWNED family, and JOURNALED in this same block — the three halves of one
    # contract (cycle-8 P2-2), not a rename.
    #
    # ⛔ The `test_` prefix is load-bearing, not cosmetic: the session-end sweep
    # drops only names that match `_SWEEP_OWNED_PREFIXES`
    # (`tests/_embedded.py::owns_by_ownership_record`). A journaled
    # `evict_<hex>` takes the PRESERVED branch — the sweep refuses to delete it
    # AND still removes the journal, so the graph leaks while the only record
    # that could reclaim it is destroyed. `tests/test_derived_names.py::
    # test_raw_falkordb_client_detach_uses_per_test_unique_graph` pins the
    # uniqueness+journal half; the owned prefix is pinned here by construction.
    #
    # The `uuid4` this replaced was unique but never OWNED and never journaled,
    # which is why `main` reddened on the census once a PR's changed-files
    # selection finally ran `tests/test_derived_names.py` (#11182's class, third
    # site: this file came in with #11167 without the guard ever seeing it).
    graph_name = f"test_evict_{os.urandom(6).hex()}"
    _journal_append(graph_name)
    graph = db.select_graph(graph_name)
    graph.query("MATCH (n) DETACH DELETE n")
    try:
        yield client, graph, graph_name
    finally:
        try:
            graph.query("MATCH (n) DETACH DELETE n")
            client.delete(graph_name)
        except Exception:  # pragma: no cover - best-effort teardown
            pass


def test_lane_graph_name_is_owned_by_the_sweep_s_ownership_record(lane):
    """A journal entry is an ownership RECORD only for a name the sweep's own
    vocabulary accepts.

    `_journal_append` is the third half of the raw-client contract BECAUSE the
    session-end sweep reads the journal through `owns_by_ownership_record`
    (`tests/_embedded.py::_SWEEP_OWNED_PREFIXES` = `test_`, `tortoise_test`,
    `team_`, `org_`). A journaled name outside that vocabulary makes the append
    INERT: `_sweep_drop` classifies it `preserved`, drops nothing, and then
    removes the journal anyway — so the graph this fixture minted can never be
    reclaimed, and `_owned_survivors` will not report it as a leak either. That
    is the #1647 cycle-8 P2-2 leak still open, with the census GREEN: the one
    thing this guard pair must not be able to produce.

    The fixture's name was `evict_<12hex>`; it reds on that name (`evict_` is
    outside every owned prefix) and greens on the `test_evict_<12hex>` it pins.
    """
    _client, _graph, graph_name = lane

    from tests._embedded import _owned_names, owns_by_ownership_record

    assert owns_by_ownership_record(graph_name), (
        f"{graph_name!r} is journaled but NOT owned — the sweep PRESERVES it and "
        f"consumes the journal, so the graph leaks for ever")
    # `_owned_names` is the sweep's DELETE set — the same predicate, applied to
    # this fixture's single recorded name, so the record and the drop set cannot
    # drift apart.
    assert _owned_names([graph_name], None) == {graph_name}


def _seed(graph, count: int, *, prefix: str = "n") -> list[str]:
    """Create `count` episodic `:Point` nodes with a deterministic coldness order."""
    keys: list[str] = []
    step = 500
    for start in range(0, count, step):
        end = min(start + step, count) - 1
        graph.query(
            "UNWIND range($a,$b) AS i "
            "CREATE (p:Point {id: $pre+toString(i), is_episodic: true, "
            "seq: i, payload: $payload})",
            {"a": start, "b": end, "pre": prefix, "payload": PAYLOAD},
        )
        keys.extend(f"{prefix}{i}" for i in range(start, end + 1))
    return keys


def _selector(graph, limit: int, population: str) -> list[Candidate]:
    """Coldest-first: `seq` ASC. The ordering is the CALLER's policy (#10976 leaves
    'what cold means' unsettled on purpose), so the test supplies one explicitly."""
    rows = graph.query(
        "MATCH (n:Point) WHERE n.is_episodic = true "
        "RETURN id(n), n.id ORDER BY n.seq ASC LIMIT $limit",
        {"limit": limit},
    ).result_set
    return [
        Candidate(node_id=int(row[0]), key=str(row[1]), population=population)
        for row in rows
    ]


def _plan_assessment(client, graph, graph_name: str):
    """Drive the REAL census to a PLAN verdict with a proving parity report.

    The report claims the whole turn population was measured and CLEAN — that is what
    a closed record gate would produce, and it is the only thing that arms the
    executor. When the record genuinely cannot serve (#10593/#7879 open), the same
    call returns REFUSED and `evict` deletes nothing; that is asserted separately.
    """
    total = node_total(graph)
    report = ParityReport(
        instrument=TURN_INSTRUMENT,
        verdict="CLEAN",
        measured_nodes=total,
        total_nodes=total,
        graph_name=graph_name,
        # ⚠️ The report must be DATED. `assess` demotes an undated (or aged) report to
        # `cannot-answer`, because the population count it carries is the executor's
        # runaway cap — so this fixture stamps "measured now" rather than leaving the
        # field out, which would silently turn this PLAN fixture into a REFUSAL.
        measured_at=time.time(),
    )
    assessment = assess(
        client, graph, graph_name,
        ceiling_bytes=1,  # any positive ceiling: resident is always over 1 byte
        parity_reports={TURN_INSTRUMENT: report},
    )
    assert assessment.verdict == "PLAN", assessment.problems
    assert assessment.plan["evictable"], assessment.plan
    return assessment


def _ids(graph) -> set[str]:
    return {str(row[0]) for row in graph.query("MATCH (n:Point) RETURN n.id").result_set}


# --------------------------------------------------------------------------------------
# (a) the cache stays bounded under repeated writes
# --------------------------------------------------------------------------------------
def test_case_a_the_cache_stays_bounded_under_repeated_writes(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 60)
    # The record serves every key this test will ever write, including the later
    # rounds — case (a) is about the BOUND, not about the record's coverage.
    record = _Record(
        seed_keys + [f"r{r}-{i}" for r in range(3) for i in range(30)]
    )
    policy = EvictionPolicy(max_nodes=20, batch_size=100)
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=record,
        dry_run=False, policy=policy,
    )
    assert report.verdict == "EVICTED", report.reason
    assert node_total(graph) <= 20, "the budget was not enforced"
    assert report.nodes_after <= 20
    # ⛔ THE FLOOR — the whole-graph outcome must be impossible. `budget` is
    # `min(batch, need, licence)` and `need = after_nodes - max_nodes` with
    # `max_nodes >= 1`, so a pass can never plan the entire graph. Pin it here:
    # a version that drained the cache would still pass the ceiling assertion above.
    assert node_total(graph) >= policy.max_nodes, (
        "the pass went BELOW the budget — the whole-graph guard is not holding"
    )

    # Keep writing; the budget must hold without re-declaring anything.
    for round_index in range(3):
        _seed(graph, 30, prefix=f"r{round_index}-")
        assessment = _plan_assessment(client, graph, graph_name)
        again = evict(
            assessment, client, graph,
            population=POPULATION, selector=_selector, record_probe=record,
            dry_run=False, policy=policy,
        )
        assert again.verdict == "EVICTED", again.reason
        assert node_total(graph) <= 20, (
            f"round {round_index}: cache grew past its budget to {node_total(graph)}"
        )


# --------------------------------------------------------------------------------------
# (b) an entry past the bound is evicted and RE-READS from the record
# --------------------------------------------------------------------------------------
def test_case_b_an_evicted_entry_re_reads_from_the_record(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)          # n0 (coldest) .. n9
    record = _Record(seed_keys)
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=record,
        dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )

    assert report.verdict == "EVICTED", report.reason
    evicted_keys = [c.key for c in report.deleted]
    assert evicted_keys == seed_keys[:4], (
        f"expected the 4 coldest entries, got {evicted_keys}"
    )
    # Gone from the cache …
    remaining = _ids(graph)
    for key in evicted_keys:
        assert key not in remaining, f"{key} was reported deleted but is still resident"
    # … and SERVED BY THE RECORD, byte for byte. A stale-hit or a lost write would
    # show up here as a missing key or a different payload.
    for key in evicted_keys:
        assert record.read(key) == {"content": f"row-for-{key}", "len": len(key)}
    # The survivors are exactly the 6 hottest.
    assert remaining == set(seed_keys[4:])


def test_case_b_a_node_the_record_cannot_serve_is_never_evicted(lane):
    """The coldest node in the graph is held back when the record cannot serve it.

    This is the ordering constraint as an executable property: a pass that cannot
    answer "can the record serve this?" for a node must not take it.
    """
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    # The record serves everything EXCEPT the coldest node.
    record = _Record(seed_keys[1:])
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=record,
        dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )

    assert [c.key for c in report.skipped_record_cannot_serve] == [seed_keys[0]]
    assert seed_keys[0] in _ids(graph), (
        "the executor evicted a node the record cannot serve — the exact data-loss "
        "class #10976 forbids"
    )
    assert seed_keys[0] not in [c.key for c in report.deleted]
    assert report.verdict == "EVICTED"


# --------------------------------------------------------------------------------------
# (c) the bound is configurable and FAILS CLOSED on an invalid value
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["abc", "0", "-5", "  ", "1.5", "10nodes"])
def test_case_c_the_bound_fails_closed_on_an_invalid_value(monkeypatch, bad):
    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, bad)
    monkeypatch.delenv(EVICTION_BATCH_ENV, raising=False)
    with pytest.raises(EvictionConfigError):
        EvictionPolicy.resolve()


def test_case_c_a_bad_bound_refuses_the_pass_and_deletes_nothing(lane, monkeypatch):
    """The failure mode this guards: a mistyped bound silently meaning 'unbounded'."""
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    record = _Record(seed_keys)
    assessment = _plan_assessment(client, graph, graph_name)

    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, "not-a-number")
    before = _ids(graph)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=record,
        dry_run=False,
    )

    assert report.verdict == "REFUSED"
    assert "config refused" in report.reason
    assert _ids(graph) == before, "a refused config must not have deleted anything"
    assert report.deleted == ()


def test_case_c_the_bound_is_configurable(monkeypatch):
    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, "250")
    monkeypatch.setenv(EVICTION_BATCH_ENV, "40")
    policy = EvictionPolicy.resolve()
    assert policy.max_nodes == 250
    assert policy.batch_size == 40
    # An explicit argument wins over the environment.
    assert EvictionPolicy.resolve(max_nodes=7, batch_size=3).max_nodes == 7


def test_case_c_an_invalid_batch_fails_closed(monkeypatch):
    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, "100")
    monkeypatch.setenv(EVICTION_BATCH_ENV, "0")
    with pytest.raises(EvictionConfigError):
        EvictionPolicy.resolve()


# --------------------------------------------------------------------------------------
# (d) eviction does not break an in-flight read
# --------------------------------------------------------------------------------------
def test_case_d_eviction_does_not_break_an_in_flight_read(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    record = _Record(seed_keys)
    leases = ReadLeases()
    coldest = int(
        graph.query(
            "MATCH (n:Point) WHERE n.is_episodic = true RETURN id(n) ORDER BY n.seq ASC "
            "LIMIT 1"
        ).result_set[0][0]
    )

    # A reader is mid-read on the coldest node.
    with leases.lease(coldest):
        assessment = _plan_assessment(client, graph, graph_name)
        report = evict(
            assessment, client, graph,
            population=POPULATION, selector=_selector, record_probe=record,
            leases=leases, dry_run=False,
            policy=EvictionPolicy(max_nodes=6, batch_size=10),
        )
        assert [c.node_id for c in report.skipped_leased] == [coldest]
        # The in-flight reader still sees its node.
        row = graph.query(
            "MATCH (n:Point) WHERE id(n) = $i RETURN n.id, n.payload", {"i": coldest}
        ).result_set
        assert row and row[0][0] == seed_keys[0], (
            "the node was deleted under an active read lease"
        )

    # Once the read finishes, the same node is evictable again — proving the lease (and
    # not the ordering) is what held it.
    assessment = _plan_assessment(client, graph, graph_name)
    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=record,
        leases=leases, dry_run=False,
        policy=EvictionPolicy(max_nodes=5, batch_size=10),
    )
    assert seed_keys[0] in [c.key for c in report.deleted]


# --------------------------------------------------------------------------------------
# the gates: REFUSED census / unlicensed population / dry run
# --------------------------------------------------------------------------------------
def test_a_refused_census_issues_no_mutating_statement(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    before = _ids(graph)
    # No parity report ⇒ nothing is proven ⇒ REFUSED, which is production's real state
    # while #10593 and #7879 are open.
    assessment = assess(client, graph, graph_name, ceiling_bytes=1)
    assert assessment.verdict == "REFUSED"

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    assert report.verdict == "REFUSED"
    assert _ids(graph) == before


def test_an_unlicensed_population_is_refused(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    before = _ids(graph)
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population="Point (label)", selector=_selector, record_probe=_Record(seed_keys),
        dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    assert report.verdict == "REFUSED"
    assert "not licensed" in report.reason
    assert _ids(graph) == before


def test_a_dry_run_plans_without_deleting(lane):
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    before = _ids(graph)
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        dry_run=True, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.verdict == "DRY_RUN"
    assert len(report.planned) == 4
    assert _ids(graph) == before, "a dry run deleted nodes"
    assert report.deleted == ()
    assert report.metric()["nodes_planned"] == 4
    assert report.metric()["nodes_evicted"] == 0


# --------------------------------------------------------------------------------------
# review findings — each one pinned so it cannot regress
# --------------------------------------------------------------------------------------
def test_a_recycled_internal_id_cannot_delete_the_wrong_node(lane):
    """⛔ FalkorDB REUSES a deleted node's internal id (measured: [0,1,2] → delete 1 →
    create → the new node took id 1). A delete keyed on the id alone would take a node
    created inside the probe→delete window that no probe ever saw."""
    from tortoise.cache_evictor import _delete_nodes

    _client, graph, _graph_name = lane
    _seed(graph, 3)                                  # n0, n1, n2
    stale = Candidate(
        node_id=int(
            graph.query(
                "MATCH (n:Point) WHERE n.id = 'n1' RETURN id(n)"
            ).result_set[0][0]
        ),
        key="n1",
        population=POPULATION,
    )
    # The node the candidate names is deleted by someone else, and a NEW node takes
    # the freed id — exactly the race the key binding must survive.
    graph.query("MATCH (n:Point) WHERE n.id = 'n1' DETACH DELETE n")
    graph.query("CREATE (p:Point {id:'NEW', is_episodic:true, seq:99})")
    reused = graph.query(
        "MATCH (n:Point) WHERE n.id = 'NEW' RETURN id(n)"
    ).result_set[0][0]
    assert int(reused) == stale.node_id, (
        "the engine did not recycle the id; this test cannot discriminate"
    )

    removed, _survivors = _delete_nodes(graph, [stale], "id")

    assert removed == 0, "a recycled id was deleted on identity alone"
    assert "NEW" in _ids(graph), (
        "the executor deleted a node it never asked the record about"
    )


def test_the_consumed_licence_count_caps_how_many_may_go(lane):
    """A population is not a label: the census's measured count is a hard cap."""
    from tortoise.cache_eviction import Assessment, CacheState

    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    state = CacheState(
        graph_name=graph_name, resident_bytes=1000, maxmemory_bytes=0,
        maxmemory_policy="noeviction", ceiling_bytes=1, ceiling_source="declared",
        graph_key_ttl_seconds=-1, keys_per_graph=1,
        graph_memory_usage_mb=None, graph_memory_usage_note=None,
    )
    assessment = Assessment(
        state=state, classes=(), verdict="PLAN",
        plan={"evictable": [
            {"population": POPULATION, "nodes": 2, "instrument": TURN_INSTRUMENT}
        ]},
    )

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        dry_run=False, policy=EvictionPolicy(max_nodes=1, batch_size=100),
    )

    assert len(report.deleted) == 2, (
        f"the licence proved 2 nodes but {len(report.deleted)} were swept"
    )
    assert report.verdict == "PARTIAL"


def test_a_dry_run_covers_the_whole_budget_not_one_batch(lane):
    """The dry run is the issue's first deliverable — it must state the WHOLE plan."""
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 20)
    assessment = _plan_assessment(client, graph, graph_name)

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        dry_run=True, policy=EvictionPolicy(max_nodes=1, batch_size=3, max_passes=10),
    )

    assert report.verdict == "DRY_RUN"
    assert len(report.planned) == 19, (
        f"a dry run planned only {len(report.planned)} of the 19 needed — it stopped "
        f"after one batch"
    )
    assert node_total(graph) == 20


def test_a_lease_taken_during_the_probe_still_wins(lane):
    """A read can start between planning and the statement; the lease must still win."""
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    leases = ReadLeases()
    order = [int(row[0]) for row in graph.query(
        "MATCH (n:Point) WHERE n.is_episodic = true RETURN id(n) ORDER BY n.seq ASC"
    ).result_set]
    victim = order[1]
    state = {"first": True}

    def probing(candidate) -> bool:
        if state["first"]:
            state["first"] = False
            leases.acquire(victim)          # the read starts DURING the pass
        return True

    assessment = _plan_assessment(client, graph, graph_name)
    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=probing,
        leases=leases, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )

    assert victim in [c.node_id for c in report.skipped_leased], report.skipped_leased
    assert seed_keys[1] in _ids(graph), "a node leased mid-pass was deleted"


def test_an_malformed_licence_entry_licenses_nothing(lane):
    """`str(entry.get("population"))` used to mint a licence literally named "None"."""
    from tortoise.cache_eviction import Assessment, CacheState

    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    before = _ids(graph)
    state = CacheState(
        graph_name=graph_name, resident_bytes=1000, maxmemory_bytes=0,
        maxmemory_policy="noeviction", ceiling_bytes=1, ceiling_source="declared",
        graph_key_ttl_seconds=-1, keys_per_graph=1,
        graph_memory_usage_mb=None, graph_memory_usage_note=None,
    )
    assessment = Assessment(
        state=state, classes=(), verdict="PLAN",
        plan={"evictable": [{"nodes": 5, "instrument": TURN_INSTRUMENT}]},  # no population
    )

    report = evict(
        assessment, client, graph,
        population="None", selector=_selector, record_probe=_Record(seed_keys),
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )

    assert report.verdict == "REFUSED"
    assert _ids(graph) == before


def test_a_refused_census_reports_none_not_zero(lane):
    """A monitor must not read an unmeasured value as a healthy zero."""
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 5)
    assessment = assess(client, graph, graph_name, ceiling_bytes=1)
    assert assessment.verdict == "REFUSED"

    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    metric = report.metric()
    assert metric["nodes_before"] is None
    assert metric["bytes_before"] == assessment.state.resident_bytes
    assert metric["nodes_evicted"] == 0


def test_a_fully_leased_pass_blames_the_LEASES_not_the_population(lane):
    """⛔ "population exhausted" sends the next reader to research the wrong thing.

    When the lease re-check empties the batch, every held node is in
    `skipped_leased` — the population is NOT exhausted. Reporting `population` there is
    the same misattribution class the pass-budget split exists to prevent, and worse:
    the answer ("a reader is holding these") is not discoverable from any population
    query.
    """
    client, graph, graph_name = lane
    seed_keys = _seed(graph, 10)
    leases = ReadLeases()
    for row in graph.query(
        "MATCH (n:Point) WHERE n.is_episodic = true RETURN id(n)"
    ).result_set:
        leases.acquire(int(row[0]))

    assessment = _plan_assessment(client, graph, graph_name)
    report = evict(
        assessment, client, graph,
        population=POPULATION, selector=_selector, record_probe=_Record(seed_keys),
        leases=leases, dry_run=False,
        policy=EvictionPolicy(max_nodes=4, batch_size=10),
    )

    assert len(report.skipped_leased) == 10, report.skipped_leased
    assert "READ" in report.reason, report.reason
    assert "did not hold enough evictable nodes" not in report.reason, report.reason
    assert node_total(graph) == 10, "a leased node was deleted"
