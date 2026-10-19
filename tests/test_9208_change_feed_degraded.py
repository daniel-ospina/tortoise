"""#9208 — a dead `:GraphEvent` change feed must not look like a quiet one.

The defect measured on `feat/9124-tm` @ `f6b971dcb` (the issue's body):

    unarmed SDK,     after one create_point:  MATCH (n:GraphEvent) -> 1 row
    journal-backed,  after one create_point:  MATCH (n:GraphEvent) -> 0 rows

and the ONLY trace was ``WARNING tortoise.sdk: event emission failed for
PointAdded — continuing``. §17.1's third door refused the store write; the
`except Exception` in `_emit_event` swallowed it; `create_point` returned
success and `poll_events` answered ``[]``. A subscriber could not tell a dead
feed from an idle one — the silent deletion of a feature the issue forbids in so
many words.

These tests pin the LOSS, not the code that runs around it:

* the first swallow is driven with the EXACT exception the armed door raises
  (`DirectCacheWriteRefused`), because that is the measured failure — the schema
  DDL and the `seq` allocator are the first statements it refuses;
* the assertions establish that the mutation LANDED and that the store holds
  ZERO rows, so a passing test cannot be one where the write merely failed;
* the failure is observed where the FEED's CONSUMER is — `poll_events` — so
  "recorded a variable" cannot pass for "made the loss visible";
* a duplicate append is proven NOT to be reported as a loss: a false positive
  here would break every poll on a healthy feed, which is worse than the bug.

MUTATION for the red tests: drop the `record_degraded` call from
`TortoiseSDK._emit_event`'s except (test 1 stops raising) or from
`event_store.append_event`'s except (test 2 stops raising), or drop the
`degraded()` refusal from `poll_events` (both). Each turns the observed reply
back into a silently short page.
"""
from __future__ import annotations

import pytest

from tortoise import event_store
from tortoise.event_store import ChangeFeedDegraded


@pytest.fixture(autouse=True)
def _no_carried_degradation():
    """The degraded state lives in PROCESS MEMORY, so it must not leak.

    Not cleanup-on-success (that would hide a real leak): the pre-test clear is
    what makes each case start from "healthy", and the post-test clear is what
    keeps an intentionally-degraded case from poisoning the rest of the session
    — several other suites drive DELIBERATE store refusals (the #5379 allocator
    ceiling), and a process-wide latch would refuse their polls.
    """
    event_store.clear_degraded()
    try:
        yield
    finally:
        event_store.clear_degraded()


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal — the §17.1 guard is ARMED.

    Same construction as the `#9123`/`#7951` fixtures: `event_log_path` is what
    arms the guard, and the explicit `db_path` makes the projection embedded
    when no URI is configured (a URI-set session is redirected to its server, so
    the file is lane-agnostic rather than embedded-only).
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "feed_degraded.db"),
                      event_log_path=str(tmp_path / "journal.jsonl"))
    try:
        yield sdk
    finally:
        sdk.close()


def _graph_events(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (n:GraphEvent) RETURN count(n)").result_set
    return int(rows[0][0]) if rows else 0


def _point_landed(sdk, pid: str) -> bool:
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id}) RETURN count(p)",
        params={"id": pid}).result_set
    return bool(rows and int(rows[0][0]) == 1)


def _refuse_the_store_leg(monkeypatch):
    """Make the emit hook's store leg raise the ARMED door's own refusal.

    Patching `next_seq` is the measured path, not a convenient one: the armed
    door refuses `ensure_event_schema`'s DDL first and this allocator's `MERGE`
    next, both of which run in the hook's `try` before `append_event`. Raising
    from the same place with the same exception keeps this test honest about
    WHICH refusal it reproduces.
    """
    from tortoise.projection import DirectCacheWriteRefused

    def refuse(proj):
        raise DirectCacheWriteRefused(
            "Refusing a mutating statement issued at the projection's write "
            "handle (`proj.g`): this projection is the derived cache of a "
            "journal ... statement=MERGE (m:GraphEventMeta) ...")

    monkeypatch.setattr(event_store, "next_seq", refuse)


def test_a_refused_store_write_is_a_visible_loss_not_an_empty_poll(
        armed_sdk, monkeypatch):
    """THE regression: the feed dies, the write succeeds, the poll lies.

    RED before #9208: `poll_events` returns ``{"events": [], ...}`` — the exact
    reply a healthy, idle feed gives — while ``MATCH (n:GraphEvent)`` is 0 and
    the Point is in the graph. Observable loss is the whole deliverable, so the
    assertion is on the CONSUMER's reply, not on a logger or an internal flag.
    """
    sdk = armed_sdk
    _refuse_the_store_leg(monkeypatch)

    point = sdk.create_point("statement", "the change feed write is refused")

    # The LOSS, established before the visibility is judged: the mutation landed
    # (so this is not a failed write) and the store holds nothing (so the event
    # really is gone, not merely delayed).
    assert _point_landed(sdk, point["id"]), (
        "the Point must be in the graph — otherwise this test would be "
        "observing a failed write instead of a lost event")
    assert _graph_events(sdk) == 0, (
        "the refusal must have cost the event; a non-zero count means the "
        "simulated refusal never reached the store leg")

    with pytest.raises(ChangeFeedDegraded) as excinfo:
        sdk.poll_events()

    message = str(excinfo.value)
    assert "PointAdded" in message, (
        f"the named state must name WHAT was lost; observed={message!r}")
    assert "DirectCacheWriteRefused" in message, (
        f"the named state must name the refusal that caused it; "
        f"observed={message!r}")
    assert "clear_degraded" in message, (
        f"the refusal must name the acknowledgement path, or the reader is "
        f"stuck with no remedy; observed={message!r}")


def test_the_row_write_swallow_is_not_silent_either(armed_sdk, monkeypatch):
    """The SECOND swallow: `append_event` catching its own write failure.

    The emit hook's `try` never sees this one — `append_event` swallows the
    exception itself and returns `False`, which `_emit_event` does not inspect.
    A fix that only narrowed the outer catch would leave this path exactly as
    silent as before, so it is pinned separately with its own refusal point.
    """
    from tortoise.projection import DirectCacheWriteRefused, _GuardedGraph

    sdk = armed_sdk
    real = _GuardedGraph.query

    def query(self, cypher, params=None, timeout=None):
        if cypher.lstrip().startswith("CREATE (e:GraphEvent"):
            raise DirectCacheWriteRefused(
                "Refusing a mutating statement issued at the projection's "
                "write handle (`proj.g`) ... statement=CREATE (e:GraphEvent ...")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)

    point = sdk.create_point("statement", "the append itself is refused")

    assert _point_landed(sdk, point["id"]), (
        "best-effort is the contract: `_emit_event` must not crash the mutation")
    assert _graph_events(sdk) == 0, "the append was refused — nothing landed"
    assert event_store.degraded(sdk._get_proj()) is not None, (
        "a refused row write must be recorded; the outer catch never sees it")

    with pytest.raises(ChangeFeedDegraded):
        sdk.poll_events()


def test_a_duplicate_append_is_not_reported_as_a_loss(armed_sdk, monkeypatch):
    """A duplicate loses NOTHING, so it must never degrade the feed.

    `append_event` returns `False` for two different outcomes. Reporting the
    duplicate one as a loss would make `poll_events` refuse on a HEALTHY feed —
    a false positive strictly worse than the silence it replaces. Today the
    duplicate is caught by the app-side pre-check; the store's unique constraint
    is the second line of defence, and THAT is the branch exercised here (a
    pre-check that saw 0 rows, then a write the constraint refused).
    """
    from tortoise.projection import _GuardedGraph

    class _Rows:
        def __init__(self, rows):
            self.result_set = rows

    sdk = armed_sdk
    proj = sdk._get_proj()
    assert event_store.append_event(
        proj, event_store.next_seq(proj), "PointAdded", {"id": "p1"}, "ev-dup")

    real = _GuardedGraph.query
    pre_checks = {"n": 0}

    def query(self, cypher, params=None, timeout=None):
        # The FIRST `event_id` lookup (the pre-check) lies and reports 0, so the
        # write is attempted; the CREATE then fails on the unique constraint; the
        # post-failure re-read is truthful and finds the row already there.
        if "RETURN count(e)" in cypher and pre_checks["n"] == 0:
            pre_checks["n"] = 1
            return _Rows([[0]])
        if cypher.lstrip().startswith("CREATE (e:GraphEvent"):
            raise RuntimeError(
                "Constraint Validation failed: unique constraint "
                "tortoise_ge_uid violated")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)

    assert event_store.append_event(
        proj, event_store.next_seq(proj), "PointAdded", {"id": "p1"},
        "ev-dup") is False
    assert event_store.degraded(proj) is None, (
        "a duplicate event_id loses no event, so the feed is NOT degraded")

    monkeypatch.undo()
    reply = sdk.poll_events()
    assert [e["event_id"] for e in reply["events"]] == ["ev-dup"]


def test_a_healthy_feed_answers_and_leaves_a_row(armed_sdk):
    """The indicator the issue asks for, on an ARMED (journal-backed) SDK.

    Nothing here is a simulation: `create_point` on a journal-backed SDK must
    leave a `:GraphEvent` row and `poll_events` must return it. This is the
    case that fails when a declaration is missing under an armed door, and it
    is also the guard against the degraded state firing on a healthy feed.
    """
    sdk = armed_sdk

    point = sdk.create_point("statement", "the change feed survives the armed door")

    assert _graph_events(sdk) == 1, (
        "one create_point must leave exactly one :GraphEvent row")
    assert event_store.degraded(sdk._get_proj()) is None

    reply = sdk.poll_events()
    assert [e["type"] for e in reply["events"]] == ["PointAdded"]
    assert reply["events"][0]["payload"]["id"] == point["id"]
    assert reply["next_cursor"]


def test_a_later_success_does_not_un_degrade_the_feed(armed_sdk, monkeypatch):
    """The gap is permanent, so the signal must not clear itself on recovery.

    Clearing on the next successful write is the tempting "self-healing" version
    of this fix, and it restores exactly the silence the state exists to break:
    the events in the gap were never written, so a subscriber parked below them
    would under-read forever while the feed looked healthy again.
    """
    sdk = armed_sdk
    _refuse_the_store_leg(monkeypatch)
    sdk.create_point("statement", "lost while the store leg was refused")
    assert event_store.degraded(sdk._get_proj()) is not None

    monkeypatch.undo()  # the refusal stops: the store is healthy again
    sdk.create_point("statement", "written after recovery")
    assert _graph_events(sdk) == 1, "the recovered write must land"

    with pytest.raises(ChangeFeedDegraded):
        sdk.poll_events()

    event_store.clear_degraded(sdk._get_proj().graph_name)
    reply = sdk.poll_events()
    assert [e["type"] for e in reply["events"]] == ["PointAdded"], (
        "after the explicit acknowledgement the feed answers again — the one "
        "lost event is gone, which is what acknowledging it means")


def test_an_unverifiable_append_is_reported_as_unverified_not_as_a_loss(
        armed_sdk, monkeypatch):
    """The third outcome: the write failed AND the read-back failed.

    `append_event` distinguishes a duplicate from a loss by re-reading the store
    for the `event_id`. When that read ALSO fails, neither "landed" nor "lost" is
    provable — the socket may have died after the server committed. Collapsing
    that into "lost" would report a permanent refusal over an unproven gap, and
    into "landed" would be the old lie. The state names the uncertainty instead,
    which is the only honest third answer for a dict whose job is to NAME the
    diagnosis.
    """
    from tortoise.projection import _GuardedGraph

    sdk = armed_sdk
    real = _GuardedGraph.query
    pre_checks = {"n": 0}

    def query(self, cypher, params=None, timeout=None):
        if cypher.lstrip().startswith("CREATE (e:GraphEvent"):
            raise RuntimeError("Connection closed by server")
        if "RETURN count(e)" in cypher:
            # The PRE-check must succeed (there is no row yet) so the write is
            # attempted; only the post-failure READ-BACK is unreadable.
            pre_checks["n"] += 1
            if pre_checks["n"] > 1:
                raise RuntimeError("Connection closed by server")
        return real(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)

    sdk.create_point("statement", "append and read-back both fail")

    state = event_store.degraded(sdk._get_proj())
    assert state is not None
    assert state["stage"] == "append-unverified", (
        "an unreadable store proves neither a loss nor a landing, so the stage "
        f"must say so; observed={state['stage']!r}")
    with pytest.raises(ChangeFeedDegraded) as excinfo:
        sdk.poll_events()
    assert "append-unverified" in str(excinfo.value)


def test_the_emit_hook_never_raises_even_when_the_recorder_fails(
        armed_sdk, monkeypatch):
    """The handler must not become a new failure mode.

    The emit hook is best-effort BY CONTRACT: several doors emit AFTER their
    cache write, so a raise out of `_emit_event` would report a FAILED mutation
    that actually landed. Recording the degraded state therefore must not be
    able to escalate — its own import comes from the module the handler is
    handling, and a raise inside the handler would escape it.
    """
    sdk = armed_sdk
    _refuse_the_store_leg(monkeypatch)

    def explode(*_args, **_kwargs):
        raise RuntimeError("the recorder itself is broken")

    monkeypatch.setattr(event_store, "record_degraded", explode)

    point = sdk.create_point("statement", "the recorder explodes")
    assert _point_landed(sdk, point["id"]), (
        "a broken recorder must not turn a landed mutation into a reported "
        "failure")


def test_the_store_identity_includes_the_server_PORT():
    """Two servers on one HOST are two stores — the port is part of identity.

    This repo's own environment runs two FalkorDB servers on loopback (`:6379`
    the test matrix, `:16379` canonical/dev), both with the default graph name
    `tortoise`. Keyed on the host alone, a refused write against one would
    permanently refuse `poll_events` on the other. `FalkorProjection` records
    `_host` but not `_port`, so the port is read from the client's connection
    pool — the same seam `tests/_embedded._projection_host` uses — and that seam
    is what this unit pin fixes in place.
    """
    from types import SimpleNamespace

    def stub(*, path=None, host=None, port=None, graph="tortoise"):
        kwargs = {"host": host, "port": port}
        return SimpleNamespace(
            _path=path, _host=host, graph_name=graph,
            db=SimpleNamespace(connection=SimpleNamespace(
                connection_pool=SimpleNamespace(connection_kwargs=kwargs))))

    here = event_store._graph_key(stub(host="localhost", port=16379))
    there = event_store._graph_key(stub(host="localhost", port=6379))
    assert here != there, (
        "two servers on one host must not share a degraded key; "
        f"both keyed as {here!r}")
    assert here == event_store._graph_key(stub(host="localhost", port=16379))

    # The graph half of the composite key, in the same store.
    assert event_store._graph_key(
        stub(host="localhost", port=16379, graph="tenant_a")) != here

    # The embedded half: two DATABASES are two stores even under one name.
    assert event_store._graph_key(stub(path="/a.db")) != event_store._graph_key(
        stub(path="/b.db"))


def test_an_unconfigured_feed_is_not_degraded(armed_sdk):
    """A refusal must not be confused with "the feed was never written to".

    The issue names this distinction explicitly: a graph that never emitted must
    keep answering `{"events": []}`. Degrading it would turn every fresh tenant
    into a broken one.
    """
    sdk = armed_sdk
    assert _graph_events(sdk) == 0
    assert event_store.degraded(sdk._get_proj()) is None
    assert sdk.poll_events()["events"] == []

def test_a_dead_feed_on_one_graph_does_not_refuse_another(tmp_path, monkeypatch):
    """The degraded state is per (STORE, GRAPH) — the product's own partition.

    ⚠️ BOTH halves of the key must be distinguishable IN THIS TEST or it pins
    neither. An earlier version opened the two SDKs on different db paths AND
    different graph names, so a store-ONLY key and a graph-ONLY key both passed:
    it proved nothing about the over-read it exists to prevent. Here both SDKs
    sit on the SAME database with DIFFERENT graph names — the shape the product
    actually has (one store, one graph per tenant/namespace), and the only shape
    where a wrong key refuses a healthy tenant's poll.
    """
    from tortoise.sdk import TortoiseSDK

    db_path = str(tmp_path / "shared.db")
    journal = str(tmp_path / "journal.jsonl")

    dead = TortoiseSDK(db_path, event_log_path=journal, graph_name="tenant_a")
    healthy = TortoiseSDK(db_path, event_log_path=journal, graph_name="tenant_b")
    try:
        assert dead._get_proj()._path == healthy._get_proj()._path, (
            "the two SDKs must share ONE store, or this test cannot tell a "
            "per-store key from a per-graph one")

        _refuse_the_store_leg(monkeypatch)
        dead.create_point("statement", "lost on the degraded graph")
        assert event_store.degraded(dead._get_proj()) is not None
        monkeypatch.undo()  # tenant_b's graph must see a WORKING store leg

        point = healthy.create_point("statement", "this graph's feed is fine")
        assert _graph_events(healthy) == 1
        reply = healthy.poll_events()
        assert [e["type"] for e in reply["events"]] == ["PointAdded"]
        assert reply["events"][0]["payload"]["id"] == point["id"]

        with pytest.raises(ChangeFeedDegraded):
            dead.poll_events()
    finally:
        healthy.close()
        dead.close()


def test_two_stores_under_one_graph_name_do_not_share_a_state(tmp_path,
                                                              monkeypatch):
    """The OTHER half of the composite key: the store identity matters.

    Two databases whose graphs share a NAME are two stores — and this repo's own
    environment runs exactly that shape (the test matrix and the canonical/dev
    FalkorDB both default to the graph name `tortoise`). Keyed on the graph name
    alone, a refused write on one permanently refuses the other's poll.
    """
    from tortoise.sdk import TortoiseSDK

    dead = TortoiseSDK(str(tmp_path / "one.db"),
                       event_log_path=str(tmp_path / "one.jsonl"),
                       graph_name="tortoise")
    healthy = TortoiseSDK(str(tmp_path / "two.db"),
                          event_log_path=str(tmp_path / "two.jsonl"),
                          graph_name="tortoise")
    try:
        assert dead._get_proj()._path != healthy._get_proj()._path

        _refuse_the_store_leg(monkeypatch)
        dead.create_point("statement", "lost in the first store")
        assert event_store.degraded(dead._get_proj()) is not None
        monkeypatch.undo()

        healthy.create_point("statement", "fine in the second store")
        assert _graph_events(healthy) == 1
        assert [e["type"] for e in healthy.poll_events()["events"]] == [
            "PointAdded"]

        with pytest.raises(ChangeFeedDegraded):
            dead.poll_events()
    finally:
        healthy.close()
        dead.close()
