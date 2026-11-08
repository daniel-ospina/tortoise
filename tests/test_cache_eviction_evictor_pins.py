"""#10976 — HERMETIC pins for the egress executor's refusal gates (PR-time gate).

WHY THIS FILE EXISTS SEPARATELY from `test_cache_eviction_evictor.py`: that suite
drives a real FalkorDB, so it is `uri_requiring`, and `ci_selection` subtracts a
`uri_requiring` file from the tier-2 PR lane (which has no URI). Left as the only
suite, a change to the DELETE gate would merge on a green PR with **no executor test
executed at all** — the silent-green class this repo treats as a defect.

Everything here is decision logic that needs no engine: the five refusal gates, the
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
    ReadLeases,
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
        fail_delete: bool = False,
        name: str | None = "g",
        fail_first_count: bool = False,
    ) -> None:
        self.nodes = nodes
        #: Raise on the FIRST node count (the pre-pass read), to pin that a store fault
        #: there is a REFUSAL returning a report rather than an exception out of `evict`.
        self.fail_first_count = fail_first_count
        self._counted = False
        #: The graph this handle SAYS it is. `evict`'s gate 5 refuses a handle that
        #: cannot name its graph, or names one other than the census's — so the fake
        #: must model the identity every real handle carries (`falkordb.Graph.name`).
        self.name = name
        #: Raise INSIDE the `DETACH DELETE` statement, to pin that a store fault during
        #: the delete still returns the record of what was already removed and marks the
        #: effect UNVERIFIED (the CLI reads that flag to avoid reporting "nothing went").
        self.fail_delete = fail_delete
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
            if self.fail_first_count and not self._counted:
                self._counted = True
                raise RuntimeError("pre-pass count transport blip")
            self._counted = True
            if self.fail_recount and self._deleted_once:
                raise RuntimeError("transport blip")
            return _Result([[self.nodes]])
        if "DETACH DELETE" in statement:
            rows = list((params or {}).get("rows") or [])
            self.deletes.append((statement, rows))
            if self.fail_delete:
                # ⛔ The statement REACHED the server and its effect is unknown — the one
                # case where the report must say UNVERIFIED rather than "nothing went".
                raise RuntimeError("RUNTIME reset on the delete")
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
# gates 1-2 — the census and the licence
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("verdict", [
    "REFUSED", "UNBOUNDED",
    # ⛔ THE ALLOW-LIST PROPERTY ITSELF. Gate 1 is an allow-list — `PLAN`/`CLEAN` and
    # nothing else — because `refused` is True only for `REFUSED`/`UNBOUNDED`, so a
    # deny-list would arm the delete path for a verdict this module has never heard of
    # whenever the caller ALSO supplied a populated plan. These words are that case:
    # a future census verdict, a typo, a stray space. Measured before they were here:
    # rewriting gate 1 as `if assessment.refused:` left 187 tests green, because every
    # test fed `PLAN`, `CLEAN`, or one of the two words already refused.
    "PARTIAL", "clean", "", "PLAN ",
])
def test_the_census_gates_the_pass_and_issues_no_mutating_statement(verdict):
    graph = _FakeGraph()
    report = evict(
        # `_assessment` still supplies a POPULATED licence: that is what made the
        # deny-list mutation dangerous rather than merely wrong.
        _assessment(verdict=verdict), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record,
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
    # ⛔ AND IT MUST NOT PROMISE A RE-RUN THAT CANNOT CONVERGE. This case is
    # `batch_size=2, max_passes=1`, so one call examines at most `2 x 2^0 = 2` nodes
    # ahead of the selection — and the held-back head IS 2. The window does not survive
    # a call, so "re-run to continue" here would be an instruction that loops forever.
    # (The comment in `cache_evict` used to claim doubling reached any head within
    # `log2(nodes)` passes; that was false, and this pin is what makes the correction
    # stick.)
    assert "re-running will not converge" in tight.reason, tight.reason
    assert "did not hold enough evictable nodes" not in tight.reason, (
        "the population was not the binding constraint")


def test_a_head_SHORTER_than_one_calls_reach_still_promises_the_re_run():
    """The other half: when the head is inside one call's reach, re-running DOES help.

    ⛔ THE PASS BUDGET MUST ACTUALLY BIND, or this asserts nothing. An earlier version
    used a configuration whose holder-free 60-node graph finished inside its budget
    (`pass_budget_exhausted` False), so both assertions sat behind an `if` that never
    ran — and it was the ONLY assertion on the "re-run to continue" arm, leaving an
    inverted arm green. Measured here: 200 nodes, `max_nodes=40, batch_size=5,
    max_passes=2` -> reach 10, budget binds, and a re-run can make progress.
    """
    offered = _candidates(200)

    def probe(candidate) -> bool:
        return candidate.node_id >= 2          # the 2-node head can never be served

    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 200, "instrument": "i"}
        ]),
        _FakeClient(), _FakeGraph(nodes=200),
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=probe, dry_run=False,
        # reach = 5 x 2^1 = 10, far INSIDE the 2-node head's successor set, and the pass
        # budget binds before the budget does.
        policy=EvictionPolicy(max_nodes=40, batch_size=5, max_passes=2),
    )

    assert report.metric()["pass_budget_exhausted"] is True, (
        f"the pass budget did not bind, so this asserts nothing: {report.reason}")
    assert "re-run to continue" in report.reason, report.reason
    assert "will not converge" not in report.reason, report.reason


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


# --------------------------------------------------------------------------------------
# gate 1's allow-list, pinned where the PR lane actually runs it (#10976 cycle 3)
#
# ⛔ WHY THESE LIVE HERE. `CLEAN` was added to gate 1 because the census's verdict is its
# BYTE state while this module enforces an ENTRY budget — a cache far over its node budget
# can sit under its byte ceiling (measured: 1,000,000 nodes against a declared 20,000-node
# budget -> `CLEAN`). Without a pin in THIS file, reverting the allow-list to `("PLAN",)`
# merged green: the only coverage lived in the URI-requiring suite this file's own
# docstring records as subtracted from the tier-2 lane.
# --------------------------------------------------------------------------------------
def test_a_CLEAN_byte_verdict_still_licenses_the_sweep():
    """CLEAN + a licensed population + over the entry budget must SWEEP."""
    graph = _FakeGraph()
    report = evict(
        _assessment(verdict="CLEAN"), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )
    assert report.verdict != "REFUSED", report.reason
    assert report.deleted, "a CLEAN byte verdict licensed nothing"
    assert graph.mutating, "the sweep issued no statement"


def test_a_CLEAN_byte_verdict_licenses_nothing_without_a_proven_population():
    """The negative half: CLEAN is admission, not a licence."""
    graph = _FakeGraph()
    report = evict(
        _assessment(verdict="CLEAN", evictable=[]), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )
    assert report.verdict == "REFUSED"
    assert graph.mutating == [], f"a CLEAN census with no licence mutated: {graph.mutating}"


def test_a_failed_DELETE_statement_is_reported_as_unverified_not_as_clean():
    """⛔ The statement was ISSUED; the server may have applied it.

    `nodes_after` must be None (UNMEASURED, never a fabricated value), `deleted` must not
    claim the pass's own rows, and `delete_unverified` must be set — the flag the CLI reads
    to avoid reporting exit 3 ("nothing went") for a run whose effect is unknowable.
    """
    graph = _FakeGraph(fail_delete=True)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record,
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )

    assert report.delete_unverified is True
    assert report.nodes_after is None, "an UNMEASURED after-state was reported as a value"
    assert report.deleted == (), "the pass's own rows were claimed as removed"
    assert "UNVERIFIED" in report.reason, report.reason
    assert graph.mutating, "the fake never saw the DELETE it was meant to fail on"


# --------------------------------------------------------------------------------------
# THE IDENTIFIER GATE — the property name that reaches the DELETE statement
#
# ⛔ WHY THIS EXISTS. `_key_property` validates a CALLER-SUPPLIED `key_prop` before it is
# interpolated into `AND n.{quote} = r.key` in the DETACH DELETE. It had NO test that
# could fail: replacing its whole body with `return key_prop` left 78 tests green, and
# `grep -n 'key_prop' tests/*.py` found no hit at all. The consequence is silent — an
# empty or unquoted name emits a MALFORMED statement mid-sweep, which raises in
# `_delete_nodes` and lands on the abort path (`delete_unverified=True`) → exit 2 ("may
# have evicted") on a run that removed nothing. That is a false alarm on the destructive
# exit code, which is exactly the shape this PR has already graded P1 twice.
# --------------------------------------------------------------------------------------
def test_an_EMPTY_key_property_is_refused():
    """Interpolating nothing into a DELETE is never an identifier."""
    from tortoise.cache_evictor import EvictionConfigError, _key_property

    for empty in ("", "   ", "``", None, 5):
        with pytest.raises(EvictionConfigError):
            _key_property(empty)          # type: ignore[arg-type]


def test_an_ALREADY_QUOTED_key_property_is_stripped_not_double_quoted():
    """A caller quoting defensively is doing the right thing.

    `_quote_identifier` quotes a non-bare name, so handing it ``` `id` ``` would produce
    ``` ```id``` ```, which the engine rejects mid-sweep.
    """
    from tortoise.cache_evictor import _key_property

    assert _key_property("`id`") == "id"
    assert _key_property("id") == "id"


def test_a_key_property_that_is_not_a_bare_identifier_is_QUOTED():
    from tortoise.cache_evictor import _key_property

    assert _key_property("my key") == "`my key`"


def test_a_key_property_carrying_a_TRAILING_NEWLINE_is_still_safe():
    """`str.isidentifier()` accepts a trailing newline; the engine reads it as the name."""
    from tortoise.cache_evictor import _key_property

    got = _key_property("id\n")
    assert got == "id", got


def test_a_pathological_key_property_refuses_the_pass_without_issuing_a_mutation():
    """The gate is on the PASS, not only the helper: nothing is ISSUED.

    ⚠️ "Nothing is READ" would be false, and an earlier version of this docstring said it:
    the pass reads the node count (`node_total`) and `INFO memory` BEFORE the config gate,
    so `graph.mutating` — not `graph.statements` — is the property asserted here. The
    executor deliberately checks the CENSUS verdict before resolving the policy; the
    statement, not every read, is what a refused config prevents.
    """
    graph = _FakeGraph()
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record, key_prop="",
        dry_run=False, policy=EvictionPolicy(max_nodes=1),
    )

    assert report.verdict == "REFUSED"
    assert "config refused" in report.reason, report.reason
    assert graph.mutating == [], f"a refused config mutated: {graph.mutating}"


def test_a_MIXED_selection_aborts_rather_than_sweeping_the_other_population():
    """A selector that labels a candidate with another population must ABORT.

    The candidate would otherwise be counted against the licence of the population being
    swept and then deleted — a node no instrument measured for THIS population. The block
    was pinned by nothing: deleting it left 84 tests green.
    """
    graph = _FakeGraph()
    other = [Candidate(node_id=0, key="k0", population="somewhere else")]

    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: other,
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=1),
    )

    assert report.verdict == "REFUSED"
    assert "mixed selection" in report.reason, report.reason
    assert graph.mutating == [], f"a mixed selection mutated: {graph.mutating}"


# --------------------------------------------------------------------------------------
# GATE 5 — the graph. The licence is scoped to the graph the CENSUS measured.
# --------------------------------------------------------------------------------------
def test_a_plan_for_one_graph_never_deletes_from_another():
    """`Assessment.state.graph_name` was never read by the executor.

    The census makes the graph binding REQUIRED (`load_parity_report(graph_name=…)`,
    #11097) precisely because "a CLEAN census of graph A licensed evicting graph B".
    The executor is the half that DELETES, so it must re-check the binding at the last
    hop — otherwise a licence measured on one graph is applied to whatever handle the
    caller passed.
    """
    graph = _FakeGraph(nodes=10, name="graph_b")
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    assert report.verdict == "REFUSED"
    assert "graph_b" in report.reason and "'g'" in report.reason
    assert graph.deletes == [], "swept a graph the census never measured"
    assert graph.mutating == []


def test_a_handle_that_cannot_name_its_graph_is_refused():
    """A duck-typed stub is exactly the handle most likely to point at the wrong graph.

    "I cannot tell which graph this is" is not "it matches" — the same doctrine
    `_read_statement` applies to a handle with no read-only verb.
    """
    graph = _FakeGraph(nodes=10, name=None)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    assert report.verdict == "REFUSED"
    assert "does not name its graph" in report.reason
    assert graph.deletes == []


def test_an_assessment_with_no_graph_binding_is_refused():
    """`CacheState.graph_name` is a required field, but an empty one binds nothing."""
    def _unbound():
        a = _assessment()
        a.state = _state(graph_name="")
        return a

    graph = _FakeGraph(nodes=10)
    report = evict(
        _unbound(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: _candidates(10)[:limit],
        record_probe=_record, dry_run=False, policy=EvictionPolicy(max_nodes=2),
    )
    assert report.verdict == "REFUSED"
    assert "did not bind a graph name" in report.reason
    assert graph.deletes == []


# --------------------------------------------------------------------------------------
# the bound cannot be smuggled past `EvictionPolicy.__post_init__`
# --------------------------------------------------------------------------------------
def test_a_foreign_policy_object_cannot_lower_the_bound_to_zero():
    """`policy or EvictionPolicy.resolve()` accepted ANY object.

    `EvictionPolicy.__post_init__` floors `max_nodes` at 1 *because* a bound of zero
    empties the cache one batch at a time. An object that merely LOOKS like a policy
    skips that validation entirely, so the only defence is an `isinstance` check.
    """
    from types import SimpleNamespace

    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=SimpleNamespace(max_nodes=0, batch_size=100, max_passes=10),
    )
    assert report.verdict == "REFUSED", "a bound of zero was honoured"
    assert "EvictionPolicy" in report.reason
    assert graph.deletes == [], "a fabricated bound emptied the graph"


# --------------------------------------------------------------------------------------
# a duplicated licence entry TIGHTENS
# --------------------------------------------------------------------------------------
def test_a_duplicated_licence_entry_takes_the_smaller_proven_count():
    """Last-wins let a second entry WIDEN a licence the first had already bounded."""
    graph = _FakeGraph(nodes=10)
    offered = _candidates(10)
    report = evict(
        _assessment(evictable=[
            {"population": POPULATION, "nodes": 2},
            {"population": POPULATION, "nodes": 10},
        ]),
        _FakeClient(), graph,
        population=POPULATION, selector=lambda g, limit, pop: offered[:limit],
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=100, max_passes=5),
    )
    assert len(report.deleted) == 2, (
        f"evicted {len(report.deleted)} on a licence whose smaller proof was 2"
    )


# --------------------------------------------------------------------------------------
# three gates the comment argues for and no test could fail (found by mutation)
# --------------------------------------------------------------------------------------
def test_a_store_fault_on_the_PRE_PASS_count_refuses_and_issues_nothing():
    """⛔ `evict()` contracts "never raise" for a store fault. The pre-pass `node_total`
    was the one mapping no test exercised: narrowing it to an exception that never fires
    left the whole suite green, and a regression lets `ConnectionError`/`CacheReadError`
    escape through the CLI's `try:/finally:` (which has no `except`) and out of `main()`
    — exit 1, this file's USAGE code, "retry the FLAGS", for a store fault.
    """
    graph = _FakeGraph(fail_first_count=True)

    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=_record, dry_run=False,
        policy=EvictionPolicy(max_nodes=1),
    )

    assert report.verdict == "REFUSED"
    assert "could not be read" in report.reason, report.reason
    assert graph.mutating == [], f"a refused pre-pass read mutated: {graph.mutating}"


def test_a_lease_taken_DURING_the_probe_of_the_SAME_node_still_wins():
    """⛔ The plan-time check is not enough — the comment says so; this is its pin.

    The existing live case leases a DIFFERENT node than the one being probed, so the
    plan-time check caught it and the pre-delete re-snapshot never fired: replacing
    `holds = leases.held()` with `frozenset()` left every test green. Here the probe
    acquires the lease for the candidate it is currently answering for, which only the
    re-snapshot can honour.
    """
    leases = ReadLeases()
    leased: set[int] = set()

    def probing(candidate) -> bool:
        if candidate.node_id == 0:
            leases.acquire(0)          # the read starts INSIDE the probe→delete window
            leased.add(0)
        return True

    graph = _FakeGraph()
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=probing, leases=leases, dry_run=False,
        policy=EvictionPolicy(max_nodes=6, batch_size=10),
    )

    assert leased == {0}
    assert 0 in [c.node_id for c in report.skipped_leased], report.skipped_leased
    assert 0 not in [c.node_id for c in report.deleted], (
        "a node leased DURING its own probe was deleted")


def test_a_node_that_becomes_SERVABLE_leaves_the_held_list():
    """⛔ Otherwise a node is DELETED while still listed as unservable.

    `metric()["held_record_cannot_serve"]` would over-report and any monitor intersecting
    `deleted` with `skipped_record_cannot_serve` — the probe's own `unservable_evicted`
    pattern — raises a false alarm on the invariant that matters most. Disabling the arm
    left the whole suite green.
    """
    seen: set[int] = set()

    def probing(candidate) -> bool:
        first = candidate.node_id not in seen
        seen.add(candidate.node_id)
        return not first              # unservable on pass 1, servable on pass 2

    graph = _FakeGraph(nodes=10)
    report = evict(
        _assessment(), _FakeClient(), graph,
        population=POPULATION,
        selector=lambda g, limit, pop: _candidates(limit),
        record_probe=probing, dry_run=False,
        policy=EvictionPolicy(max_nodes=5, batch_size=5, max_passes=3),
    )

    deleted_ids = {c.node_id for c in report.deleted}
    held_ids = {c.node_id for c in report.skipped_record_cannot_serve}
    assert deleted_ids, "the second pass evicted nothing, so the arm is not exercised"
    assert not (deleted_ids & held_ids), (
        f"deleted AND reported as unservable: {sorted(deleted_ids & held_ids)}")


def test_a_journalled_lane_graph_name_must_be_in_the_ownership_vocabulary(monkeypatch):
    """#11373 P1 — the journal reclaims ONLY a name `_SWEEP_OWNED_PREFIXES` covers.

    `owns_by_ownership_record` is a PREFIX predicate, and it is what BOTH halves of
    the sweep apply: `_sweep_drop` deletes only names it covers, and the E2E-7 leak
    census `_owned_survivors` counts through the same filter. So journalling a name
    outside the vocabulary (`evict_…`, this fixture's pre-fix shape) bought the
    PEER-protection half only — `_peer_journaled_graphs` reads journal names
    prefix-blind — while the reclamation half stayed inert: the sweep put the graph
    in `preserved` and then consumed the journal anyway, so a run interrupted before
    its `finally:` leaked that graph PERMANENTLY, and the census could not even
    report it.

    ⛔ `tests/test_derived_names.py`'s raw-client census is not evidence about this: it
    checks that a per-run `os.urandom` and a `_journal_append(` CALL appear in the same
    block, never the vocabulary of the name journalled. This pin is what settles it.

    Hermetic on purpose (no engine): the live suite `test_cache_eviction_evictor.py`
    `allow_module_level`-skips without a URI, so a pin living there would not run in
    the PR lane that this defect would have merged through — and it cannot be imported
    here for the same reason, since the skip fires at import. So the fixture is COMPILED
    from that file with its decorators and module-level skip removed, its three I/O
    surfaces stubbed, and EXECUTED: the verdict is the name it actually journals versus
    the name it actually mints, not a reading of its source text.
    """
    import ast
    import os
    from pathlib import Path

    from tests._embedded import _SWEEP_OWNED_PREFIXES, owns_by_ownership_record

    # The shape `test_cache_eviction_evictor.py`'s `lane` fixture builds INLINE (they
    # must agree; the AST coupling at the bottom of this test asserts it).
    name = f"test_evict_{os.urandom(6).hex()}"
    # ⛔ No `name.startswith("test_")` row here: `name` is built with that prefix
    # two lines up, so it was true by construction and could never red. The
    # predicate below is the one that discriminates (the sweep's own
    # `owns_by_ownership_record` against `_SWEEP_OWNED_PREFIXES`), and the
    # executed COUPLING block at the bottom holds the fixture to the same name at
    # runtime.
    assert owns_by_ownership_record(name), (
        f"{name!r} is OUTSIDE the ownership vocabulary {_SWEEP_OWNED_PREFIXES} — "
        "journalling it reclaims nothing and leaks the graph on an interrupted run")

    # The PRE-FIX shape, asserted as the thing that must stay unowned: if this ever
    # flips, the fixture's prefix rationale has changed and this pin says so.
    assert owns_by_ownership_record(f"evict_{os.urandom(6).hex()}") is False, (
        "the bare `evict_` prefix is now ownership-covered — re-read #11373 P1 "
        "before assuming the fixture's `test_` prefix is still load-bearing")

    # ⛔ COUPLING — and it is EXECUTED, not read off the source. Three static drafts were
    # each measured wrong here (#11373 review, three rounds), and the reason is the same
    # every time: a static pass can only answer "what does the text say", while the
    # property is "what NAME is journalled at runtime".
    #   1. a whole-file substring — GREEN when the f-string moved to a helper that still
    #      contained the line, RED when the local was merely renamed;
    #   2. a set of identifier NAMES assigned an owned name somewhere in `lane` — GREEN
    #      when an owned assignment was followed by an `evict_…` one, or when the two sat
    #      in different branches, because it never asked which assignment wins;
    #   3. a static pass over `Assign`-to-`Name` only — blind to `AnnAssign`, walrus,
    #      tuple targets, `for`/`with`/`except as`, so every one of those rebound the name
    #      to an unowned one with the pin still GREEN; and it never related the journalled
    #      name to the name the graph is actually MINTED under, so renaming the mint local
    #      leaked an unowned graph on EVERY run (teardown deleted a name never created).
    # Every one of those is the same consequence: the journal is INERT for reclamation,
    # `_sweep_drop` preserves the graph and consumes the record anyway, and the graph
    # leaks permanently (#11373 P1).
    # So this compiles `lane` on its own, stubs the three things it touches that would
    # otherwise do I/O, and runs it: whatever the source looks like, the pin asserts what
    # is JOURNALLED, what is MINTED, and that the two are the same owned, per-run name.
    #
    # ⛔ SCOPE, stated rather than implied: this pins the VOCABULARY half (an owned name),
    # the per-run half, and that the journal names the graph that is minted. It does NOT
    # pin WHERE the name is built — the sibling census owns the same-block half, and
    # genuinely cannot see a helper extraction whose block keeps prose naming
    # `os.urandom` (measured; #11182 masked only the ownership half). Extraction is not a
    # leak by itself, so leaving it to the census is deliberate.
    import sys
    import types

    live_src = (Path(__file__).resolve().parent / "test_cache_eviction_evictor.py").read_text(
        encoding="utf-8")
    module_ast = ast.parse(live_src)
    assert any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "lane"
               for n in module_ast.body), (
        "`test_cache_eviction_evictor.py` no longer defines a top-level `lane` fixture, "
        "so this coupling cannot execute the block it pins — re-read #11373 P1 instead "
        "of deleting it")
    # Compile the module's IMPORTS, its defs and its LITERAL constants — but not the
    # module-level `pytest.skip(allow_module_level=True)`, and not a non-literal constant
    # (`TURN_INSTRUMENT = …[...]`, which would drag a real import in). The imports are
    # included because the fixture file has `import os` at module level, and a helper that
    # uses it is the NATURAL extraction — dropping them made that refactor die inside the
    # helper and reported it as this probe's own breakage (measured). `_live_utils` is
    # overridden after the exec, so the real one never reaches the fixture.
    # Decorators say how the SUITE uses these objects, not what they build, so they go.
    probe_body = []
    for node in module_ast.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            probe_body.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            node.decorator_list = []
            probe_body.append(node)
        elif isinstance(node, ast.Assign):
            try:
                ast.literal_eval(node.value)
            except (ValueError, SyntaxError, TypeError):
                continue
            probe_body.append(node)
    probe_module = ast.Module(body=probe_body, type_ignores=[])

    # ONE ORDERED LOG, not two sets: the property is that the ownership record exists
    # BEFORE the graph does. Reading two collections after `gen.close()` cannot tell
    # "journalled in setup" from "journalled in the teardown" (measured: moving the call
    # into `finally:` left the earlier draft GREEN), and it cannot see a MINT that
    # precedes the journal — either way an interrupted run leaves a graph with no
    # ownership record, which is #11373 P1 exactly.
    events: list[tuple[str, str]] = []

    class _Graph:
        def query(self, *a, **k):
            return None

    class _FalkorDB:
        def _record(self, name):
            events.append(("mint", name))
            return _Graph()

        select_graph = _record
        create_graph = _record

    deletes: list[str] = []
    redis_stub = types.ModuleType("redis")
    # ⛔ VARIADIC, not `name`: `redis.delete` takes `*names`, and a one-argument stub let
    # `client.delete(graph_name, "test_evict_a_live_peer")` through GREEN — destroying a
    # peer's LIVE owned graph is exactly the #11373 harm, so the stub must see every name.
    redis_stub.Redis = lambda *a, **k: types.SimpleNamespace(
        delete=lambda *names, **k2: deletes.extend(names))
    falkordb_stub = types.ModuleType("falkordb")
    falkordb_stub.FalkorDB = lambda *a, **k: _FalkorDB()
    live_utils_stub = types.SimpleNamespace(
        service_host=lambda: "127.0.0.1",
        docker_port=lambda: 6379,
        docker_password=lambda: "",
    )

    import tests._embedded as embedded

    def _journal_ok(name):
        events.append(("journal", name))

    monkeypatch.setitem(sys.modules, "redis", redis_stub)
    monkeypatch.setitem(sys.modules, "falkordb", falkordb_stub)
    monkeypatch.setattr(embedded, "_journal_append", _journal_ok)

    # NOTE: no `from __future__ import annotations` node is injected here, and adding one
    # is a no-op: `compile(..., dont_inherit=False)` picks up the CALLING module's future
    # flags, and this file carries `from __future__ import annotations` at its own top, so
    # the exec'd fixture's annotations are ALREADY lazy (measured: deleting the injection
    # leaves this pin green). The earlier draft injected one and claimed the probe would
    # otherwise die with a `NameError` — that claim did not reproduce; the injection was
    # inert and left the assignment above as a dead store.
    namespace: dict = {}
    exec(compile(probe_module, "<lane fixture>", "exec"), namespace)
    # The module's own import of `tests._live_utils` just bound the REAL helper, which
    # would dial a live service. Replace it AFTER the exec — `lane` resolves it from the
    # module globals at CALL time, so the stub is what the fixture actually sees.
    #
    # ⛔ This file STUBS the seam; it does not read it. `tests/test_runner_ports_6673.py`
    # decides seam membership by the IMPORT EDGE (`_imports_the_seam`), not by scanning raw
    # text (`#11437`), so this file is outside the split-brain census because it imports
    # nothing — the `_live_utils` mentions above and below it are a comment and a dict key,
    # which is why they are harmless. A real `import` here WOULD put it back in the census,
    # and `test_the_mocked_seam_is_really_mocked_and_needs_no_exemption` in that file reds.
    namespace["_live_utils"] = live_utils_stub

    def _run_lane():
        """Drive the fixture to its `yield`; return the log AS OF that point."""
        del events[:], deletes[:]
        try:
            gen = namespace["lane"]()
            next(gen)
            at_yield = list(events)
            setup_deletes = list(deletes)
            gen.close()
        except (NameError, TypeError, AttributeError, StopIteration) as exc:
            raise AssertionError(
                f"this probe no longer reaches `lane`'s yield ({exc!r}) — extend the "
                "stubs/probe above, do not drop the pin") from exc
        assert events == at_yield, (
            "`lane`'s TEARDOWN minted or journalled: the ownership record must be "
            "written in the setup path, because `finally:` does not run on an "
            f"interrupted run — the teardown added {events[len(at_yield):]}")
        assert setup_deletes == [], (
            f"`lane` DELETED {setup_deletes} during setup — deleting is the teardown's "
            "job; a setup-path delete would drop a graph whose record it still needs")
        return at_yield

    first_run = _run_lane()
    mints = [name for kind, name in first_run if kind == "mint"]
    assert len(mints) == 1, (
        f"`lane` minted {len(mints)} graphs ({mints}) — this probe relates ONE mint to "
        f"its journal record: measured {first_run}")
    minted_name = mints[0]
    # The PROPERTY, not an exact sequence: a second journal of the SAME name is legal and
    # idempotent (`tests/_embedded.py`: "Duplicate entries (per-test seam re-appends) are
    # idempotent — dedupe once"), so requiring `["journal", "mint"]` exactly would red a
    # correct fixture. What must hold is that the record exists BEFORE the graph does.
    before_mint = first_run[:first_run.index(("mint", minted_name))]
    assert ("journal", minted_name) in before_mint, (
        "`lane` must JOURNAL the graph name BEFORE it MINTS the graph — an interrupted "
        "run must still leave the ownership record the sweep reads: measured "
        f"{first_run}")
    assert owns_by_ownership_record(minted_name), (
        f"`lane` mints {minted_name!r}, which is OUTSIDE the ownership vocabulary "
        f"{_SWEEP_OWNED_PREFIXES} — `_sweep_drop` preserves it and consumes the journal "
        "anyway, so an interrupted run leaks it permanently (#11373 P1)")
    assert deletes == [minted_name], (
        f"`lane`'s teardown deleted {deletes}, not the graph it created "
        f"({minted_name!r}) — deleting a name that was never created leaves the real "
        "graph behind while looking cleaned up (#11373 P1)")
    # The SET, not just "the minted name is in there": `_sweep_drop` DETACH+DELETEs every
    # journalled name the vocabulary covers (`unique = dict.fromkeys(names)` → `owned` →
    # `_drop_one_graph`), so a name this fixture journals but never creates is still
    # dropped at session end — and if it names a live peer's graph, that peer's graph is
    # destroyed. Measured green without this arm (an extra `_journal_append("test_…")`
    # after the mint). A duplicate journal of the SAME name stays legal.
    journalled_names = {name for kind, name in first_run if kind == "journal"}
    assert journalled_names == {minted_name}, (
        f"`lane` journalled {sorted(journalled_names)} but minted only {minted_name!r} — "
        "the sweep drops EVERY owned name in the journal, so journalling a graph this "
        "fixture never created can delete a peer's live graph (#11373 P1)")

    # Compare MINT to MINT. Reading the second run's first EVENT compared a decoy journal
    # against the first run's mint, so a FIXED name passed whenever anything else was
    # journalled first (measured); and an empty second run raised IndexError instead of
    # asserting. Both are fixed by deriving the second run's mint explicitly.
    second_mints = [name for kind, name in _run_lane() if kind == "mint"]
    assert second_mints, (
        "`lane`'s second invocation minted no graph at all — the per-run arm cannot tell "
        "a FIXED name from a cached one unless a second run mints (#11373 P1)")
    assert second_mints[0] != minted_name, (
        f"`lane` minted {minted_name!r} twice — the name is FIXED, not per-run, so two "
        "concurrent sessions DETACH each other's live writes (#11373 P1)")

    # ⛔ A CALL IN THE RIGHT POSITION IS NOT A RECORD. `_journal_append` raises
    # `RuntimeError` by design when the write fails, so the caller drops what it created
    # (`tests/_embedded.py`, #3214) — and a fixture that swallows that and mints anyway
    # leaves an UNOWNED graph, the same permanent leak. The stub above never fails, so a
    # drive with a raising stub is what actually pins the "stop at the first unowned
    # mint" contract (measured: the swallowing shape was GREEN without this drive).
    def _journal_fails(name):
        raise RuntimeError(f"journal write failed for {name!r}")

    monkeypatch.setattr(embedded, "_journal_append", _journal_fails)
    # ⛔ AND every namespace binding that ALIASED the happy-path stub — BY IDENTITY, not
    # by name. The module attribute covers an in-function `from tests._embedded import
    # _journal_append` (read at CALL time), but a fixture that hoists that import froze
    # the HAPPY-path stub into the exec'd namespace, so patching only the module left
    # this drive calling the stub — and a correct fixture was then accused of minting
    # after a failed journal (measured). A name-keyed rebind (`setitem(namespace,
    # "_journal_append", …)`) covered only the literal key: the same hoist spelled
    # `as _lane_journal`, or `_LANE_JOURNAL = _journal_append`, freezes the SAME object
    # under another key and the drive stayed falsely RED (measured on a CORRECT fixture).
    # Swapping every value that `is` the stub is spelling-independent by construction.
    rebound = [key for key, value in namespace.items() if value is _journal_ok]
    for key in rebound:
        monkeypatch.setitem(namespace, key, _journal_fails)
    del events[:], deletes[:]
    try:
        gen = namespace["lane"]()
        next(gen)
    except RuntimeError:
        pass
    else:
        gen.close()
        raise AssertionError(
            "`lane` MINTS a graph even when `_journal_append` raises — a swallowed "
            "journal failure leaves an UNOWNED graph the sweep preserves and can never "
            "report (#11373 P1, tests/_embedded.py::_journal_append)")
    assert not [e for e in events if e[0] == "mint"], (
        f"`lane` created {events} after the journal write failed — no graph may exist "
        "without an ownership record (#11373 P1)")
