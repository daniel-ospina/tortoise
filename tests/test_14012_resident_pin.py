"""#14012 — *keep this resident*: the pin, its closure, and its BOUND (hermetic).

WHY THIS SUITE IS HERMETIC
--------------------------
The controls' whole safety story is DECISION logic — does the cap refuse, does the gate
hold a pinned node back, does an unreadable declaration stop a sweep — and every one of
those is answerable without an engine. The live suite
(`tests/test_cache_eviction_evictor.py`) is `uri_requiring`, and `ci_selection`
subtracts a `uri_requiring` file from the tier-2 PR lane: left as the only coverage, a
change to the pin gate would merge on a green PR with **no pin test executed at all**,
which is the silent-green class this repo treats as a defect.

⛔ The fake graph EXECUTES the delete and records every statement, so "a pinned node
survived" and "a refused declaration issued no mutating statement" are asserted against
state rather than argued from a dry run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.cache_eviction import Assessment, CacheState
from tortoise.cache_evictor import Candidate, EvictionPolicy, evict
from tortoise.resident_pin import (
    PINS_META_KEY,
    PinBudget,
    PinConfigError,
    PinRefused,
    pin,
    read,
    unpin,
)

POPULATION = "turn"
MUTATING = ("DETACH DELETE", "DELETE ", "MERGE", "CREATE", "REMOVE", "DROP")


class _Result:
    def __init__(self, rows: list[list]) -> None:
        self.result_set = rows


class _FakeGraph:
    """A tiny graph: numbered nodes with an ``id`` key, undirected edges, one ``:Meta``.

    It models exactly the statements `resident_pin` and `cache_evictor` issue, and
    EXECUTES the delete so a survivor is a fact about state rather than about a plan.
    """

    def __init__(self, keys: list[str] | None = None, *, name: str = "g") -> None:
        self.name = name
        self.nodes: dict[int, dict] = {}
        self.edges: set[tuple[int, int]] = set()
        self.meta: dict[str, dict] = {}
        self.statements: list[str] = []
        for key in keys or []:
            self.add(key)

    # ── building state ────────────────────────────────────────────────
    def add(self, key: str, *, extra: dict | None = None) -> int:
        node_id = (max(self.nodes) + 1) if self.nodes else 0
        props = {"id": key, "key": key}
        props.update(extra or {})
        self.nodes[node_id] = props
        return node_id

    def link(self, a: str, b: str) -> None:
        ia = next(i for i, p in self.nodes.items() if p["id"] == a)
        ib = next(i for i, p in self.nodes.items() if p["id"] == b)
        self.edges.add((min(ia, ib), max(ia, ib)))

    def id_of(self, key: str) -> int | None:
        for node_id, props in self.nodes.items():
            if props.get("id") == key:
                return node_id
        return None

    def neighbours(self, node_id: int) -> set[int]:
        out = set()
        for a, b in self.edges:
            if a == node_id:
                out.add(b)
            elif b == node_id:
                out.add(a)
        return out

    def node_keys(self) -> list[str]:
        return sorted(p["id"] for p in self.nodes.values())

    @property
    def mutating(self) -> list[str]:
        return [s for s in self.statements if any(t in s.upper() for t in MUTATING)]

    # ── the engine ────────────────────────────────────────────────────
    def ro_query(self, statement: str, params=None):
        return self.query(statement, params=params)

    def query(self, statement: str, params=None):
        self.statements.append(statement)
        params = params or {}

        if "MATCH (n:Meta {key:$key}) RETURN n.label, n.key_prop, n.keys" in statement:
            entry = self.meta.get(params["key"])
            if entry is None:
                return _Result([])
            return _Result([[entry.get("label"), entry.get("key_prop"),
                             entry.get("keys")]])

        if "MERGE (n:Meta {key:$key}) SET n += $props" in statement:
            self.meta.setdefault(params["key"], {}).update(params["props"])
            return _Result([])

        if "MATCH (n:Meta {key:$key}) DETACH DELETE n" in statement:
            self.meta.pop(params["key"], None)
            return _Result([])

        if "UNWIND $keys AS k" in statement:
            rows = []
            for key in params["keys"]:
                node_id = self.id_of(key)
                if node_id is None:
                    continue  # a MATCH that finds nothing returns no row
                nbrs = self.neighbours(node_id)
                rows.append([
                    key,
                    sorted(nbrs),
                    [self.nodes[i]["id"] for i in sorted(nbrs) if "id" in self.nodes[i]],
                ])
            return _Result(rows)

        # ⛔ The label count MUST be matched before the bare count: `"MATCH (n) RETURN
        # count(n)"` is a substring of neither, but the bare form would otherwise be
        # found first by a looser check.
        if "MATCH (n:Point) RETURN count(n)" in statement:
            return _Result([[sum(1 for p in self.nodes.values() if "id" in p)]])

        if "MATCH (n:Point) RETURN n.id AS key LIMIT $limit" in statement:
            limit = int(params["limit"])
            return _Result([[p["id"]] for p in
                            list(self.nodes.values())[:limit] if "id" in p])

        if statement.startswith("MATCH (n) RETURN count(n)"):
            return _Result([[len(self.nodes)]])

        if "DETACH DELETE n RETURN count(n)" in statement:
            removed = 0
            for row in params["rows"]:
                node_id = row["id"]
                props = self.nodes.get(node_id)
                if props is None or props.get("id") != row["key"]:
                    continue  # the delete binds on id AND key (FalkorDB reuses ids)
                del self.nodes[node_id]
                self.edges = {e for e in self.edges if node_id not in e}
                removed += 1
            return _Result([[removed]])

        if "RETURN id(n)" in statement:
            rows = params.get("rows") or []
            return _Result([[r["id"]] for r in rows if r["id"] in self.nodes])

        raise AssertionError(f"unexpected statement: {statement}")


class _FakeClient:
    def info(self, section: str):
        assert section == "memory"
        return {"used_memory": 1_000_000}


def _state(*, graph_name: str = "g") -> CacheState:
    return CacheState(
        graph_name=graph_name,
        resident_bytes=1_000_000,
        maxmemory_bytes=0,
        maxmemory_policy="noeviction",
        ceiling_bytes=1,
        ceiling_source="declared (test)",
        graph_key_ttl_seconds=-1,
        keys_per_graph=1,
        graph_memory_usage_mb=None,
        graph_memory_usage_note=None,
    )


def _assessment(nodes: int) -> Assessment:
    return Assessment(
        state=_state(),
        classes=(),
        verdict="PLAN",
        plan={"evictable": [
            {"population": POPULATION, "nodes": nodes,
             "instrument": "tools/turn_parity_census.py"}
        ]},
    )


def _record(candidate: Candidate) -> bool:
    return candidate.key is not None


def _coldest(graph: _FakeGraph, limit: int, population: str) -> list[Candidate]:
    """The selector the score would produce: every node, coldest key first."""
    return [Candidate(node_id=i, key=p["id"], population=population)
            for i, p in sorted(graph.nodes.items())[:limit]]


# ── the budget ──────────────────────────────────────────────────────────────────
def test_the_budget_is_a_tenth_of_the_window_and_an_equal_claim_is_refused():
    budget = PinBudget.resolve(max_nodes=None, window_nodes=20_000)
    assert budget.max_nodes == 2_000, (
        "the derived cap is what makes 'it can never take over the fast layer' true "
        "without the operator declaring a second number"
    )
    assert budget.max_nodes < budget.window_nodes
    assert "2000 fast-layer slot(s)" in budget.explain().replace("2,000", "2000")

    with pytest.raises(PinConfigError) as exc:
        PinBudget(max_nodes=20_000, window_nodes=20_000)
    assert "not smaller than the residency window" in str(exc.value)


def test_a_budget_with_no_window_and_no_declaration_is_a_refusal(monkeypatch):
    monkeypatch.delenv("TORTOISE_PIN_MAX_NODES", raising=False)
    monkeypatch.delenv("TORTOISE_CACHE_MAX_NODES", raising=False)
    with pytest.raises(PinConfigError) as exc:
        PinBudget.resolve(max_nodes=None, window_nodes=None)
    assert "unbounded claim on the fast layer" in str(exc.value)


def test_a_budget_of_zero_is_a_disabled_control_not_a_budget():
    with pytest.raises(PinConfigError):
        PinBudget(max_nodes=0)
    with pytest.raises(PinConfigError):
        PinBudget(max_nodes=10, max_degree=0)


# ── the declaration ─────────────────────────────────────────────────────────────
def test_pin_writes_the_declaration_and_reads_back_its_closure():
    g = _FakeGraph(["a", "b", "c", "d"])
    g.link("a", "b")
    budget = PinBudget(max_nodes=10)

    result = pin(g, ["a"], budget=budget)

    assert result.written is True
    assert result.added == frozenset({"a"})
    assert g.meta[PINS_META_KEY]["keys"] == ["a"]
    # ⛔ The CLOSURE is derived from the graph on read, never stored: a stored copy
    # goes stale the moment an edge is added, and a stale copy under-protects.
    assert "b" not in g.meta[PINS_META_KEY]["keys"]

    reading = read(g, budget=budget)
    assert reading.closure == frozenset({"a", "b"})
    assert reading.keys == frozenset({"a"})
    assert reading.degrees == {"a": 1}
    assert reading.over_budget() is None


def test_the_pin_budget_refuses_with_a_declared_message_and_writes_nothing():
    g = _FakeGraph(["a", "b", "c", "d"])
    g.link("a", "b")
    g.link("a", "c")
    before = list(g.mutating)
    budget = PinBudget(max_nodes=2)  # a + b + c = 3 slots

    with pytest.raises(PinRefused) as exc:
        pin(g, ["a"], budget=budget)

    assert exc.value.code == "budget"
    assert "would need 3 fast-layer slot(s)" in exc.value.reason
    assert "at most 2 fast-layer slot(s)" in exc.value.reason
    assert "Nothing was pinned" in exc.value.reason
    assert g.mutating == before, "a refused declaration issued a mutating statement"
    assert PINS_META_KEY not in g.meta


def test_a_hub_above_the_degree_ceiling_is_refused_rather_than_pinned():
    g = _FakeGraph([f"n{i}" for i in range(10)])
    for i in range(1, 6):
        g.link("n0", f"n{i}")
    budget = PinBudget(max_nodes=100, max_degree=4)

    with pytest.raises(PinRefused) as exc:
        pin(g, ["n0"], budget=budget)

    assert exc.value.code == "degree"
    assert "degree ceiling of 4" in exc.value.reason
    assert "n0 (5 connections)" in exc.value.reason
    assert PINS_META_KEY not in g.meta
    # The way out the refusal names must actually work.
    pin(g, ["n1"], budget=budget)
    assert read(g, budget=budget).keys == frozenset({"n1"})


def test_a_key_the_fast_layer_does_not_hold_is_refused():
    g = _FakeGraph(["a"])
    with pytest.raises(PinRefused) as exc:
        pin(g, ["a", "ghost"], budget=PinBudget(max_nodes=10))
    assert exc.value.code == "unknown-node"
    assert "ghost" in exc.value.reason
    assert PINS_META_KEY not in g.meta


def test_an_empty_declaration_is_refused_rather_than_written():
    g = _FakeGraph(["a"])
    with pytest.raises(PinRefused) as exc:
        pin(g, [], budget=PinBudget(max_nodes=10))
    assert exc.value.code == "empty"


def test_unpin_releases_the_key_and_removes_the_empty_declaration():
    g = _FakeGraph(["a", "b"])
    budget = PinBudget(max_nodes=10)
    pin(g, ["a"], budget=budget)
    assert read(g, budget=budget).keys == frozenset({"a"})

    result = unpin(g, ["a"], budget=budget)

    assert result.written is True
    assert PINS_META_KEY not in g.meta, (
        "an emptied declaration must be REMOVED, not left behind claiming nothing — a "
        "reader cannot tell a node that claims no keys from a graph that was never "
        "pinned, and a rebuild is the moment that difference matters"
    )
    assert g.node_keys() == ["a", "b"], "unpinning must not touch the nodes themselves"


def test_unpinning_a_key_that_is_not_pinned_is_a_no_op_not_a_write():
    g = _FakeGraph(["a"])
    budget = PinBudget(max_nodes=10)
    pin(g, ["a"], budget=budget)
    before = list(g.mutating)

    result = unpin(g, ["zzz"], budget=budget)

    assert result.written is False
    assert g.mutating == before, "a no-op unpin issued a mutating statement"


# ── the gate: the score cannot override the pin ─────────────────────────────────
def test_a_pinned_node_and_its_neighbours_survive_a_full_window_cycle():
    """The done-when: a pinned node is not evicted by scoring, closure included."""
    g = _FakeGraph([f"p{i}" for i in range(6)])
    g.link("p0", "p1")          # p0 is pinned; p1 is the "things connected to them"
    budget = PinBudget(max_nodes=10)
    pin(g, ["p0"], budget=budget)
    pins = read(g, budget=budget)

    report = evict(
        _assessment(nodes=3), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=pins.gate(),
        dry_run=False,
        policy=EvictionPolicy(max_nodes=3, batch_size=10, max_passes=3),
    )

    assert "p0" in g.node_keys(), "the pinned node was evicted by the score"
    assert "p1" in g.node_keys(), (
        "the pinned node's neighbour was evicted — the docs page promises the things "
        "connected to it are kept resident too, and a pinned hub with no neighbours "
        "cannot complete a walk"
    )
    assert {c.key for c in report.deleted} == {"p2", "p3", "p4"}
    held = {c.key for c in report.skipped_pinned}
    assert held == {"p0", "p1"}
    assert report.metric()["held_pinned"] == 2
    # A pinned node is not reported as an unservable record row or as a held lease:
    # each of those sends an operator to a different, wrong remedy.
    assert report.skipped_record_cannot_serve == ()
    assert report.skipped_leased == ()


def test_the_pin_is_checked_before_the_record_probe_and_the_lease():
    """A pinned node is not "unservable"; the reason it was held matters."""
    g = _FakeGraph(["p0", "p1"])
    pins = read(g, budget=PinBudget(max_nodes=10))
    # A pin set built by hand — the declaration machinery is exercised above; this test
    # is about the GATE's ordering only.
    from tortoise.resident_pin import PinReading

    pinned = PinReading(label="Point", key_prop="id",
                        keys=frozenset({"p0"}), closure=frozenset({"p0"}),
                        budget=PinBudget(max_nodes=10))
    del pins

    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest,
        record_probe=lambda c: False,       # the record cannot serve ANYTHING here
        pinned=pinned.gate(),
        dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=2),
    )

    assert {c.key for c in report.skipped_pinned} == {"p0"}
    assert {c.key for c in report.skipped_record_cannot_serve} == {"p1"}
    assert report.deleted == ()


def test_the_pin_survives_every_pass_of_the_window_cycle():
    """No pass may take it: the gate is asked per candidate, per pass."""
    g = _FakeGraph([f"p{i}" for i in range(6)])
    from tortoise.resident_pin import PinReading

    pinned = PinReading(label="Point", key_prop="id",
                        keys=frozenset({"p0"}), closure=frozenset({"p0"}),
                        budget=PinBudget(max_nodes=10))
    report = evict(
        _assessment(nodes=3), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=pinned.gate(),
        dry_run=False,
        policy=EvictionPolicy(max_nodes=3, batch_size=1, max_passes=6),
    )
    assert "p0" in g.node_keys()
    assert report.metric()["passes"] >= 2, (
        "a one-pass run would not prove the gate is re-asked on a later pass"
    )


def test_a_pin_predicate_that_raises_holds_the_node_back():
    """Cannot-ask must never authorise a delete (the safe direction)."""
    g = _FakeGraph(["p0", "p1", "p2"])

    def boom(candidate: Candidate) -> bool:
        raise RuntimeError("pin store unreachable")

    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=boom,
        dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=2),
    )

    assert g.node_keys() == ["p0", "p1", "p2"], (
        "a pin predicate that could not answer deleted nodes — an unanswerable "
        "question about what the user pinned must hold the node back, never release it"
    )
    assert report.deleted == ()


def test_a_window_made_entirely_of_pinned_nodes_names_the_pin_as_the_reason():
    g = _FakeGraph(["p0", "p1", "p2"])
    from tortoise.resident_pin import PinReading

    pinned = PinReading(label="Point", key_prop="id",
                        keys=frozenset({"p0", "p1", "p2"}),
                        closure=frozenset({"p0", "p1", "p2"}),
                        budget=PinBudget(max_nodes=10))
    report = evict(
        _assessment(nodes=2), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=pinned.gate(),
        dry_run=False,
        # ⛔ Enough passes for the widening window to REACH the whole population: the
        # window starts at `budget` and doubles per pass, so a 3-node population is
        # fully examined on pass 3. With too few passes the stop reason is the pass
        # budget, which is a different (and honest) report.
        policy=EvictionPolicy(max_nodes=2, batch_size=10, max_passes=5),
    )
    assert report.verdict == "PARTIAL"
    assert "PINNED" in report.reason and "skipped_pinned" in report.reason, (
        "a window of pinned nodes reported as an exhausted population sends the next "
        f"operator to widen a selector instead of unpinning — got: {report.reason!r}"
    )


# ── the second half of the cap: state, not just the writer ──────────────────────
def test_a_pin_set_over_its_budget_refuses_the_sweep_and_issues_no_statement():
    g = _FakeGraph(["p0", "p1", "p2"])
    g.link("p0", "p1")
    g.link("p1", "p2")
    pin(g, ["p0", "p1"], budget=PinBudget(max_nodes=10, max_degree=10))

    # The budget SHRANK after the fact (an operator lowered the window): the closure is
    # now 3 against a cap of 2. Both "obvious" responses are wrong — evicting a pinned
    # node drops the user's declaration silently, and honouring it lets pins take over.
    shrunk = read(g, budget=PinBudget(max_nodes=2, max_degree=10))
    refusal = shrunk.gate_reason()
    assert refusal is not None
    assert "3 node(s)" in refusal and "at most 2" in refusal
    assert "Unpin" in refusal

    before = list(g.mutating)
    assert str(refusal)  # the gate reason is what the caller refuses with
    assert g.mutating == before


def test_an_unreadable_pin_declaration_refuses_the_sweep():
    class _Broken(_FakeGraph):
        def query(self, statement, params=None):
            if ":Meta" in statement and "RETURN" in statement:
                raise RuntimeError("transport blip")
            return super().query(statement, params=params)

    g = _Broken(["p0"])
    reading = read(g, budget=PinBudget(max_nodes=10))
    assert reading.readable is False
    refusal = reading.gate_reason()
    assert refusal is not None and "could not be read" in refusal
    assert reading.gate is not None  # the gate exists; the CALLER must refuse first


def test_a_malformed_declaration_is_unreadable_rather_than_empty():
    g = _FakeGraph(["p0"])
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": 7}
    reading = read(g, budget=PinBudget(max_nodes=10))
    assert reading.readable is False
    assert "malformed" in (reading.source or "")


def test_a_missing_declaration_is_the_normal_state_and_pins_nothing():
    g = _FakeGraph(["p0"])
    reading = read(g, budget=PinBudget(max_nodes=10))
    assert reading.readable is True
    assert reading.keys == frozenset()
    assert reading.closure == frozenset()
    assert reading.gate_reason() is None
    assert reading.gate()(Candidate(node_id=0, key="p0", population=POPULATION)) is False


def test_pin_refuses_to_extend_a_population_its_set_does_not_address():
    g = _FakeGraph(["p0"])
    pin(g, ["p0"], label="Point", key_prop="id", budget=PinBudget(max_nodes=10))
    with pytest.raises(PinRefused) as exc:
        pin(g, ["p0"], label="Source", key_prop="url", budget=PinBudget(max_nodes=10))
    assert exc.value.code == "population-mismatch"


def test_unpin_never_rewrites_the_population_a_set_addresses():
    """Releasing keys must not move a `Source`-keyed set to `Point`.

    Before this pin, `unpin` wrote back with its own `Point`/`id` defaults, so the
    surviving keys kept their strings but addressed nodes that do not exist — every one
    of them silently stopped being resident while the declaration still claimed them.
    """
    g = _FakeGraph(["a", "b"])
    budget = PinBudget(max_nodes=10)
    pin(g, ["a", "b"], label="Source", key_prop="url", budget=budget)
    assert g.meta[PINS_META_KEY]["label"] == "Source"

    unpin(g, ["a"], budget=budget)

    assert g.meta[PINS_META_KEY]["label"] == "Source"
    assert g.meta[PINS_META_KEY]["key_prop"] == "url"
    assert g.meta[PINS_META_KEY]["keys"] == ["b"]


def test_unpinning_with_nothing_declared_reports_no_write():
    """`written=True` over an absent declaration told an operator the state changed."""
    g = _FakeGraph(["a"])
    result = unpin(g, ["a"], budget=PinBudget(max_nodes=10))
    assert result.written is False
    assert result.reading is not None and result.reading.keys == frozenset()
    assert g.mutating == []


def test_a_degree_ceiling_is_a_declaration_time_guard_and_the_budget_is_the_invariant():
    """A hub pinned under a higher ceiling survives a lowered one — the budget does not.

    §7's ceiling exists to stop one DECLARATION pulling in a window-sized neighbourhood;
    the invariant that must hold on every read is the closure budget, so a pinned node
    that gains neighbours is caught there rather than refused for growing.
    """
    g = _FakeGraph([f"n{i}" for i in range(6)])
    for i in range(1, 5):
        g.link("n0", f"n{i}")
    pin(g, ["n0"], budget=PinBudget(max_nodes=100, max_degree=10))

    lowered = read(g, budget=PinBudget(max_nodes=100, max_degree=2))
    assert lowered.readable is True
    assert lowered.closure_size == 5
    assert lowered.over_budget() is None, "a legal set was refused for growing"

    shrunk = read(g, budget=PinBudget(max_nodes=3, max_degree=10))
    assert shrunk.over_budget() is not None
    assert "5 node(s)" in (shrunk.gate_reason() or "")
