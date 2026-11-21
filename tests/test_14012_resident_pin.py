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
    PinReading,
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
    # ⛔ AND THE REPORT MUST NOT CLAIM THEY ARE PINNED. The delete decision is the same
    # either way, but "the user pinned these" and "we could not ask" have different
    # remedies, and only one of them is reachable: folding the two into `skipped_pinned`
    # told an operator to unpin nodes that were never established as pinned — advice that
    # cannot clear the state, on the refusal an operator acts on.
    assert report.skipped_pin_unanswerable, (
        "the nodes held back by a RAISING predicate were not reported at all"
    )
    assert report.skipped_pinned == (), (
        "a predicate that RAISED was reported as `skipped_pinned` — the report claims a "
        "pin nobody established"
    )
    assert report.metric()["held_pin_unanswerable"] == len(
        report.skipped_pin_unanswerable)
    assert report.metric()["held_pinned"] == 0
    # ⛔ And no message may offer `unpin` for nodes nobody established as pinned: that
    # advice cannot clear this state, which is exactly what the split prevents.
    assert "PINNED (`skipped_pinned`)" not in report.reason, report.reason


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
def test_a_pin_set_over_its_budget_is_reported_as_a_refusal():
    """A set the budget no longer covers is a REFUSAL the caller can act on.

    ⛔ Named for what it asserts. It does NOT sweep: the sweep-level guard ("refuses
    before `evict`") lives in `tests/test_14012_resident_cli.py`, and an earlier name
    here claimed a sweep this body never ran.
    """
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

    # The sweep that honours this set is stopped by the CALLER (see the CLI suite's
    # `test_a_pin_set_over_its_budget_stops_the_sweep_before_evict`); what this suite owns
    # is that the gate REASON exists and names both ways out. An earlier version asserted
    # `g.mutating == before` here, which compared two empty lists and could not fail.


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
    # ⛔ No `assert reading.gate is not None` here: `gate` is a bound method on a frozen
    # dataclass, so that assertion was a tautology that could not vary under ANY
    # mutation of this diff (agent-infra#3442 rule 3) — it told a reader the gate was
    # covered when the refusal string above is what actually varies.


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


# ── review round 5: the guard no row saw, the partial list, the no-op cap ───────
def test_a_stored_declaration_with_an_injected_label_reads_as_malformed():
    """`read()`'s identifier guard is the LAST gate before a stored pair reaches Cypher.

    ⛔ Deleting it left the suite green. The `:Meta {key:'pins'}` node is operator metadata
    in a shared store (SDK, `graph-scripts`, a restore can MERGE it), so the value it
    validates is NOT one this module wrote; the existing malformed-declaration rows cover
    the TYPE arm (`keys=7`) and the corrupt-ENTRY arm, neither of which reaches
    `bare_identifier`. This row is the identifier arm's only coverage.
    """
    g = _FakeGraph(["p0"])
    g.meta[PINS_META_KEY] = {"label": "Point}) DETACH DELETE n //", "key_prop": "id",
                             "keys": ["p0"]}
    reading = read(g, budget=PinBudget(max_nodes=10))
    assert reading.readable is False
    assert (reading.source or "").startswith("malformed:")
    assert g.mutating == [], "a malformed declaration reached a query that mutates"


def test_pin_refuses_a_list_carrying_a_bad_key_rather_than_applying_part_of_it():
    """ALL OR NOTHING, the promise the docstring makes — enforced, not merely stated.

    ⛔ Measured: `pin(g, ["a", ""])` wrote `['a']` and returned `written=True`, so a stray
    empty argv element (`--key ""`) produced a partially applied declaration reported as
    success. This is the CALLER's list — a different door from the stored one the
    corrupt-entry test above covers, and a separate fix.
    """
    g = _FakeGraph(["a", "b"])
    with pytest.raises(PinRefused) as exc:
        pin(g, ["a", ""], budget=PinBudget(max_nodes=10))
    assert exc.value.code == "bad-key"
    assert "Nothing was pinned" in exc.value.reason
    assert g.mutating == [], "a refused declaration still wrote"
    assert PINS_META_KEY not in g.meta


def test_unpin_refuses_a_list_carrying_a_bad_key_rather_than_releasing_part_of_it():
    g = _FakeGraph(["a", "b"])
    budget = PinBudget(max_nodes=10)
    pin(g, ["a", "b"], budget=budget)
    with pytest.raises(PinRefused) as exc:
        unpin(g, ["a", ""], budget=budget)
    assert exc.value.code == "bad-key"
    assert g.meta[PINS_META_KEY]["keys"] == ["a", "b"], "a refused release still wrote"


def test_repinning_declared_keys_on_an_over_budget_set_is_refused_not_reported_ok():
    """The cap check lived only inside `if fresh:`, so a no-op pin skipped it entirely.

    ⛔ Measured: re-pinning the same keys at `max_nodes=1` returned `written=True`,
    `added=[]` and `verdict: OK`, while the envelope's own `set.over_budget` named the
    violation and every sweep refused it.
    """
    g = _FakeGraph(["p0", "p1", "p9"])
    g.link("p0", "p1")
    pin(g, ["p0"], budget=PinBudget(max_nodes=10))
    with pytest.raises(PinRefused) as exc:
        pin(g, ["p0"], budget=PinBudget(max_nodes=1))
    assert exc.value.code == "budget"
    assert "over budget" in exc.value.reason
    assert g.meta[PINS_META_KEY]["keys"] == ["p0"], "a refused re-pin still wrote"


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


# ── review round 1: the refusals that were reported with the wrong cause ───────
def test_a_truncated_declaration_is_unmeasured_even_with_no_budget():
    """Truncation is not a budget question: it answers `over_budget` before the cap.

    Reading with `budget=None` (the default) used to return "fine" for a closure nobody
    could see in full — the fail-open direction on the one field a sweep reads.
    """
    g = _FakeGraph([f"k{i}" for i in range(5)])
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id",
                             "keys": [f"k{i}" for i in range(5)]}
    import tortoise.resident_pin as rp

    original = rp.DEFAULT_KEY_LIMIT
    rp.DEFAULT_KEY_LIMIT = 2
    try:
        reading = read(g)          # no budget at all
        assert reading.truncated is True
        assert reading.over_budget() is not None
        assert "UNKNOWN" in (reading.gate_reason() or "")
    finally:
        rp.DEFAULT_KEY_LIMIT = original


def test_a_truncated_declaration_is_never_rewritten_by_pin_or_unpin():
    """A partial read must not be the basis of a full write — that DROPS keys."""
    g = _FakeGraph([f"k{i}" for i in range(6)])
    budget = PinBudget(max_nodes=100)
    pin(g, ["k0", "k1", "k2"], budget=budget)
    import tortoise.resident_pin as rp

    original = rp.DEFAULT_KEY_LIMIT
    rp.DEFAULT_KEY_LIMIT = 2
    try:
        with pytest.raises(PinRefused) as exc:
            unpin(g, ["k0"], budget=budget)
        assert exc.value.code == "truncated"
        with pytest.raises(PinRefused) as exc2:
            pin(g, ["k5"], budget=budget)
        assert exc2.value.code == "truncated"
    finally:
        rp.DEFAULT_KEY_LIMIT = original
    assert g.meta[PINS_META_KEY]["keys"] == ["k0", "k1", "k2"], (
        "a truncated read rewrote the declaration, silently dropping keys it could not "
        "see"
    )


def test_the_read_bound_is_reachable_so_a_truncated_declaration_can_be_released(
        monkeypatch):
    """The escape the truncation refusal names must be EXECUTABLE, and it is the env var.

    ⛔ Before this arm the bound was a module constant with no control, and BOTH
    truncation refusals told the operator to "release keys first, or raise the read
    bound" — while `unpin` refuses for the same truncation. So a declaration past the
    bound could be neither extended nor released by any shipped tool, the sweep refused
    forever, and the remedy named in the message could not be run: the "an instruction
    that cannot be followed" class this module refuses elsewhere.
    """
    import tortoise.resident_pin as rp

    g = _FakeGraph([f"k{i}" for i in range(6)])
    budget = PinBudget(max_nodes=100)
    pin(g, ["k0", "k1", "k2", "k3"], budget=budget)

    # The default bound, narrowed through the lever, truncates and refuses — and names
    # the lever.
    monkeypatch.setenv(rp.KEY_LIMIT_ENV, "2")
    with pytest.raises(PinRefused) as exc:
        unpin(g, ["k0"], budget=budget)
    assert exc.value.code == "truncated"
    assert rp.KEY_LIMIT_ENV in str(exc.value), (
        "the refusal must name the control that raises the bound, not just the bound"
    )

    # Raising it through the SAME lever is what makes the refusal executable.
    monkeypatch.setenv(rp.KEY_LIMIT_ENV, "100")
    unpin(g, ["k0"], budget=budget)
    assert g.meta[PINS_META_KEY]["keys"] == ["k1", "k2", "k3"], (
        "with the bound raised the release must run, and release only what was named"
    )


def test_a_malformed_read_bound_refuses_rather_than_falling_back(monkeypatch):
    """A malformed bound must not silently fall back to the default.

    An operator who set the variable and got a truncated read anyway would be told the
    wrong cause, and the refusal they need (fix the value) would never appear.
    """
    import tortoise.resident_pin as rp

    monkeypatch.setenv(rp.KEY_LIMIT_ENV, "not-a-number")
    with pytest.raises(PinConfigError) as exc:
        rp.key_limit()
    assert "not an integer" in str(exc.value)

    monkeypatch.setenv(rp.KEY_LIMIT_ENV, "0")
    with pytest.raises(PinConfigError) as exc2:
        rp.key_limit()
    assert "below its floor" in str(exc2.value)


def test_a_pin_held_head_at_the_reach_bound_is_not_told_to_re_run():
    """An all-pinned head told "re-run to continue" sends the operator into a loop.

    ⛔ The reach guard exists because "re-run to continue" is a LIE once the held-back
    head reaches the widest prefix ONE call can examine — re-running offers the same
    nodes and stops in the same place. It counted the record and lease hold-backs and
    not the pin, which is the third: with 6 pinned nodes, batch 1 x 3 passes (reach 4),
    the run reported "re-run to continue" while the only working reply ("unpin, or raise
    the pin budget") lived in a branch this path never reaches.
    """
    g = _FakeGraph([f"p{i}" for i in range(6)])
    pinned_keys = frozenset(f"p{i}" for i in range(6))

    def all_pinned(candidate: Candidate) -> bool:
        return candidate.key in pinned_keys

    report = evict(
        _assessment(nodes=6), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=all_pinned, dry_run=False,
        policy=EvictionPolicy(max_nodes=3, batch_size=1, max_passes=3),
    )
    assert report.deleted == ()
    assert "re-run to continue" not in report.reason, (
        "an all-pinned head was told to re-run: re-running examines the same nodes and "
        f"stops in the same place. reason={report.reason}"
    )
    # ⛔ The remedy must be IN the message. The breakdown string ("N pinned, M
    # pin-unanswerable) has reached the widest prefix …") satisfies a bare "pinned"
    # check while offering only "raise the batch or the pass budget, or widen the proven
    # population" — none of which unpins anything. The cheapest working reply is the
    # one that must appear.
    assert "unpin" in report.reason.lower(), report.reason


def test_a_node_taken_after_the_pin_predicate_recovered_leaves_no_stale_held_entry():
    """A stale held entry makes `deleted` intersect a `skipped_*` list.

    ⛔ `held_pinned_ids` only prevented a duplicate APPEND — it was never consulted when
    a node was taken, so a node whose predicate RAISED once (a store blip) and answered
    False on the retry was DELETED while staying in `skipped_pin_unanswerable`. The
    record-probe list beside it already prunes exactly this, for exactly this reason.
    """
    g = _FakeGraph(["p0", "p1", "p2"])
    calls: dict[str, int] = {}

    def flaky(candidate: Candidate) -> bool:
        seen = calls.get(candidate.key or "", 0) + 1
        calls[candidate.key or ""] = seen
        if seen == 1:
            raise RuntimeError("pin store blip")
        return False

    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=flaky, dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=4),
    )
    assert report.deleted, "sanity: the second pass must be able to take something"
    deleted_ids = {c.node_id for c in report.deleted}
    stale = deleted_ids & {c.node_id for c in report.skipped_pin_unanswerable}
    assert not stale, (
        f"node(s) {sorted(stale)} are reported as BOTH deleted and held-back — a stale "
        f"entry: deleted={[c.key for c in report.deleted]}, "
        f"held={[c.key for c in report.skipped_pin_unanswerable]}"
    )


def test_a_malformed_read_bound_makes_the_declaration_unreadable_not_a_traceback(
        monkeypatch):
    """`key_limit()` raises by design; `read()` is the door that must not let it escape.

    ⛔ `tools/cache_evict.py` and `tools/resident.py show` call `read()` directly, so an
    uncaught `PinConfigError` became a traceback with exit 1 — "retry the FLAGS" — for a
    config fault no flag change clears. Converted to the module's own unreadable state,
    every door refuses with ITS code instead (the sweep exits 3 REFUSED).
    """
    import tortoise.resident_pin as rp

    g = _FakeGraph(["p0", "p1"])
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}
    monkeypatch.setenv(rp.KEY_LIMIT_ENV, "abc")
    reading = read(g)          # must not raise
    assert reading.readable is False
    assert reading.source.startswith("config:")
    assert rp.KEY_LIMIT_ENV in reading.source
    assert (reading.gate_reason() or "") != "", (
        "an unreadable declaration must carry a refusal reason, or a sweep would "
        "proceed over it"
    )


def test_a_partially_corrupt_declaration_is_malformed_not_silently_narrowed():
    """A dropped key is a LOSS of the user's declaration, and the next pin makes it final.

    ⛔ `keys: ["p0", 7]` used to read as `readable=True, keys=["p0"]`, so the reader saw
    a shorter set than the declaration holds and the next `pin` REWROTE the declaration
    without the `7` — the same silent loss the truncation guard refuses. A bad ENTRY is
    now classified like a wrong-typed `keys` FIELD.
    """
    g = _FakeGraph(["p0", "p1"])
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id",
                             "keys": ["p0", 7]}
    reading = read(g, budget=PinBudget(max_nodes=10))
    assert reading.readable is False, (
        "a declaration with a non-string key read as READABLE — the caller then sees a "
        "narrowed set it will rewrite over, dropping the entry it could not represent"
    )
    assert "malformed" in (reading.source or "")
    assert "7" in (reading.source or ""), "the malformed entry must be NAMED"

    # And the writer refuses rather than rewriting: `pin` is guarded by `not readable`.
    with pytest.raises(PinRefused) as exc:
        pin(g, ["p1"], budget=PinBudget(max_nodes=10))
    assert exc.value.code == "unreadable"
    assert g.meta[PINS_META_KEY]["keys"] == ["p0", 7], (
        "a refused pin rewrote a declaration it could not read"
    )


def test_a_window_too_small_for_a_pin_names_that_rather_than_an_impossible_number():
    with pytest.raises(PinConfigError) as exc:
        PinBudget(max_nodes=1, window_nodes=1)
    message = str(exc.value)
    assert "cannot carry a pin at all" in message
    assert "Pin at most 1" not in message, (
        "the refusal named a value it then refuses — an instruction that cannot be "
        "followed"
    )


def test_unpin_of_a_population_the_set_does_not_address_is_refused():
    g = _FakeGraph(["a", "b"])
    budget = PinBudget(max_nodes=10)
    pin(g, ["a", "b"], label="Source", key_prop="url", budget=budget)
    with pytest.raises(PinRefused) as exc:
        unpin(g, ["a"], label="Point", key_prop="id", budget=budget)
    assert exc.value.code == "population-mismatch"
    assert g.meta[PINS_META_KEY]["keys"] == ["a", "b"]


def test_a_truthy_non_boolean_pin_predicate_holds_the_node_back():
    """The pin gate's polarity is `bool(...)`, NOT the record probe's `is True`.

    There, a truthy non-bool HOLDS the node (safe); here the same spelling would DELETE
    it, because `1`, `"pinned"` and `numpy.True_` are all truthy and none is `True`.
    """
    g = _FakeGraph(["p0", "p1"])
    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=lambda candidate: 1,      # truthy, not True
        dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=2),
    )
    assert g.node_keys() == ["p0", "p1"], (
        "a truthy non-boolean pin predicate authorised a delete — the predicate's "
        "meaning is 'is this pinned', and anything truthy must hold the node"
    )
    assert report.deleted == ()


def test_the_weak_assertions_are_now_behavioural():
    """`gate is not None` asserted nothing; the over-budget test never reached `evict`.

    Rep-laced here so a future edit to those two tests cannot go back to asserting that a
    bound method exists.
    """
    g = _FakeGraph(["p0", "p1"])
    g.link("p0", "p1")
    budget = PinBudget(max_nodes=10)
    pin(g, ["p0"], budget=budget)
    reading = read(g, budget=budget)
    gate = reading.gate()
    assert gate(Candidate(node_id=0, key="p0", population=POPULATION)) is True
    assert gate(Candidate(node_id=1, key="p1", population=POPULATION)) is True  # closure
    assert gate(Candidate(node_id=2, key="zz", population=POPULATION)) is False
    assert gate(Candidate(node_id=3, key=None, population=POPULATION)) is False


# ── review round 6: the documented correctness rule that had no row ──────────────
class _LabelledGraph(_FakeGraph):
    """A fake that HONOURS the label in `OPTIONAL MATCH (n)--(m:{label})`.

    The base fake answers every neighbour regardless of label, so it cannot see the
    difference between the label-scoped pattern and the unscoped one — which is exactly
    why the closure's scoping had no coverage.
    """

    def query(self, statement: str, params=None):
        if "OPTIONAL MATCH (n)--(m:" in statement:
            self.statements.append(statement)
            label = statement.split("OPTIONAL MATCH (n)--(m:", 1)[1].split(")", 1)[0]
            rows = []
            for key in (params or {})["keys"]:
                node_id = self.id_of(key)
                if node_id is None:
                    continue
                nbrs = {i for i in self.neighbours(node_id)
                        if self.nodes[i].get("label", "Point") == label}
                rows.append([key, sorted(nbrs),
                             [self.nodes[i].get("id") for i in sorted(nbrs)]])
            return _Result(rows)
        return super().query(statement, params=params)


def test_a_gate_built_from_an_unreadable_or_truncated_reading_REFUSES():
    """`read(g).gate()` must not answer "nothing is pinned" when the read failed.

    ⛔ MUTATION-VERIFIED GAP: this assertion did not exist, and both mutations of the guard
    — deleting the `if not self.readable or self.truncated:` refusal, and returning the bare
    closure comparison — left the whole suite GREEN. Every other `.gate()` call in these
    suites is built from a `readable=True, truncated=False` reading, so the door this PR
    exists to close had no row at all. The consequences it prevents: an unreadable
    declaration yields an EMPTY closure, so the predicate answers "nothing is pinned" and a
    caller that skips `gate_reason()` deletes the entire pinned population while the report
    reads `held_pinned: 0`; a truncated one yields a PARTIAL closure and deletes exactly the
    pinned nodes past the read bound.
    """
    unreadable = PinReading(label="Point", key_prop="id", readable=False,
                            source="unreadable:RuntimeError: boom")
    with pytest.raises(PinRefused) as exc:
        unreadable.gate()
    assert exc.value.code == "unreadable"

    truncated = PinReading(label="Point", key_prop="id",
                           keys=frozenset({"p0"}), closure=frozenset({"p0"}),
                           truncated=True, source="meta:pins")
    with pytest.raises(PinRefused) as exc2:
        truncated.gate()
    assert exc2.value.code == "truncated"

    # And the live path: a real `read()` over a graph whose declaration cannot be parsed
    # must not hand out a gate either.
    g = _FakeGraph(["p0"])
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0", 7]}
    with pytest.raises(PinRefused):
        read(g).gate()


def test_the_closure_is_label_scoped_so_a_foreign_labels_neighbour_is_not_counted():
    """§7.1 and `read()` both call the label-scoped neighbour a MEASURED correctness fix.

    ⛔ `OPTIONAL MATCH (n)--(m)` collects a neighbour in ANY label, so a `:Point` joined to
    an `:Object` carrying the same key property was counted into the closure AND gated by
    that key — an inflated footprint (false budget refusals) over nodes this control cannot
    protect. Reverting `(m:{label})` -> `(m)` at both sites left every suite green, so the
    rule was documented in two places and enforced by nothing.
    """
    g = _LabelledGraph()
    g.add("p0")
    g.add("o1", extra={"label": "Object"})
    g.link("p0", "o1")
    g.meta[PINS_META_KEY] = {"label": "Point", "key_prop": "id", "keys": ["p0"]}

    scoped = read(g, budget=PinBudget(max_nodes=1))
    assert scoped.closure_size == 1, (
        "the closure counted the :Object neighbour — the label scope is gone, and a "
        "budget computed from this footprint is inflated"
    )
    assert scoped.over_budget() is None


def test_the_declaration_time_closure_is_label_scoped_too_not_just_the_read():
    """F2 — the label scope is enforced at TWO sites and only one had a row.

    ⛔ `read()` and `_closure_for` both build `OPTIONAL MATCH (n)--(m:{label})`, and both
    carry the same ⛔ comment calling it a measured correctness fix. Reverting it at the
    `read()` site reds the row above; reverting it at `_closure_for` — the WRITE path, which
    sizes a declaration against the cap and the degree ceiling — left the whole suite green.
    So a `:Point` joined to an `:Object` carrying the same key property could still inflate
    a declaration's closure at `pin()` time, refusing a legitimate pin for "would need N
    slots" over nodes this control cannot protect.
    """
    g = _LabelledGraph()
    g.add("p0")
    g.add("o1", extra={"label": "Object"})
    g.link("p0", "o1")

    # One slot is enough for the POINT alone; the :Object neighbour would need a second.
    result = pin(g, ["p0"], budget=PinBudget(max_nodes=1))

    assert result.written is True
    assert result.added == frozenset({"p0"})
    assert result.reading.degrees == {"p0": 0}, (
        "the declaration-time closure counted the :Object neighbour — the write path's "
        "label scope is gone, and the budget/degree ceilings are computed over nodes this "
        "control cannot protect"
    )


def test_a_pinned_node_held_by_a_reader_is_reported_pinned_not_leased():
    """The `before the lease` half of gate 6's ordering, which had no row at all.

    ⛔ The ordering test next door is named `..._before_the_record_probe_and_the_lease` but
    never passed `leases`, so only the record half was graded: moving the pin check AFTER
    `leases.held()` left every row green. The order matters because the two remedies are
    different — `unpin` clears a pin, nothing but time clears a live reader — and a pinned
    node reported as `skipped_leased` sends an operator to wait for a reader that is not
    the reason it was kept.
    """
    from tortoise.cache_evictor import ReadLeases
    from tortoise.resident_pin import PinReading

    g = _FakeGraph(["p0", "p1"])
    leases = ReadLeases()
    leases.acquire(g.id_of("p0"))
    pinned = PinReading(label="Point", key_prop="id",
                        keys=frozenset({"p0"}), closure=frozenset({"p0"}),
                        budget=PinBudget(max_nodes=10))

    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest,
        record_probe=_record,          # the record CAN serve it, so only the two gates decide
        pinned=pinned.gate(),
        leases=leases,
        dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=2),
    )

    assert "p0" in g.node_keys()
    assert {c.key for c in report.skipped_pinned} == {"p0"}, (
        "a node that is BOTH pinned and leased was not reported as pinned — the pin gate "
        "is being asked after the lease, so the operator is sent to wait on a reader"
    )
    assert report.skipped_leased == (), (
        "the pin gate ran AFTER `leases.held()` — the 'before the lease' ordering in the "
        "module header is unenforced"
    )


def test_a_pin_declared_between_planning_and_the_delete_still_wins():
    """Gate 6's second half — the same window the lease already re-checks.

    ⛔ `final` re-asks `leases.held()` immediately before `_delete_nodes` because "a read can
    start between planning and the statement". The pin is the STRONGER promise ("a pinned
    node is NEVER deleted, whatever the selector offered") and had no equivalent re-ask, so
    a declaration written in that window let the nodes be deleted AND reported in `deleted`
    — the report could not even show that it happened.
    """
    g = _FakeGraph(["p0", "p1"])

    # ⛔ ASKED TWICE IS THE SIMULATION. The predicate answers False on the FIRST ask for a
    # node (so it is planned and reaches `final`) and True on any later ask — which is
    # exactly a declaration written between planning and the DELETE. Order-independent, so
    # it does not depend on how many times the plan-time gate happens to be consulted.
    asks: dict[int, int] = {}

    def pin_appears_mid_sweep(candidate: Candidate) -> bool:
        asks[candidate.node_id] = asks.get(candidate.node_id, 0) + 1
        return asks[candidate.node_id] > 1

    report = evict(
        _assessment(nodes=1), _FakeClient(), g,
        population=POPULATION,
        selector=_coldest, record_probe=_record,
        pinned=pin_appears_mid_sweep,
        dry_run=False,
        policy=EvictionPolicy(max_nodes=1, batch_size=10, max_passes=1),
    )

    assert asks.get(g.id_of("p0"), 0) > 1, (
        "the pin predicate was never re-asked before the delete — gate 6 has no second "
        "half, so no window is closed"
    )
    assert report.deleted == (), (
        "the pin gate is only asked at PLAN time — a declaration written between planning "
        "and the delete statement did not hold the node, so a pinned node was deleted "
        "while the report called it `deleted`"
    )
    assert {c.key for c in report.skipped_pinned} == {"p0"}, (
        "the node was held but not reported as pinned, so the operator cannot see that a "
        "declaration written mid-sweep is what kept it"
    )
