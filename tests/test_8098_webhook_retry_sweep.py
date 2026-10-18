"""#8098 — the scheduled webhook retry sweep (epic #2080 W4 phase 2, #2081 follow-up).

The delivery layer (#2081) dispatches at the END of a tenant's dream cycle. That
is the correct cadence for NEW events — a write enqueues the dream that pushes
it — but it is a WRITE-TRIGGERED cadence, so a subscription whose retry backoff
(``next_attempt_at``) elapses while its tenant stays IDLE had no trigger at all:
the bookkeeping was durable and correct, the TRIGGER was missing. #8098 adds the
trigger.

What these tests pin, and why each one is shaped the way it is:

* **the trigger exists at all.** Driving the loop and asserting the pass ran
  (not just that the function is defined) — the #1890/#4241 shape, where the
  repair was never wrong, it just never ran. A definition-only test would have
  been green for the whole life of this bug.
* **an IDLE tenant is retried after the backoff elapses, and the cursor
  advances.** The integration case: fail a delivery, let the backoff elapse
  (no new write to that tenant's graph at all), run ONE sweep pass, and read the
  cursor back from the store.
* **the no-subscription cost.** One read, ZERO nodes, ZERO indexes — asserted by
  DIFFING the graph, not by reading the code: `ensure_subscription_schema`
  creates three indexes, so a sweep that reached it for every tenant would be a
  fleet-wide index-creation storm on the first deploy.
* **shutdown/handover.** The task is in the shared ``_LIVENESS_TASK_ATTRS``
  tuple (so a re-entry or shutdown disarms it) and ``_stop_liveness`` actually
  cancels it.
* **the gate cannot drift from the dispatcher.** Both read ONE pair of clauses
  (due-by-TIME and BEHIND-the-stream); the parity test drives a matrix of
  subscription states through both and requires the same yes/no — including the
  fourth state, DECLARED-AND-CAUGHT-UP, which is where every subscription spends
  ~100% of its life and which the first 3-state matrix omitted (#8098 review
  F3/P1: the temporal clause alone is true forever, so the gate admitted every
  tenant that had ever registered a webhook on every pass).
* **the pool is disposed at thread shutdown.** Its workers are non-daemon and
  `concurrent.futures.thread` JOINS them at exit, so cancelling the sweep task
  alone leaves queued dispatches to run out their POST timeouts (#8098 review
  F2).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

pytest.importorskip("cryptography")  # the at-rest encryption dependency

from tortoise import backup_sweep
from tortoise import hosted_api as ha
from tortoise import webhook_delivery as wd

# ── harness ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _webhook_env(monkeypatch):
    """Real Fernet key + every webhook knob at its unset default, per test."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    for var in ("TORTOISE_WEBHOOK_DISPATCH", "TORTOISE_WEBHOOK_SWEEP",
                "TORTOISE_WEBHOOK_SWEEP_INTERVAL",
                "TORTOISE_WEBHOOK_SWEEP_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)
    ha._WEBHOOK_SWEEP_INFLIGHT.clear()
    yield
    ha._WEBHOOK_SWEEP_INFLIGHT.clear()


class FakeTransport:
    """Replays a scripted status per POST and records the calls."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        return item, None


class _FakeSDK:
    """Stands in for the per-graph SDK the dispatch path builds."""

    def __init__(self, proj):
        self._proj = proj
        self.closed = 0

    def _get_proj(self):
        return self._proj

    def close(self):
        self.closed += 1


class _Graph:
    """A raw graph handle whose ``query`` answers the gate's TWO reads.

    `due_subscription_count` reads the event head first (``:GraphEventMeta``)
    and the due count second, so the fake answers by QUERY SHAPE — a scripted
    single result set would let the head masquerade as the count (or the reverse)
    and a broken gate would still look green.
    """

    def __init__(self, rows, log, head: int = 0):
        self._rows = rows
        self._log = log
        self._head = head

    def query(self, cypher, params=None):
        self._log.append(cypher)
        if "GraphEventMeta" in cypher or "GraphEvent" in cypher:
            return SimpleNamespace(result_set=[[self._head]])
        return SimpleNamespace(result_set=self._rows)


class _Db:
    """A data-plane handle: named graphs whose due count is scripted."""

    def __init__(self, graphs: set[str], counts: dict[str, int] | None = None,
                 raise_on_list: bool = False, raise_on_select=(),
                 heads: dict[str, int] | None = None):
        self._graphs = graphs
        self._counts = counts or {}
        #: The event head each graph reports; high by default so a scripted
        #: count reads as "due" (a 0 head would make the cursor clause refuse
        #: every scripted subscription and turn the fixtures vacuous).
        self._heads = heads or {}
        self._raise_on_list = raise_on_list
        self._raise_on_select = set(raise_on_select)
        self.selected: list[str] = []
        self.queries: list[str] = []

    def list_graphs(self):
        if self._raise_on_list:
            raise RuntimeError("graph list unavailable")
        return list(self._graphs)

    def select_graph(self, name):
        self.selected.append(name)
        if name in self._raise_on_select:
            raise RuntimeError(f"graph {name} unreachable")
        return _Graph([[self._counts.get(name, 0)]], self.queries,
                      head=self._heads.get(name, 100))


def _patch_enumeration(monkeypatch, db, orgs=("t1",), graphs_by_org=None):
    """Wire the sweep's enumeration seams to fakes (no control plane, no SDK)."""
    plan = graphs_by_org or {
        "t1": [{"graph_id": "default", "kind": "default",
                "namespace": "g_default", "graph_name": "g_default"}],
    }

    held = SimpleNamespace(closed=0)

    def _close():
        held.closed += 1

    held.close = _close
    monkeypatch.setattr(ha, "_data_plane_db", lambda: (held, db))
    monkeypatch.setattr(ha, "_control_plane_source_on_worker", lambda: object())
    monkeypatch.setattr(backup_sweep, "enumerate_orgs", lambda _source: list(orgs))
    monkeypatch.setattr(backup_sweep, "_sweep_graph_list",
                        lambda _source, org_id: list(plan.get(org_id, [])))
    return held


def _inline_daemon(monkeypatch):
    """Run the enumeration inline — this file must not depend on a worker thread."""
    async def _inline(fn, *, name, timeout=None):
        return fn()

    monkeypatch.setattr(ha, "run_on_daemon_worker", _inline)


def _seed_events(sdk, n=1):
    from tortoise import event_store
    from tortoise.ids import ulid

    proj = sdk._get_proj()
    seqs = []
    for i in range(n):
        seq = event_store.next_seq(proj)
        event_store.append_event(proj, seq, "PointAdded", {"id": f"pt_{i}"},
                                 f"evt-{ulid()}")
        seqs.append(seq)
    return seqs


def _count(proj) -> int:
    return int(proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0])


def _indexes(proj):
    return proj.g.query("CALL db.indexes()").result_set


# ── the due predicate: ONE copy, and the gate == the dispatcher ────────────

def test_the_gate_counts_exactly_what_the_dispatcher_would_dispatch(sdk_factory):
    """Parity over a matrix of states — the gate may never admit a graph the
    dispatcher no-ops on, nor starve one it would have dispatched.

    Behavioural, not source-level: an AST pin on ``_DUE_PREDICATE`` would pass
    while the two queries built it into different Cypher.

    FOUR states, not three (#8098 review F3/P1). The three time-based ones
    (fresh / backing-off / disabled) are the states the predicate's FIRST clause
    governs; they are blind to the state every subscription spends ~100% of its
    life in — DECLARED AND CAUGHT UP, where ``next_attempt_at`` is NULL and the
    cursor is at the head. Pre-fix the gate answered 1 there forever.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    now = datetime.now(UTC)

    # due: never attempted — and (crucially) BEHIND the stream, so the cursor
    # clause must not hide it. Pre-fix and post-fix both answer 1 here.
    _seed_events(sdk, n=1)
    fresh = wd.create_subscription(proj, url="https://hooks.example.com/fresh",
                                   types=["PointAdded"], now=now)
    # not due: a backed-off retry in the FUTURE
    backing_off = wd.create_subscription(
        proj, url="https://hooks.example.com/wait", now=now)
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.next_attempt_at=$next",
        params={"id": backing_off["sub_id"],
                "next": (now + timedelta(seconds=60)).isoformat()},
    )
    # not due: disabled by the attempt ceiling
    dead = wd.create_subscription(proj, url="https://hooks.example.com/dead",
                                  now=now)
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.disabled_at=$ts "
        "REMOVE s.next_attempt_at",
        params={"id": dead["sub_id"], "ts": now.isoformat()},
    )

    due_ids = {s["sub_id"] for s in wd._due_subscriptions(proj, now)}
    assert due_ids == {fresh["sub_id"]}, due_ids
    assert wd.due_subscription_count(proj.g, now) == len(due_ids) == 1

    # Advance the clock past the backed-off retry: it becomes due, and the gate
    # and the dispatcher must agree on that too.
    later = now + timedelta(seconds=120)
    due_ids = {s["sub_id"] for s in wd._due_subscriptions(proj, later)}
    assert due_ids == {fresh["sub_id"], backing_off["sub_id"]}, due_ids
    assert wd.due_subscription_count(proj.g, later) == len(due_ids) == 2

    # ── the FOURTH state: declared and CAUGHT UP ──────────────────────────
    # Deliver everything the graph holds, so every enabled subscription's cursor
    # reaches the head with `next_attempt_at` reset to NULL — the healthy steady
    # state of a real subscriber. The gate must answer 0: there is nothing to
    # read, so the sweep must not open an SDK for it.
    wd.dispatch_pending(proj, transport=FakeTransport([200]), now=later)
    caught_up = wd.get_subscription(proj, fresh["sub_id"])
    assert caught_up["cursor"] == wd.event_head_seq(proj.g) > 0, caught_up
    assert caught_up["next_attempt_at"] is None
    assert wd._due_subscriptions(proj, later) == [], (
        "a caught-up subscription is still returned by the dispatcher's due "
        "query — it will read the stream to discover there is nothing there")
    assert wd.due_subscription_count(proj.g, later) == 0, (
        "the gate is true for a subscription that owes NOTHING — the sweep "
        "then pays a full SDK open per tenant per interval forever ("
        "#8098 review F3/P1)")


def test_a_declared_and_caught_up_tenant_is_not_a_sweep_target(sdk_factory,
                                                              monkeypatch):
    """End-to-end over the REAL gate: dispatch everything, then sweep.

    The unit parity above pins the predicate; this pins the thing the predicate
    exists to buy — a caught-up tenant costs the pass NO SDK open, no journal
    bind, no index statement. Pre-fix the gate answered 1 here and the pass
    built the full dispatch stack for a graph with nothing to deliver.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=2)
    now = datetime.now(UTC)
    wd.create_subscription(proj, url="https://hooks.example.com/cb", now=now)
    assert wd.dispatch_pending(proj, transport=FakeTransport([200, 200]),
                               now=now)["delivered"] == 2

    db = _Db({"g_default"}, {"g_default": 0})
    _patch_enumeration(monkeypatch, db)
    # The REAL db handle shape, so the REAL gate runs against the REAL graph.
    monkeypatch.setattr(ha, "_data_plane_db", lambda: (
        SimpleNamespace(close=lambda: None), db))
    monkeypatch.setattr(db, "select_graph", lambda _name: proj.g)

    assert ha._webhook_retry_targets() == [], (
        "a tenant with nothing owed was still a sweep target — every pass pays "
        "its SDK open for nothing (#8098 review F3/P1)")


def test_a_tenant_that_is_behind_is_still_a_sweep_target(sdk_factory,
                                                        monkeypatch):
    """The anti-starvation control for the test above: SAME wiring, one new
    event, and the graph must be a target again."""
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=1)
    now = datetime.now(UTC)
    wd.create_subscription(proj, url="https://hooks.example.com/cb", now=now)
    wd.dispatch_pending(proj, transport=FakeTransport([200]), now=now)

    db = _Db({"g_default"}, {"g_default": 0})
    _patch_enumeration(monkeypatch, db)
    monkeypatch.setattr(ha, "_data_plane_db", lambda: (
        SimpleNamespace(close=lambda: None), db))
    monkeypatch.setattr(db, "select_graph", lambda _name: proj.g)
    assert ha._webhook_retry_targets() == []

    _seed_events(sdk, n=1)  # one new event → the cursor is behind again
    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_default"}]


def test_the_head_read_does_not_starve_a_graph_written_without_the_counter(
        sdk_factory):
    """``event_head_seq`` must read 0 only when there is genuinely nothing.

    Every production writer allocates through ``event_store.next_seq`` (which
    MERGEs the ``:GraphEventMeta`` counter), but a store whose events predate the
    counter — a fixture, or a migrated graph — must not be STARVED by a gate
    that equated "no counter" with "no events".
    """
    from tortoise import event_store

    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    assert wd.event_head_seq(proj.g) == 0, "an empty graph has no head"

    # Bypass the allocator entirely: events exist, the counter does not.
    event_store.append_event(proj, 7, "PointAdded", {"id": "pt_x"}, "evt-x")
    assert wd.event_head_seq(proj.g) == 7


def test_an_absent_counter_falls_back_and_the_counter_stays_one_node_read(
        sdk_factory):
    """The counter is the head, not ``max(:GraphEvent.seq)``, in the steady
    state — the whole reason the gate's extra read is affordable."""
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=3)
    queries: list[str] = []

    class _Spy:
        def query(self, cypher, params=None):
            queries.append(cypher)
            return proj.g.query(cypher, params=params)

    assert wd.event_head_seq(_Spy()) == 3
    assert len(queries) == 1 and "GraphEventMeta" in queries[0], (
        f"the head read scanned {queries} — the gate must stay a one-node read "
        f"on a graph that has a counter")


@pytest.mark.parametrize("corrupt", [-1, "not-a-number", True, 2.9, "1", " 2 "])
def test_a_corrupt_counter_cannot_starve_delivery(sdk_factory, corrupt):
    """#8098 review (ISSUE 1): an UNUSABLE counter must not read as head 0.

    ``event_head_seq`` is not only the sweep's cheap gate: ``dispatch_pending``
    reads it once and threads it into ``_due_subscriptions``, whose
    ``s.cursor < $head`` clause decides whether a subscription is returned at
    all. So a counter that read as 0 while the graph holds events with
    ``seq > 0`` made every subscription look caught up — the graph stopped being
    dispatched by the dream cycle AND the sweep, silently, with no log line.

    The safe direction is the ABSENT-counter fallback already in the function
    (the events' own ``max(:GraphEvent.seq)``): it can only over-admit.

    Each parameter is a shape the pre-fix ``max(0, int(value))`` body turns into
    a head BELOW the real one, in one of three ways: ``'1'``, ``' 2 '``, ``True``
    and ``2.9`` are PARSED by ``int()`` into 1, 2, 1, 2; ``-1`` is parsed and
    then clamped to 0; ``'not-a-number'`` is unparseable and takes the
    ``except`` branch to 0. ``event_store.next_seq`` refuses the non-number and
    ``bool`` classes outright ("refused, never coerced"), but it does NOT refuse
    the non-integral float — ``2.9`` is bumped to ``3.9`` there, re-issuing a seq
    — so no counter in this list becomes trustworthy on its own.

    The subscription is deliberately left one event behind rather than at
    ``cursor: 0``: ``0 < 1`` is true, so a head of 1 is still ADMITTED at cursor
    0 and the gate half of the assertion would pass green at a head it must
    reject.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=3)
    assert wd.event_head_seq(proj.g) == 3, "the seeded graph should have a head"

    now = datetime.now(UTC)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 now=now)
    assert sub["cursor"] == 0
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.cursor = 1",
        params={"id": sub["sub_id"]})
    assert wd.due_subscription_count(proj.g, now) == 1, (
        "control: a behind subscription on a healthy counter is admitted")

    # Corrupt the counter the writers bump — the #3902 / pre-#5379 shapes.
    proj.g.query("MATCH (m:GraphEventMeta) SET m.last_seq=$bad",
                 params={"bad": corrupt})

    assert wd.event_head_seq(proj.g) >= 3, (
        f"a corrupt counter ({corrupt!r}) read as the head — every "
        "subscription then looks caught up and DELIVERY stops with no log line")
    assert wd.due_subscription_count(proj.g, now) == 1, (
        "the gate answered 0 for a graph that IS behind its events")
    assert wd.dispatch_pending(proj, transport=FakeTransport([200, 200]),
                               now=now)["delivered"] == 2, (
        "the dispatcher delivered nothing — the corrupt counter starved it")


def test_the_events_fallback_over_admits_rather_than_rejecting_a_seq(
        sdk_factory):
    """The fallback must round UP, not apply the counter's strictness.

    ``_as_seq`` rejects a non-integral float on the COUNTER read, where a
    rejected value means "ask the events instead". On the events read the same
    rejection is the STALL direction: the value returned there is the ``$head``
    that ``_BEHIND_PREDICATE`` compares the cursor against, so answering 0 for a
    graph whose ``:GraphEvent.seq`` is a float hides a deliverable event behind
    ``s.cursor < 0`` and silences the graph. Rounding up can only over-admit.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=3)
    # A hand-written / restored event row with a fractional seq, and NO counter:
    # the graph must be read through the fallback.
    proj.g.query("MATCH (e:GraphEvent) WHERE e.seq = 3 SET e.seq = 3.5")
    proj.g.query("MATCH (m:GraphEventMeta) DELETE m")
    assert wd.event_head_seq(proj.g) >= 4, (
        "the events fallback rounded DOWN (or rejected) a fractional seq — the "
        "head then sits under a deliverable event and the graph is starved")
    now = datetime.now(UTC)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb", now=now)
    assert sub["cursor"] == 0
    assert wd.due_subscription_count(proj.g, now) == 1
    assert wd.dispatch_pending(proj, transport=FakeTransport([200, 200, 200]),
                               now=now)["delivered"] >= 1, (
        "the fallback's head hid every unread event from the dispatcher")


def test_the_gate_refuses_a_subscription_that_is_behind_but_not_yet_due(
        sdk_factory):
    """The OTHER half of the pair: being behind is necessary, not sufficient.

    Mutation control for the cursor clause — a gate that dropped the temporal
    clause in favour of the cursor one would admit every backed-off retry.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=2)
    now = datetime.now(UTC)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 now=now)
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.next_attempt_at=$next",
        params={"id": sub["sub_id"],
                "next": (now + timedelta(seconds=60)).isoformat()},
    )
    assert wd.event_head_seq(proj.g) == 2
    assert wd.due_subscription_count(proj.g, now) == 0, (
        "a subscription whose backoff has not elapsed was reported due")
    assert wd.due_subscription_count(
        proj.g, now + timedelta(seconds=120)) == 1


def test_the_gate_reads_only_and_creates_no_index(sdk_factory):
    """Cost indicator: an empty graph answers ``0`` and stays byte-identical.

    ``ensure_subscription_schema`` mints three indexes on a graph's FIRST
    subscription; a gate that reached it for every tenant would be a fleet-wide
    index-creation storm on the first deploy of the sweep.
    """
    sdk = sdk_factory()
    proj = sdk._get_proj()
    before_nodes = _count(proj)
    before_indexes = _indexes(proj)

    assert wd.due_subscription_count(proj.g) == 0

    assert _count(proj) == before_nodes, "the gate created a node"
    assert _indexes(proj) == before_indexes, "the gate created an index"


def test_a_graph_with_a_caught_up_subscription_stays_byte_identical(sdk_factory):
    """The caught-up case is the one that USED to pay: the gate read returned 1,
    so the sweep built a projection (journal bind) and the dispatcher then ran
    ``ensure_subscription_schema`` → three ``CREATE INDEX`` statements against a
    graph with nothing to deliver (#8098 review F3/P1).

    Pinned by DIFFING the graph and the index list, not by reading the code.
    """
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    _seed_events(sdk, n=1)
    now = datetime.now(UTC)
    wd.create_subscription(proj, url="https://hooks.example.com/cb", now=now)
    wd.dispatch_pending(proj, transport=FakeTransport([200]), now=now)

    before_nodes = _count(proj)
    before_indexes = _indexes(proj)
    assert wd.due_subscription_count(proj.g) == 0
    assert wd.dispatch_pending(proj, transport=FakeTransport([200]), now=now)[
        "subscriptions"] == 0, "a caught-up subscription was still dispatched"

    assert _count(proj) == before_nodes, "the caught-up pass created a node"
    assert _indexes(proj) == before_indexes, "the caught-up pass created an index"


# ── the enumeration: existence-gated, per-graph, fail-closed ───────────────

def test_the_default_graph_is_swept_even_without_a_graph_node(monkeypatch):
    """`_sweep_graph_list` SYNTHESIZES the default — the reason the sweep uses it.

    Pre-#2083 orgs have no ``Graph`` nodes at all, so a bare
    ``enumerate_org_graphs`` walk would silently skip exactly the oldest tenants.
    """
    db = _Db({"g_default"}, {"g_default": 2})
    _patch_enumeration(monkeypatch, db)

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_default"}]
    assert db.selected == ["g_default"], db.selected


def test_a_graph_that_does_not_exist_is_never_selected_or_read(monkeypatch):
    """The #2251 probe-before-touch rule: querying an ABSENT graph MATERIALIZES it.

    So an orphan registry row must cost neither a read nor a graph.
    """
    db = _Db({"g_default"}, {"g_default": 1})
    _patch_enumeration(monkeypatch, db, graphs_by_org={"t1": [
        {"graph_id": "default", "kind": "default",
         "namespace": "g_default", "graph_name": "g_default"},
        {"graph_id": "g_ghost", "kind": "custom",
         "namespace": "g_ghost", "graph_name": "g_ghost"},        # absent
        {"graph_id": "g_invalid", "kind": "custom",
         "namespace": "", "graph_name": "", "_invalid": True},    # unresolved
    ]})

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_default"}]
    assert "g_ghost" not in db.selected, (
        "an absent graph was selected — a read against it materializes it")
    assert "g_invalid" not in db.selected


def test_a_failed_graph_list_probe_skips_the_whole_pass(monkeypatch):
    """A read that did not answer is NOT "nothing is due"."""
    db = _Db({"g_default"}, raise_on_list=True)
    _patch_enumeration(monkeypatch, db)

    assert ha._webhook_retry_targets() == []
    assert db.selected == []


def test_an_unreadable_graph_costs_only_that_graph(monkeypatch):
    db = _Db({"g_a", "g_b"}, {"g_a": 1, "g_b": 1}, raise_on_select=("g_a",))
    _patch_enumeration(monkeypatch, db, graphs_by_org={"t1": [
        {"graph_id": "default", "kind": "default",
         "namespace": "g_a", "graph_name": "g_a"},
        {"graph_id": "g_b", "kind": "custom",
         "namespace": "g_b", "graph_name": "g_b"},
    ]})

    assert ha._webhook_retry_targets() == [
        {"org_id": "t1", "graph_name": "g_b"}]


def test_a_failed_org_enumeration_skips_the_pass(monkeypatch):
    db = _Db({"g_default"}, {"g_default": 1})
    held = _patch_enumeration(monkeypatch, db)

    def _boom(_source):
        raise RuntimeError("team enumeration failed: control plane down")

    monkeypatch.setattr(backup_sweep, "enumerate_orgs", _boom)

    assert ha._webhook_retry_targets() == []
    assert held.closed == 1, "the data-plane SDK leaked open"


def test_the_data_plane_handle_is_always_closed(monkeypatch):
    db = _Db({"g_default"}, {"g_default": 0})
    held = _patch_enumeration(monkeypatch, db)

    ha._webhook_retry_targets()
    assert held.closed == 1


# ── the trigger: an IDLE tenant is retried once its backoff elapses ────────

def test_an_idle_tenant_is_retried_by_the_sweep_and_its_cursor_advances(
        sdk_factory, monkeypatch):
    """The indicator: retried with NO new write to the tenant's graph."""
    sdk = sdk_factory(ensure_schema=True)
    proj = sdk._get_proj()
    seqs = _seed_events(sdk, n=2)
    # Two hours ago, so the retry's backoff (30s) elapsed long before "now".
    base = datetime.now(UTC) - timedelta(hours=2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], now=base)

    first = wd.dispatch_pending(proj, transport=FakeTransport([503]), now=base)
    assert first["failed"] == 1 and first["delivered"] == 0
    state = wd.get_subscription(proj, sub["sub_id"])
    assert state["attempts"] == 1 and state["cursor"] == 0
    assert state["next_attempt_at"] is not None, (
        "a failed attempt must schedule the retry the sweep is looking for")

    # NOTHING writes to this tenant's graph between the failure and the sweep:
    # the subscription state is the same and the event stream is untouched.
    events_before = len(proj.g.query(
        "MATCH (e:GraphEvent) RETURN count(e)").result_set[0])

    transport = FakeTransport([200, 200])
    monkeypatch.setattr(wd, "WebhookTransport", lambda *a, **k: transport)
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_default"}])
    monkeypatch.setattr(ha, "_make_sdk", lambda **_kw: _FakeSDK(proj))
    _inline_daemon(monkeypatch)

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["enabled"] is True and summary["targets"] == 1, summary
    assert summary["delivered"] == 2, summary
    state = wd.get_subscription(proj, sub["sub_id"])
    assert state["cursor"] == seqs[-1], "the cursor did not advance"
    assert state["attempts"] == 0 and state["next_attempt_at"] is None
    assert len(transport.calls) == 2, "the idle tenant's events were not pushed"
    assert len(proj.g.query(
        "MATCH (e:GraphEvent) RETURN count(e)").result_set[0]) == events_before, (
        "the sweep invented or consumed events — it must only READ the stream")


def test_a_graph_already_in_flight_is_skipped_not_queued(monkeypatch):
    """A pass longer than the interval must not re-enter the same graph."""
    ha._WEBHOOK_SWEEP_INFLIGHT.add("g_default")
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_default"}])
    _inline_daemon(monkeypatch)
    monkeypatch.setattr(ha, "_make_sdk", lambda **_kw: pytest.fail(
        "an in-flight graph was dispatched again"))

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["targets"] == 0 and summary["skipped"] == 1, summary


def test_a_failing_graph_does_not_cost_the_pass_its_other_tenants(monkeypatch):
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: [
        {"org_id": "t1", "graph_name": "g_bad"},
        {"org_id": "t2", "graph_name": "g_good"}])

    dispatched: list[str] = []

    def _make_sdk(*, graph_name=None, namespace=None):
        if graph_name == "g_bad":
            raise RuntimeError("graph unreachable")
        dispatched.append(graph_name)
        return _FakeSDK(SimpleNamespace())

    monkeypatch.setattr(ha, "_make_sdk", _make_sdk)
    monkeypatch.setattr(wd, "dispatch_pending", lambda proj: {"delivered": 1})
    _inline_daemon(monkeypatch)

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert dispatched == ["g_good"], dispatched
    assert summary["delivered"] == 1, summary


# ── knobs: kill switch, enable flag, interval, concurrency ─────────────────

def test_the_kill_switch_stops_the_sweep_before_any_graph_work(monkeypatch):
    """``TORTOISE_WEBHOOK_DISPATCH=0`` must stop the sweep too — an operator
    who turned egress off must not keep paying a fleet-wide pass every interval.
    """
    calls: list[int] = []
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: calls.append(1) or [])
    monkeypatch.setenv("TORTOISE_WEBHOOK_DISPATCH", "0")

    summary = asyncio.run(ha._webhook_retry_sweep_pass())

    assert summary["enabled"] is False, summary
    assert calls == [], "the kill switch still enumerated the fleet"


def test_the_sweep_enable_knob_is_its_own_tristate(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(ha, "_webhook_retry_targets", lambda: calls.append(1) or [])
    monkeypatch.setenv("TORTOISE_WEBHOOK_SWEEP", "0")

    assert asyncio.run(ha._webhook_retry_sweep_pass())["enabled"] is False
    assert calls == []


@pytest.mark.parametrize("raw,expected", [
    (None, wd.SWEEP_INTERVAL_DEFAULT_S),
    ("", wd.SWEEP_INTERVAL_DEFAULT_S),
    ("90", 90),
    ("0", wd.SWEEP_INTERVAL_DEFAULT_S),        # a busy loop, refused
    ("-5", wd.SWEEP_INTERVAL_DEFAULT_S),
    ("oops", wd.SWEEP_INTERVAL_DEFAULT_S),
])
def test_sweep_interval_resolver(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_INTERVAL_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_INTERVAL_ENV, raw)
    assert wd.sweep_interval_s() == expected


@pytest.mark.parametrize("raw,expected", [
    (None, wd.SWEEP_CONCURRENCY_DEFAULT),
    ("8", 8),
    ("0", 1),
    ("-3", 1),
    ("999", wd.SWEEP_CONCURRENCY_MAX),
    ("oops", wd.SWEEP_CONCURRENCY_DEFAULT),
])
def test_sweep_concurrency_clamps(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_CONCURRENCY_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_CONCURRENCY_ENV, raw)
    assert wd.sweep_concurrency() == expected


@pytest.mark.parametrize("raw,expected", [
    (None, True),
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("0", False), ("false", False), ("no", False), ("off", False),
    ("banana", True),   # garbage NEVER silently disarms the retry trigger
])
def test_sweep_enabled_is_tristate(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(wd.SWEEP_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(wd.SWEEP_ENABLED_ENV, raw)
    assert wd.sweep_enabled() is expected


# ── lifecycle: armed by the loop, disarmed by shutdown ─────────────────────

def test_the_periodic_loop_arms_the_sweep(monkeypatch, caplog):
    """Drive the REAL runner — the scheduling property, not the step.

    A direct call to ``_webhook_retry_sweep_pass()`` cannot observe whether it
    is armed at all, which is exactly the #8098 defect (the delivery path
    existed and was tested; nothing ever called it for an idle tenant).
    """
    passes: list[int] = []

    async def _pass():
        passes.append(1)
        return {"enabled": True}

    monkeypatch.setattr(ha, "_webhook_retry_sweep_pass", _pass)

    async def _one_pass():
        task = asyncio.create_task(ha._webhook_retry_sweep_loop(0.0))
        for _ in range(200):
            await asyncio.sleep(0)
            if passes:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_one_pass())
    assert passes, "the periodic retry loop did not arm the sweep"


def test_a_raising_pass_cannot_end_the_loop(monkeypatch, caplog):
    """#5381: a failing pass must not silently stop retrying every tenant."""
    calls: list[int] = []

    async def _pass():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("sweep exploded")
        return {"enabled": True}

    monkeypatch.setattr(ha, "_webhook_retry_sweep_pass", _pass)

    async def _two_passes():
        task = asyncio.create_task(ha._webhook_retry_sweep_loop(0.0))
        for _ in range(400):
            await asyncio.sleep(0)
            if len(calls) >= 2:
                break
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    with caplog.at_level(logging.WARNING, logger="tortoise.api"):
        asyncio.run(_two_passes())

    assert len(calls) >= 2, "a raising pass ended the retry loop"


def test_the_sweep_task_is_in_the_shared_liveness_tuple_and_shutdown_cancels_it():
    """The task must be disarmed by the SAME tuple re-entry and shutdown read.

    A task armed in the lifespan but absent from ``_LIVENESS_TASK_ATTRS`` is
    orphaned on a re-entry (two sweeps, double delivery) or leaks at shutdown.
    """
    assert "_webhook_sweep_task" in ha._LIVENESS_TASK_ATTRS

    cancelled: list[int] = []

    async def _sleeper():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(1)
            raise

    async def _shutdown():
        app = SimpleNamespace(state=SimpleNamespace())
        app.state._webhook_sweep_task = asyncio.create_task(_sleeper())
        await asyncio.sleep(0)
        await ha._stop_liveness(app)
        assert app.state._webhook_sweep_task.cancelled(), (
            "shutdown left the sweep task running")

    asyncio.run(_shutdown())
    assert cancelled == [1]


# ── the dispatch pool is disposed at THREAD shutdown (#8098 review F2) ────

def _thread_atexit_targets() -> list:
    """The functions registered on ``threading._register_atexit``.

    That helper stores ``functools.partial(func)`` (CPython 3.12
    ``threading._register_atexit`` always wraps), so identity has to be read
    off the partial, not off the list entry.
    """
    import threading

    return [getattr(entry, "func", entry)
            for entry in getattr(threading, "_threading_atexits", ())]


def test_the_sweep_pool_is_disposed_at_thread_shutdown():
    """The pool's workers are NON-daemon and are JOINED at interpreter exit.

    ``_stop_liveness`` cancels the sweep TASK, which stops the ``await`` — the
    futures already QUEUED in the pool keep running, and
    ``concurrent.futures.thread._python_exit`` (registered on
    ``threading._register_atexit``) joins every worker behind them. One queued
    dispatch is a WHOLE graph dispatch (``_dispatch_webhooks_sync``:
    ``MAX_SUBSCRIPTIONS`` 25 x ``DEFAULT_BATCH`` 50 sequential POSTs at
    ``TIMEOUT_S`` 10 s each), so without this hook a SIGTERM during a pass holds
    exit far past fly.toml's ``kill_timeout``. Pre-fix there was no hook at all.

    ``cancel_futures`` only reaches QUEUED futures, so the test must make the
    pool's queue non-empty — which means occupying EVERY worker first: with a
    free worker the second ``submit`` is picked up immediately and is RUNNING,
    and a running future cannot be cancelled (#8098 review, ISSUE 1).
    """
    # The hook must be on `threading._register_atexit` — a plain `atexit`
    # handler runs AFTER `threading._shutdown()` already joined the workers, so
    # it can cancel nothing (the #3087 note, same class).
    assert ha._dispose_webhook_sweep_executor in _thread_atexit_targets(), (
        "the sweep pool has no thread-shutdown dispose hook — its queued "
        "dispatches are joined at exit and a SIGTERM waits them out")

    pool = ha._webhook_sweep_executor()
    assert ha._WEBHOOK_SWEEP_EXECUTOR is pool

    # Queue work that would block forever if it ever ran, then dispose: the
    # queued future must be CANCELLED, not merely abandoned to the join.
    import threading

    release = threading.Event()
    workers = max(int(getattr(pool, "_max_workers", 1)), 1)
    started = [threading.Event() for _ in range(workers)]

    def _blocked(idx: int):
        started[idx].set()
        release.wait(30)
        return "ran"

    try:
        # Occupy EVERY worker, so the submit below is genuinely QUEUED.
        busy = [pool.submit(_blocked, i) for i in range(workers)]
        for idx, ev in enumerate(started):
            assert ev.wait(10), f"the pool never ran worker {idx}"
        queued = pool.submit(_blocked, 0)

        ha._dispose_webhook_sweep_executor()

        assert ha._WEBHOOK_SWEEP_EXECUTOR is None, "the disposed pool was kept"
        assert queued.cancelled(), (
            "a QUEUED dispatch survived disposal — at thread shutdown it runs "
            "out its POST timeouts instead of being dropped")
        assert not any(f.cancelled() for f in busy), (
            "a RUNNING future cannot be cancelled")
    finally:
        release.set()


def test_the_dispose_assertion_discriminates_on_cancel_futures():
    """Control for the test above: make sure it is testing something.

    With the same pool shape and the SAME blocked-then-queued futures,
    ``shutdown(wait=False)`` WITHOUT ``cancel_futures=True`` leaves the queued
    future pending — so the ``queued.cancelled()`` assertion in
    ``test_the_sweep_pool_is_disposed_at_thread_shutdown`` fails if the dispose
    hook ever loses that argument. This is a stdlib-behaviour control (no repo
    code under test), kept beside the assertion it protects.
    """
    import threading

    pool = ha.ThreadPoolExecutor(max_workers=1, thread_name_prefix="8098-control")
    release = threading.Event()
    started = threading.Event()

    def _blocked():
        started.set()
        release.wait(30)
        return "ran"

    try:
        busy = pool.submit(_blocked)
        assert started.wait(10), "the control pool never ran"
        queued = pool.submit(_blocked)
        pool.shutdown(wait=False)  # NO cancel_futures
        assert not queued.cancelled(), (
            "control broken: plain shutdown(wait=False) already cancelled a "
            "queued future, so the dispose test would pass without "
            "cancel_futures=True")
        assert not busy.cancelled()
    finally:
        release.set()
        pool.shutdown(wait=False, cancel_futures=True)


def test_the_sweep_pool_dispose_hook_is_none_safe(monkeypatch):
    """The pool is built LAZILY, so a process that never swept reaches the hook
    with nothing to dispose — and the hook runs during shutdown, where a raise
    is unhelpful and unreportable."""
    monkeypatch.setattr(ha, "_WEBHOOK_SWEEP_EXECUTOR", None)
    ha._dispose_webhook_sweep_executor()  # must not raise
    assert ha._WEBHOOK_SWEEP_EXECUTOR is None
    assert ha._dispose_webhook_sweep_executor in _thread_atexit_targets()


def test_the_sweep_is_armed_as_a_background_task_not_awaited_at_boot():
    """#2953: the bind must not wait for a fleet-wide pass.

    Pinned from source so the test needs no app import: the arm site must be a
    ``create_task`` on ``app.state``, and nothing may ``await`` the loop.
    """
    import ast
    from pathlib import Path

    source = (Path(ha.__file__).resolve()).read_text()
    tree = ast.parse(source, filename="hosted_api.py")
    lifespan = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_lifespan")
    arms = [
        node for node in ast.walk(lifespan)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr == "_webhook_sweep_task"
    ]
    assert len(arms) == 1, "expected exactly one arm site for the sweep task"
    value = arms[0].value
    assert isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) \
        and value.func.attr == "create_task", (
        "the sweep must be scheduled with create_task — awaiting it would "
        "delay the listening socket (#2953)")
    awaited = [
        node for node in ast.walk(lifespan)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "_webhook_retry_sweep_loop"
    ]
    assert not awaited, "the sweep loop is awaited on the startup path"


# ── the plan's own evidence table (a citation that rots is not evidence) ───

PLAN_8098 = (Path(__file__).resolve().parent.parent / "docs" / "plans"
             / "2026-10-18-8098-webhook-retry-sweep.md")


def test_every_test_the_plan_cites_exists():
    """#8098 review (P3): the plan's §5 verification table cited a test that did
    not exist at the PR head.

    §5 is the issue's own checklist, so it is the record a later lane trusts when
    it wants to know which case pins which half of the design. A row pointing at
    a name nothing defines reads exactly like a covered row, and the coverage it
    claims cannot be executed — the defect is invisible to every other gate (no
    lint, no CI check reads plan prose).

    (1) *What value makes this test fail?*  The pre-fix plan: its "the gate is
        NOT always due" row cites `test_a_caught_up_subscription_costs_the_gate_nothing`,
        which no file under `tests/` defines (verified: `grep -rn` over the head
        tree returned only that plan line).
    (2) *Does the test reach it?*  Yes — the committed plan is read, every
        backticked `test_…` name in it is collected, and each one is resolved
        against the names this module defines and then against `tests/**` for
        anything not defined here. Nothing about the assertion depends on the
        rest of the table's prose.
    """
    import re

    text = PLAN_8098.read_text(encoding="utf-8")
    cited = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", text))
    assert len(cited) >= 10, (
        f"the plan's verification table cites only {len(cited)} test(s) — a table "
        "that names nothing pins nothing")

    defined_here = {name for name in globals() if name.startswith("test_")}
    unresolved = sorted(cited - defined_here)
    elsewhere: set[str] = set()
    if unresolved:  # cheap path: almost every citation lives in this module
        here = Path(__file__).resolve()
        for path in here.parent.rglob("test_*.py"):
            if path.resolve() == here:
                continue
            elsewhere |= set(re.findall(
                r"^\s*(?:async\s+)?def (test_[A-Za-z0-9_]+)",
                path.read_text(encoding="utf-8", errors="replace"), re.M))

    missing = sorted(name for name in unresolved if name not in elsewhere)
    assert not missing, (
        f"the #8098 plan cites {missing}, which neither this module nor any "
        "file under tests/ defines — §5 would claim coverage that cannot run")
