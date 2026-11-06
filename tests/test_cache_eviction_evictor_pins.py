"""#10976 — HERMETIC pins for the egress executor's refusal gates (PR-time gate).

WHY THIS FILE EXISTS SEPARATELY from `test_cache_eviction_evictor.py`: that suite
drives a real FalkorDB, so it is `uri_requiring`, and `ci_selection` subtracts a
`uri_requiring` file from the tier-2 PR lane (which has no URI). Left as the only
suite, a change to the DELETE gate would merge on a green PR with **no executor test
executed at all** — the silent-green class this repo treats as a defect.

Everything here is decision logic that needs no engine: the four refusal gates, the
fail-closed config, and the dry run. The engine-dependent cases (a bounded cache, a
re-read from the record, a lease held across a real delete, a recycled node id) stay in
the live suite, where they can be wrong in ways a fake cannot reproduce.

⛔ The fake graph RECORDS every statement, so "refused and issued no mutating
statement" is asserted rather than argued.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.cache_eviction import Assessment, CacheState
from tortoise.cache_evictor import (
    EVICTION_BATCH_ENV,
    EVICTION_MAX_NODES_ENV,
    Candidate,
    EvictionConfigError,
    EvictionPolicy,
    evict,
)

POPULATION = "turn"  # the BARE population name the census puts in the licence
                     # (`plan["evictable"][i]["population"]`), never its display label
MUTATING = ("DETACH DELETE", "DELETE", "CREATE", "MERGE", "SET ", "REMOVE", "DROP")


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """Records every statement, and can EXECUTE a delete so the delete path is pinned.

    ⛔ The delete must be reachable here: the round-1 P1 fix (bind the delete to the
    record key, not just the internal id) and the survivor accounting had no PR-time
    coverage while every deletion-adjacent case was a dry run.
    """

    def __init__(
        self,
        nodes: int = 10,
        *,
        delete_removes: int | None = None,
        peer_takes: int = 0,
    ) -> None:
        self.nodes = nodes
        self.delete_removes = delete_removes   # None = remove everything offered
        #: Rows a PEER evicted inside the probe->delete window. They are gone from the
        #: graph but are NOT this pass's deletions — the case `survivors` cannot
        #: express (it is a complement query, so a peer-taken row appears in neither
        #: `final` nor `survivors`).
        self.peer_takes = peer_takes
        #: Raise on the post-delete re-count, to pin that a measurement failure after a
        #: delete still RETURNS a report instead of losing the record of the deletion.
        self.fail_recount = False
        self._deleted_once = False
        self.statements: list[str] = []
        self.deletes: list[tuple[str, list[dict]]] = []

    def ro_query(self, statement: str, params=None):
        """The census's read verb (`GRAPH.RO_QUERY`). A handle without it is
        REFUSED by `cache_eviction._read_statement`, so the fake must expose it."""
        return self.query(statement, params=params)

    def query(self, statement: str, params=None):
        self.statements.append(statement)
        if "MATCH (n) RETURN count(n)" in statement:
            if self.fail_recount and self._deleted_once:
                raise RuntimeError("transport blip")
            return _Result([[self.nodes]])
        if "DETACH DELETE" in statement:
            rows = list((params or {}).get("rows") or [])
            self.deletes.append((statement, rows))
            removed = len(rows) if self.delete_removes is None else self.delete_removes
            # ⛔ PEER-TAKEN rows: the statement removed `len(rows) - peer_takes`, and a
            # peer took the rest, so the survivor query returns NOTHING. This is the
            # exact shape that `final - survivors` over-reports and `[:removed]` fixes.
            if self.peer_takes:
                removed = max(0, len(rows) - self.peer_takes)
                self.nodes = max(0, self.nodes - removed)
                self._survivors = []
                return _Result([[removed]])
            self.nodes = max(0, self.nodes - removed)
            self._deleted_once = True
            if self.delete_removes is None:
                return _Result([[removed]])
            # A shortened delete: report the count, then name the survivors.
            self._survivors = rows[self.delete_removes:]
            return _Result([[removed]])
        if "RETURN id(n)" in statement:
            rows = list((params or {}).get("rows") or [])
            short = getattr(self, "_survivors", rows)
            return _Result([[r["id"]] for r in short])
        raise AssertionError(f"unexpected statement: {statement}")

    @property
    def mutating(self) -> list[str]:
        return [
            s for s in self.statements
            if any(token in s.upper() for token in MUTATING)
        ]


class _FakeClient:
    def __init__(self, used_memory: int = 1_000_000) -> None:
        self.used_memory = used_memory

    def info(self, section: str):
        assert section == "memory"
        return {"used_memory": self.used_memory}


def _state(*, graph_name: str = "g", ceiling_bytes: int | None = 1) -> CacheState:
    return CacheState(
        graph_name=graph_name,
        resident_bytes=1_000_000,
        maxmemory_bytes=0,
        maxmemory_policy="noeviction",
        ceiling_bytes=ceiling_bytes,
        ceiling_source="declared (test)",
        graph_key_ttl_seconds=-1,
        keys_per_graph=1,
        graph_memory_usage_mb=None,
        graph_memory_usage_note=None,
    )


def _assessment(*, verdict: str = "PLAN", evictable=None, ceiling_bytes: int | None = 1) -> Assessment:
    return Assessment(
        state=_state(ceiling_bytes=ceiling_bytes),
        classes=(),
        verdict=verdict,
        plan={"evictable": evictable if evictable is not None else [
            {"population": POPULATION, "nodes": 10, "instrument": "tools/turn_parity_census.py"}
        ]},
    )


def _record(candidate: Candidate) -> bool:
    return candidate.key is not None


# --------------------------------------------------------------------------------------
# the four refusal gates
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("verdict", ["REFUSED", "UNBOUNDED"])
def test_the_census_gates_the_pass_and_issues_no_mutating_statement(verdict):
    graph = _FakeGraph()
    report = evict(
        _assessment(verdict=verdict), _FakeClient(), graph,
        population=POPULATION, selector=lambda *a: [], record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )
    assert report.verdict == "REFUSED"
    assert graph.mutating == [], "a refused census still issued a mutating statement"
    # A monitor must not read an unmeasured node count as a healthy zero.
    assert report.metric()["nodes_before"] is None
    assert report.metric()["bytes_before"] == 1_000_000


def test_an_unlicensed_population_is_refused():
    graph = _FakeGraph()
    report = evict(
        _assessment(), _FakeClient(), graph,
        population="Point (label)", selector=lambda *a: [], record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )
    assert report.verdict == "REFUSED"
    assert "not licensed" in report.reason
    assert graph.mutating == []


def test_a_licence_entry_without_a_population_licenses_nothing():
    """`str(entry.get("population"))` used to mint a licence literally named "None"."""
    graph = _FakeGraph()
    report = evict(
        _assessment(evictable=[{"nodes": 5}]), _FakeClient(), graph,
        population="None", selector=lambda *a: [], record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )
    assert report.verdict == "REFUSED"
    assert graph.mutating == []


# --------------------------------------------------------------------------------------
# the bound fails closed
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "", "  ", "abc", "0", "-5", "1.5", "10nodes"])
def test_an_invalid_or_absent_bound_refuses_instead_of_disabling_eviction(monkeypatch, bad):
    if bad is None:
        monkeypatch.delenv(EVICTION_MAX_NODES_ENV, raising=False)
    else:
        monkeypatch.setenv(EVICTION_MAX_NODES_ENV, bad)
    monkeypatch.delenv(EVICTION_BATCH_ENV, raising=False)
    with pytest.raises(EvictionConfigError):
        EvictionPolicy.resolve()


@pytest.mark.parametrize("bad", ["0", "-1", "abc"])
def test_an_invalid_batch_refuses(monkeypatch, bad):
    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, "100")
    monkeypatch.setenv(EVICTION_BATCH_ENV, bad)
    with pytest.raises(EvictionConfigError):
        EvictionPolicy.resolve()


def test_a_bad_bound_refuses_the_pass_and_touches_nothing(monkeypatch):
    graph = _FakeGraph()
    monkeypatch.setenv(EVICTION_MAX_NODES_ENV, "not-a-number")
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda *a: [], record_probe=_record,
        dry_run=False,
    )
    assert report.verdict == "REFUSED"
    assert "config refused" in report.reason
    assert graph.mutating == []


def test_a_zero_bound_cannot_be_built_directly_either():
    with pytest.raises(EvictionConfigError):
        EvictionPolicy(max_nodes=0)


# --------------------------------------------------------------------------------------
# dry run and the licence cap
# --------------------------------------------------------------------------------------
def _candidates(n: int, population: str = POPULATION) -> list[Candidate]:
    return [Candidate(node_id=i, key=f"k{i}", population=population) for i in range(n)]


def test_a_dry_run_plans_and_never_deletes():
    graph = _FakeGraph(nodes=10)
    planned = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: planned[:limit],
        # ⛔ batch_size=2 against a need of 4 FORCES a multi-pass plan: a one-pass
        # implementation plans 2 and fails, which is what makes this discriminate.
        record_probe=_record, dry_run=True, policy=EvictionPolicy(max_nodes=6, batch_size=2),
    )
    assert report.verdict == "DRY_RUN"
    assert len(report.planned) == 4
    assert report.metric()["nodes_evicted"] == 0
    assert graph.mutating == [], "a dry run issued a mutating statement"


def test_a_keyless_candidate_is_never_deleted():
    """The delete binds on the record key; without one there is nothing to bind to."""
    graph = _FakeGraph(nodes=10)
    keyless = [Candidate(node_id=i, key=None, population=POPULATION) for i in range(10)]
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: keyless[:limit],
        # ⛔ EXECUTING, not dry: a dry run cannot prove the DELETE path holds back a
        # keyless candidate. The fake accepts a delete, so a broken implementation that
        # deleted on the id alone would be caught here.
        record_probe=_record, dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert graph.deletes == [], "a keyless candidate reached the delete statement"
    assert report.planned == ()
    # Every candidate the pass examined was held back, and NONE was planned. The window
    # widens pass-over-pass, so the exact count depends on `max_passes`; the invariant
    # that discriminates is that not one keyless candidate was ever planned.
    assert len(report.skipped_record_cannot_serve) >= 4
    assert all(c.key is None for c in report.skipped_record_cannot_serve)


def test_the_proven_population_count_caps_the_sweep():
    """Evicting more nodes than the instrument measured is evicting unproven nodes."""
    graph = _FakeGraph(nodes=100)
    offered = _candidates(100)
    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 3, "instrument": "tools/turn_parity_census.py"}
        ]),
        _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=True,
        policy=EvictionPolicy(max_nodes=1, batch_size=100),
    )
    assert len(report.planned) == 3, (
        f"the licence proved 3 nodes but {len(report.planned)} were planned"
    )


def test_the_whole_graph_can_never_be_planned():
    """`budget = min(batch, need, licence)` with `max_nodes >= 1` makes it impossible."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=True,
        # batch 2 against a need of 9: a single pass cannot reach 9, so the multi-pass
        # loop is what this asserts.
        policy=EvictionPolicy(max_nodes=1, batch_size=2, max_passes=10),
    )
    assert len(report.planned) == 9, "a pass planned the entire graph"
    assert len(report.planned) < graph.nodes


# --------------------------------------------------------------------------------------
# the delete path (round-1 P1 fix and survivor accounting)
# --------------------------------------------------------------------------------------
def test_the_delete_binds_the_internal_id_AND_the_record_key():
    """FalkorDB reuses ids, so the key is the half that makes the delete safe."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.verdict == "EVICTED"
    assert graph.deletes, "no delete statement was issued"
    statement, rows = graph.deletes[0]
    assert "id(n) = r.id" in statement, statement
    assert "n.id = r.key" in statement, (
        f"the delete does not bind the record key: {statement}"
    )
    assert all(row.get("key") for row in rows)


def test_a_short_delete_is_unverified_and_excludes_the_survivor():
    """`RETURN count(n)` counts rows; a concurrent delete can shorten the batch."""
    graph = _FakeGraph(nodes=10, delete_removes=1)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=2, max_passes=1),
    )
    assert report.delete_unverified is True
    assert len(report.deleted) == 1, (
        f"reported {len(report.deleted)} evicted but only 1 row was removed"
    )
    assert report.metric()["nodes_evicted"] == 1


def test_a_truncated_dry_run_says_so():
    """A plan cut short by the pass budget must not read as complete."""
    graph = _FakeGraph(nodes=100)
    offered = _candidates(100)
    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 100, "instrument": "i"}
        ]),
        _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=True,
        policy=EvictionPolicy(max_nodes=1, batch_size=2, max_passes=3),
    )
    assert report.truncated is True, report.reason
    assert report.metric()["plan_truncated"] is True
    assert "TRUNCATED" in report.reason


def test_a_duplicate_offered_node_is_counted_once():
    """A join-shaped selector can offer one node twice; `count(n)` counts ROWS."""
    graph = _FakeGraph(nodes=10)
    dup = _candidates(2) * 5           # 10 rows, 2 distinct nodes
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: dup,
        record_probe=_record, dry_run=True,
        policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert len(report.planned) == 2, f"one node was counted {len(report.planned)} times"


# --------------------------------------------------------------------------------------
# round-3 review findings
# --------------------------------------------------------------------------------------
def test_a_truthy_non_boolean_probe_does_not_license_a_delete():
    """⛔ `bool((False, "no row"))` is True. "Could not answer" must refuse."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=lambda c: (False, "no row"),   # truthy, but NOT an explicit True
        dry_run=False, policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.planned == ()
    assert graph.deletes == [], "a truthy non-boolean probe licensed a delete"
    assert len(report.skipped_record_cannot_serve) >= 1


def test_an_unservable_head_does_not_starve_the_pass():
    """The window must widen past held-back nodes, not stop at the first one."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)

    def probe(candidate) -> bool:
        return candidate.node_id >= 4          # the 4 coldest can never be served

    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=probe, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=4, max_passes=5),
    )
    assert len(report.deleted) == 4, (
        f"the pass starved on the unservable head and evicted {len(report.deleted)}"
    )


def test_a_formatted_memory_reading_is_none_not_an_exception():
    """A measurement failure must be the documented None, not a ValueError mid-sweep."""

    class _FormattedClient:
        def info(self, section: str):
            return {"used_memory": "12.5M"}

    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FormattedClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=True,
        policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )
    assert report.metric()["bytes_before"] is None
    assert report.metric()["bytes_reclaimed"] is None


def test_a_peer_evicted_row_is_not_counted_as_ours():
    """`survivors` is a complement query; the count must come from the statement."""
    graph = _FakeGraph(nodes=10, delete_removes=1)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=2, max_passes=1),
    )
    assert report.metric()["nodes_evicted"] == 1, (
        "the metric claimed a deletion the statement did not prove"
    )
    assert report.delete_unverified is True


def test_a_peer_evicted_row_with_no_survivor_is_not_counted_as_ours():
    """⛔ The complement-query defect: a peer takes a planned row, so `survivors` is
    EMPTY while the statement's count is short. `final - survivors` would report both."""
    graph = _FakeGraph(nodes=10, peer_takes=2)
    offered = _candidates(2)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=2, max_passes=1),
    )
    assert report.metric()["nodes_evicted"] == 0, (
        "the metric counted a row a peer had already evicted"
    )
    assert report.delete_unverified is True


def test_a_long_unservable_head_is_reached_and_the_budget_state_is_named():
    """The window must double, and "budget spent" must not read as "nothing to give"."""
    graph = _FakeGraph(nodes=60)
    offered = _candidates(60)

    def probe(candidate) -> bool:
        return candidate.node_id >= 50          # 50 unservable, then 10 servable

    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 60, "instrument": "i"}
        ]),
        _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=probe, dry_run=False,
        policy=EvictionPolicy(max_nodes=40, batch_size=5, max_passes=5),
    )
    assert len(report.deleted) > 0, "the pass never reached past the unservable head"

    # And when the budget genuinely runs out, the reason says so rather than blaming
    # the population.
    tight = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 60, "instrument": "i"}
        ]),
        _FakeClient(), _FakeGraph(nodes=60),
        population=POPULATION,
        selector=lambda g, limit, pop: offered[:limit],
        record_probe=probe, dry_run=False,
        policy=EvictionPolicy(max_nodes=40, batch_size=2, max_passes=1),
    )
    assert tight.metric()["pass_budget_exhausted"] is True
    assert "re-run to continue" in tight.reason


def test_a_population_proven_dry_is_not_blamed_on_the_pass_budget():
    """⛔ "Ran out of passes" and "nothing left to give" need different responses."""
    offered = _candidates(10)

    def probe(candidate) -> bool:
        return candidate.node_id < 2          # only 2 can ever be served

    report = evict(
        _assessment(), _FakeClient(), _FakeGraph(nodes=10),
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=probe, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=10, max_passes=2),
    )
    # The last allowed pass examined the WHOLE remaining graph and every node was
    # unservable — so the pass budget is not what stopped it.
    assert report.metric()["pass_budget_exhausted"] is False, report.reason
    assert "did not hold enough evictable nodes" in report.reason


def test_a_truncated_plan_names_the_real_constraint():
    """A plan cut by the LICENCE must not tell the reader to tune the pass budget."""
    offered = _candidates(100)
    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 3, "instrument": "i"}
        ]),
        _FakeClient(), _FakeGraph(nodes=100),
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=True,
        policy=EvictionPolicy(max_nodes=1, batch_size=100, max_passes=10),
    )
    assert report.truncated is True
    assert "census proved only 3 node(s)" in report.reason, report.reason
    assert "pass budget" not in report.reason


def test_a_post_delete_measure_failure_still_returns_the_record():
    """⛔ An unguarded re-count escapes AFTER nodes were removed and loses the record."""
    graph = _FakeGraph(nodes=10)
    graph.fail_recount = True
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=4, max_passes=1),
    )
    assert report.verdict == "REFUSED"
    assert len(report.deleted) == 4, (
        "the deletions were lost when the re-count failed"
    )
    assert "UNKNOWN" in report.reason
    assert graph.deletes, "the delete statement did run"
    # ⛔ UNMEASURED is None, never the pre-delete reading: otherwise the report says
    # "4 evicted, 0 bytes reclaimed" about a state nobody measured.
    assert report.metric()["nodes_after"] is None
    assert report.metric()["bytes_after"] is None
    assert report.metric()["bytes_reclaimed"] is None


def test_a_licence_bound_partial_plan_names_the_licence():
    """A PARTIAL cut by the census proof must not tell the reader to widen the selector."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 2, "instrument": "i"}
        ]),
        _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=100, max_passes=3),
    )
    assert report.verdict == "PARTIAL"
    assert "census proved only 2 node(s)" in report.reason, report.reason


def test_a_failed_post_delete_byte_read_is_none_not_the_pre_delete_value():
    """⛔ The node count and the byte reading are two reads; either can fail alone."""

    class _FailsAfterFirstRead:
        def __init__(self) -> None:
            self.calls = 0

        def info(self, section: str):
            self.calls += 1
            if self.calls > 1:
                return {"used_memory": "unreadable"}
            return {"used_memory": 1_000_000}

    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FailsAfterFirstRead(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=4, max_passes=1),
    )
    m = report.metric()
    # The NODE count WAS measured after the delete, so it stays a number …
    assert m["nodes_after"] == 6
    # … and the byte reading was NOT, so it must not be the pre-delete value.
    assert m["bytes_after"] is None, (
        f"reported the pre-delete reading {m['bytes_after']} as the after-state"
    )
    assert m["bytes_reclaimed"] is None
    assert m["byte_ceiling_still_exceeded"] is None
