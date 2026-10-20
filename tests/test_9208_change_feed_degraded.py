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

import functools
import os
import threading
import time
from pathlib import Path

import pytest

from tortoise import event_store
from tortoise.event_store import ChangeFeedDegraded

# ⛔ NO module-level `_no_carried_degradation` fixture here any more (#9208
# review). The suite-wide `tests/conftest.py::_change_feed_state_isolation`
# already clears `event_store._DEGRADED` before and after every test, and it also
# NAMES the test that left a latch behind. A second, module-scoped autouse
# fixture is finalised AFTER a conftest-level one, so its silent
# `clear_degraded()` ran FIRST and the conftest sample then always saw an empty
# map — the attribution half was dead in the one file whose tests degrade feeds
# on purpose, i.e. exactly where a future leak is most likely. The consequence of
# dropping it: a test below that latches a state and forgets to clear it now
# REDS ITSELF (naming its own nodeid) instead of being silently tidied. Every
# such test therefore clears in a `finally` — that is the file's contract, not a
# courtesy.


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal — `_journal_backed` is True.

    ⚠️ WHAT THIS DOES **NOT** DO (#9208 review, P3): it does not put the §17.1
    third door on the path these tests drive. `DirectCacheWriteRefused` is
    raised by `FalkorProjection.apply`/`.query` (the fold and the raw-query
    lane); `event_store` writes through `proj.g`, i.e. `_GuardedGraph.query`,
    whose refusals are the `=~` operator, the numeric-domain guards and the
    bulk-wipe L2 — none of which is that door. On `main` the door does not
    reach `proj.g` at all, so the refusal is SIMULATED here (`_refuse_the_store_leg`)
    at the statement it would refuse, and the "feed survives an armed door"
    claim belongs to #9124 part 1 / #9079.
    `test_the_9124_door_does_not_reach_the_write_handle_yet` is the pin that
    reds when that changes, so the simulation is re-evaluated rather than
    remembered.

    Same construction as the `#9123`/`#7951` fixtures: `event_log_path` is what
    arms the journal fold, and the explicit `db_path` makes the projection
    embedded when no URI is configured (a URI-set session is redirected to its
    server, so the file is lane-agnostic rather than embedded-only).
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "feed_degraded.db"),
                      event_log_path=str(tmp_path / "journal.jsonl"))
    try:
        yield sdk
    finally:
        sdk.close()


def _acknowledges_the_loss_it_latches(func):
    """Clear the process-global latch this test leaves ON PURPOSE — in a `finally`.

    `tests/conftest.py::_change_feed_state_isolation` FAILS a test that leaves a
    degraded feed behind, naming the polluter and then clearing it. That
    attribution is only worth having if the tests which degrade a feed
    DELIBERATELY acknowledge the loss themselves, so every such test opts in
    here: the clear happens on the raising path too. A test that latches without
    this decorator (and without its own clear) now reds ITSELF — which is the
    contract, not a regression.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            event_store.clear_degraded()

    return wrapper


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


@_acknowledges_the_loss_it_latches
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


@_acknowledges_the_loss_it_latches
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
    """A journal-backed SDK's feed WORKS: one `:GraphEvent` row, one poll.

    ⚠️ NOT the issue's Indicator (`with §17.1's third door armed, create_point
    leaves a :GraphEvent row`): that door is not wired to `proj.g` on this
    branch, so no armed-door case is expressible here — see `armed_sdk` and
    `test_the_9124_door_does_not_reach_the_write_handle_yet`. What this pins is
    the healthy direction plus the false-positive guard: a journal-backed SDK
    must leave a row and must NOT be reported as degraded.
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


def test_the_refusal_tells_the_OPERATOR_what_to_do_not_the_caller(
        armed_sdk, monkeypatch):
    """`degraded_graphs()` is the /health read seam, and the raise addresses
    the operator — not the client that cannot act (#9208 review).

    The state is process memory whose only acknowledgement is a Python call
    inside the process that observed the loss. Two consequences, both pinned
    here:

    * a reporting surface with no projection to hand (``/health``) needs the
      state in LIST form — keyed by (store, graph), which only a projection can
      compute, so the listing must carry the store itself;
    * the 503 detail an HTTP client receives must not prescribe that call to the
      client. It names the exits (in-process clear, or restart this worker) and
      says the client cannot make them.
    """
    sdk = armed_sdk
    assert event_store.degraded_graphs() == [], (
        "a healthy process reports NO degraded feeds")

    _refuse_the_store_leg(monkeypatch)
    sdk.create_point("statement", "lost while the store leg was refused")

    rows = event_store.degraded_graphs()
    assert len(rows) == 1, rows
    assert rows[0]["graph"] == sdk._get_proj().graph_name
    assert rows[0]["stage"] == "emit"
    assert rows[0]["lost"] == 1
    assert rows[0]["store"], "the listing must name the store, not only the graph"

    # A copy, never the live dict: a reporter must not be able to rewrite the
    # state by holding it.
    rows[0]["stage"] = "append"
    rows[0]["lost"] = 999
    assert event_store.degraded_graphs()[0]["stage"] == "emit"
    assert event_store.degraded(sdk._get_proj())["lost"] == 1

    with pytest.raises(ChangeFeedDegraded) as excinfo:
        sdk.poll_events()
    detail = str(excinfo.value)
    assert "OPERATOR" in detail, (
        "the remedy must be addressed to the operator; the recipient of this "
        f"raise is a client: {detail[:300]}")
    assert "restart that worker" in detail, (
        "an unreachable worker's only exit is a restart, and the message must "
        "name it")
    assert "cannot be cleared by this API client" in detail, (
        "the message must not read as an instruction the client can follow")

    event_store.clear_degraded()
    assert event_store.degraded_graphs() == []


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


@_acknowledges_the_loss_it_latches
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
    assert state["first_stage"] == "append-unverified", (
        "the FIRST diagnosis must be kept, not overwritten by a later one: it "
        "is often the only provable one (#9208 review); "
        f"observed={state['first_stage']!r}")
    with pytest.raises(ChangeFeedDegraded) as excinfo:
        sdk.poll_events()
    assert "append-unverified" in str(excinfo.value)

    # A SECOND, different failure on the same graph: the latest diagnosis moves
    # and the first is still readable beside it.
    event_store.record_degraded(sdk._get_proj(), "append", "PointAdded", None,
                                RuntimeError("row really is gone"))
    state = event_store.degraded(sdk._get_proj())
    assert (state["stage"], state["first_stage"]) == ("append", "append-unverified"), (
        "the later stage must not discard the first failure's diagnosis; "
        f"observed stage={state['stage']!r} "
        f"first_stage={state['first_stage']!r}")
    assert state["lost"] == 2, "both failures are losses, so both are counted"

    # AND THE CONSUMER SEES BOTH (#9208 review): keeping `first_stage` in the
    # state is only worth anything if the one surface a caller can act on
    # reports it. Reporting the latest alone would discard the diagnosis this
    # field was added to preserve.
    with pytest.raises(ChangeFeedDegraded) as excinfo:
        sdk.poll_events()
    detail = str(excinfo.value)
    assert "The FIRST failure on this graph was at stage 'append-unverified'" in detail, (
        "the 503 detail must carry the first diagnosis beside the latest; "
        f"got: {detail[:400]}")
    assert "stage 'append'" in detail, (
        "the LATEST diagnosis must still be named, and named as the latest")
    # ⚠️ AND THE POINTER MUST POINT SOMEWHERE (#9208 review, P3): the FIRST clause
    # is APPENDED, so the latest stage is named ABOVE it. "the stage named below"
    # sent the reader past the end of the string, and a presence-only assertion
    # could not see it — the early clause satisfies `"stage 'append'" in detail`
    # on its own. So assert the ORDER and the direction word.
    assert detail.index("stage 'append'") < detail.index("The FIRST failure"), (
        "the latest stage must be named BEFORE the appended first-failure "
        f"clause, or no directional pointer can be true; got: {detail[-300:]}")
    assert "the stage named above is the LATEST" in detail, (
        "the appended clause must point ABOVE (where the latest stage is named), "
        f"not 'below' — nothing follows it; got: {detail[-300:]}")
    assert "below" not in detail.rsplit("The FIRST failure", 1)[1], (
        "no part of the appended clause may tell the reader to look below "
        f"itself; got: {detail[-300:]}")
    # ⛔ AND THE APPENDED CLAUSE MUST QUOTE THE *FIRST* ERROR (#9208 review, P3).
    # Asserting `"row really is gone" in detail` proved nothing: that is the
    # LATEST error, rendered in the MAIN clause from `state['error']`, so it is
    # present whatever the appended clause says — swapping
    # `state.get('first_error')` for `state.get('error')` in `sdk.poll_events`
    # left every assertion above green. So read the appended segment itself and
    # require it to carry the first error and NOT the latest one.
    appended = detail.rsplit("The FIRST failure", 1)[1]
    assert "Connection closed by server" in appended, (
        "the appended clause must quote the FIRST error (the unverifiable one), "
        f"not just name its stage; got: {appended[:300]}")
    assert "row really is gone" not in appended, (
        "the appended clause must quote the FIRST error, not the latest one — "
        f"the latest already appears in the main clause; got: {appended[:300]}")


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


def test_the_9124_door_does_not_reach_the_write_handle_yet(armed_sdk):
    """VISIBILITY pin for the #9124 dependency, not a proof of this fix.

    Every refusal in this file is SIMULATED at the statement the §17.1 third
    door would refuse, because on this branch the door does not sit on
    `proj.g` (see `armed_sdk`). That is a fact about the code, and a fact
    recorded only in a docstring goes stale silently — so it is asserted here:
    a mutating statement through the write handle is ACCEPTED today.

    When #9124 part 1 lands the door on this handle, this test goes RED on
    purpose. That is the moment to (a) re-read `_refuse_the_store_leg` — the
    measured case then comes from the real door instead of a patch — and (b)
    turn the armed-door Indicator into a real test
    (`test_a_healthy_feed_answers_and_leaves_a_row`'s original claim).
    """
    from tortoise.projection import DirectCacheWriteRefused

    proj = armed_sdk._get_proj()
    try:
        rows = proj.g.query(
            "CREATE (n:_DoorProbe {id:'probe'}) RETURN n.id").result_set
    except DirectCacheWriteRefused as exc:  # pragma: no cover — future state
        pytest.fail(
            "the §17.1 third door now refuses writes at `proj.g` "
            f"({exc}). The refusal these tests simulate is now a REAL refusal: "
            "re-derive `_refuse_the_store_leg` from the live door, restore the "
            "armed-door Indicator in "
            "`test_a_healthy_feed_answers_and_leaves_a_row`, and remove this pin "
            "(#9124 part 1 landed).")
    assert rows, "the probe write must reach the store while the door is absent"


def test_the_degraded_state_is_atomic_across_threads():
    """The hosted path records from WORKER THREADS while `/health` reads.

    `record_degraded` runs inside `hosted_api._graph_offload` — the dedicated
    #3498 `graph` pool (`_cp_offload` -> `monitoring.run_control_plane_call`),
    NOT `asyncio.to_thread`'s shared default pool (#3060) — and
    `degraded_graphs()` runs on the event loop from the `async def` health
    route (`_health_payload` -> `_change_feed_health`) — two thread populations,
    over one module dict. What the lock guarantees, and what is asserted here:
    `lost` is an exact
    read-modify-write (that count is quoted to the consumer verbatim, so an
    undercount is a false number about data loss), and a report taken while
    writers are live neither raises nor tears.

    ⚠️ NON-VACUITY, STATED HONESTLY — this is a ROBUSTNESS pin, not a
    red-before-green proof. On CPython the pre-lock shape did not observably
    fail this harness: `list(dict.items())` is atomic under the GIL, and the
    unlocked `previous["lost"] + 1` did not undercount in 10/10 trials at both
    8x60 and 8x20,000 (the GIL's 5 ms switch interval is long relative to one
    increment, so writers effectively serialise). The lock is taken because the
    alternative is a CPython-implementation accident, not a contract — it must
    hold for free-threaded builds, and it reds here if a future refactor puts an
    await or a hand-off between the read and the write.
    """
    from types import SimpleNamespace

    event_store.clear_degraded()
    proj = SimpleNamespace(_path="/atomic.db", _host=None,
                           graph_name="atomic", db=None)
    threads_n, per_thread = 8, 60
    new_keys = 40
    start = threading.Barrier(threads_n + 2)  # workers + resizer + this thread
    # ⚠️ A THIRD gate, because `assert seen` was a race (#9208 review): the
    # reporter loop only starts after `start.wait()` returns, so on a fast lane
    # (Linux, where these increments cost ~1 ms not ~190 ms) the writers can
    # finish their whole workload before the main thread is scheduled again —
    # and a CORRECT implementation would then fail on `seen == []`. Writers
    # hold here until the reporter has taken its first sample, so the first
    # sample is deterministic AND the rest run concurrently with real writes.
    sampled = threading.Event()

    def record() -> None:
        start.wait()
        sampled.wait()
        for _ in range(per_thread):
            event_store.record_degraded(proj, "append", "PointAdded", None,
                                        RuntimeError("lost"))

    def mint_keys() -> None:
        """New (store, graph) keys — a RESIZE while the reporter iterates."""
        start.wait()
        sampled.wait()
        for i in range(new_keys):
            event_store.record_degraded(
                SimpleNamespace(_path=f"/resize-{i}.db", _host=None,
                                graph_name="r", db=None),
                "emit", "PointAdded", None, RuntimeError("lost"))

    workers = [threading.Thread(target=record) for _ in range(threads_n)]

    for t in workers:
        t.start()
    resizer = threading.Thread(target=mint_keys)
    resizer.start()
    start.wait()  # release every thread at once
    # ⚠️ `sampled.set()` is in a `finally`: if this first sample RAISED, eight
    # non-daemon workers would block on `sampled.wait()` forever and turn a
    # report bug into a hung shard (#9208 review, P3).
    try:
        seen = [len(event_store.degraded_graphs())]  # the gate's first sample
    finally:
        sampled.set()
    while any(t.is_alive() for t in (*workers, resizer)):
        try:
            seen.append(len(event_store.degraded_graphs()))
        except RuntimeError as exc:  # pragma: no cover — the pre-lock failure
            pytest.fail(
                "reporting the degraded state must not raise on a concurrent "
                f"insert — /health swallows that and reports `degraded: false`: {exc}")
        time.sleep(0.001)
    for t in (*workers, resizer):
        t.join()

    # WHAT THE GATE ACTUALLY GUARANTEES (and what is asserted): the first sample
    # is taken while every writer is still held at `sampled.wait()`, so it must
    # see an EMPTY map. The old `assert seen` was a one-element list and could
    # never fail (#9208 review, P3); this one reds if the gate is ever moved
    # after the writers are released.
    assert seen[0] == 0, (
        "the first sample must precede every write — the writers are held at "
        f"`sampled.wait()` until it is taken; observed={seen[0]!r}")
    # The NUMBER of samples is deliberately NOT asserted: it depends on how long
    # the writers take to finish after the gate opens, which is a timing claim on
    # a fast lane — the class of latent false red this test already had to remove
    # (#9208 review). What the gate guarantees is the FIRST sample only.
    state = event_store.degraded(proj)
    assert state["lost"] == threads_n * per_thread, (
        "every loss must be counted — the count is quoted to the consumer as "
        f"\"N event(s) are unaccounted for\"; observed={state['lost']!r}")
    assert len(event_store.degraded_graphs()) == new_keys + 1
    event_store.clear_degraded()


def test_the_store_identity_is_one_key_however_the_path_is_spelled(tmp_path):
    """One database reached through two spellings is ONE store (#9208 review).

    A symlinked deployment directory, a `..`-segment spelling and a
    `~`-spelled home are the same file. Keyed on the raw string, the state
    SPLITS: a refused write recorded under one spelling leaves a poll through
    the other answering `[]` over the lost event — the silence this module
    exists to break. The canonicalisation is the repo's own
    (`realpath(abspath(expanduser()))`, cf. `projection._prewipe_db_path_identity`
    and `fallback_snapshot.snapshot_key`).

    ⚠️ EVERY SPELLING HERE IS BUILT SO IT IS FALSIFIABLE, which cost this test
    its first version (#9208 review): `str(Path("/tmp") / ".." / "/tmp" / "one.db")`
    is `'/tmp/one.db'` — pathlib DISCARDS the left side when the right operand is
    absolute — so the assertion compared a string to itself and would have passed
    for an identity function. The `..` form is a literal string, and the symlink
    is a real one created below.
    """
    from types import SimpleNamespace

    def key(path):
        return event_store._graph_key(
            SimpleNamespace(_path=str(path), _host=None, graph_name="tortoise",
                            db=None))

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)
    assert key(link_dir / "one.db") == key(real_dir / "one.db"), (
        "a symlinked deployment directory must key as the database it names: "
        f"{key(link_dir / 'one.db')!r} != {key(real_dir / 'one.db')!r}")

    dotted = f"{tmp_path}/sub/../one.db"
    assert key(dotted) == key(tmp_path / "one.db"), (
        "a `..`-segment spelling of one database must not key apart: "
        f"{key(dotted)!r} != {key(tmp_path / 'one.db')!r}")

    assert key("~/feed.db") == key(str(Path("~").expanduser() / "feed.db")), (
        "a `~`-spelled path must key as the database it names")
    assert key(":memory:") != key(str(Path(":memory:").absolute())), (
        "`:memory:` names no file — folding the cwd into it would make two "
        "cwds two keys for one embedded daemon")


def test_a_real_projection_reports_the_store_identity_the_key_uses(tmp_path):
    """The `_store_identity` seam against a REAL projection, in BOTH lanes.

    The unit pin above hand-builds the client shape it reads
    (`db.connection.connection_pool.connection_kwargs['port']`) — the same
    presumption `tests/_embedded._projection_host` makes — so a divergence
    between that stub and the real client would keep the PORT out of the key
    while the unit pin stayed green. This reads the identity off a projection
    the SDK actually built:

    * embedded lane → `file:<path>`, i.e. two databases really do key apart;
    * server lane (#1647 test redirect discards the explicit path) →
      `host:<host>:<port>` with a NON-`None` port RESOLVED FROM THE REAL POOL.
      `None` here is the #9208 regression this test exists to catch: keyed on the
      host alone, this repo's two loopback servers (`:6379` test matrix,
      `:16379` canonical/dev) — both defaulting to the graph name `tortoise` —
      would share one degraded key, so a refusal on one would permanently refuse
      the other's polls.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "identity.db"),
                      event_log_path=str(tmp_path / "identity.jsonl"))
    try:
        proj = sdk._get_proj()
        identity = event_store._store_identity(proj)
        if proj._path:
            canonical = os.path.realpath(os.path.abspath(str(proj._path)))
            assert identity == f"file:{canonical}", (
                "an embedded projection's identity is its database FILE, "
                f"canonically spelled; got {identity!r}")
            return
        pool = proj.db.connection.connection_pool
        port = pool.connection_kwargs["port"]
        assert port is not None, "the real client must carry the port"
        assert identity == f"host:{proj._host}:{port}", (
            "the store identity must carry the PORT read from the REAL "
            f"connection pool; got {identity!r} for host={proj._host!r}, "
            f"port={port!r}")
    finally:
        sdk.close()


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


@_acknowledges_the_loss_it_latches
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
        # Asserted through the code's OWN identity seam, not `_path`: under a
        # URI-set session (#1647's test redirect) BOTH projections are host-mode
        # (`_path is None`) on the SAME server, so `_path == _path` is
        # `None == None` — true, and pinning nothing. `_store_identity` is the
        # half this test is about, and it names one store in either lane.
        assert (event_store._store_identity(dead._get_proj())
                == event_store._store_identity(healthy._get_proj())), (
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


@_acknowledges_the_loss_it_latches
def test_two_stores_under_one_graph_name_do_not_share_a_state(tmp_path,
                                                              monkeypatch):
    """The OTHER half of the composite key: the store identity matters.

    Two databases whose graphs share a NAME are two stores — and this repo's own
    environment runs exactly that shape (the test matrix and the canonical/dev
    FalkorDB both default to the graph name `tortoise`). Keyed on the graph name
    alone, a refused write on one permanently refuses the other's poll.

    ⚠️ EMBEDDED LANE ONLY, and gated on it EXPLICITLY. Two `db_path`s are two
    stores only when each one is honoured. Under `TORTOISE_DB_URI` — the lane
    CI's `test` shards run — `FalkorProjection.__init__`'s #1647 test redirect
    DISCARDS an explicit path for a non-exempt stem
    (`tests/_embedded.py::TEST_NO_REDIRECT_STEMS`) and connects to the server,
    re-deriving a graph name per path. Both SDKs would then be ONE store with
    TWO derived names: the shape this test exists to distinguish from
    "two stores", and every assertion below would pass while the store half of
    the key went unpinned. Skipping is the honest answer — the store half is
    pinned lane-agnostically by
    `test_a_real_projection_reports_the_store_identity_the_key_uses` and by the
    `_store_identity` unit pin above.
    """
    from tortoise.sdk import TortoiseSDK

    dead = TortoiseSDK(str(tmp_path / "one.db"),
                       event_log_path=str(tmp_path / "one.jsonl"),
                       graph_name="tortoise")
    healthy = TortoiseSDK(str(tmp_path / "two.db"),
                          event_log_path=str(tmp_path / "two.jsonl"),
                          graph_name="tortoise")
    try:
        if dead._get_proj()._path is None or healthy._get_proj()._path is None:
            pytest.skip(
                "the two-stores shape is embedded-only: this session has "
                "TORTOISE_DB_URI set, so the #1647 test redirect discarded "
                "both explicit paths and put both SDKs on ONE server store")
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


def test_every_writer_takes_the_degraded_map_lock_and_the_reader_does_not(
        monkeypatch):
    """The lock is a KEEP-IT contract, pinned by acquisition, not by a race.

    `test_the_degraded_state_is_atomic_across_threads` is a robustness pin and
    says so: under CPython's GIL the pre-lock shape does not observably fail it,
    so deleting `with _DEGRADED_LOCK` from all three writers leaves that test
    green (#9208 review). This one is DETERMINISTIC — it counts `__enter__` on a
    real lock wrapper, with no threads and no timing — and it pins BOTH halves of
    the documented contract:

    * the three writers (`record_degraded`, `degraded_graphs`, `clear_degraded`)
      take it, because each is a read-modify-write or a resize-sensitive walk;
    * `degraded()` does NOT, because it is a single `dict.get` over a dict that
      `record_degraded` publishes instead of mutating — locking it would
      serialise every `poll_events` behind an emitting thread's error path.

    MUTATION that reds it: drop any one `with _DEGRADED_LOCK:` in
    `tortoise/event_store.py`, or "tidy" a lock into `degraded()`.
    """
    from types import SimpleNamespace

    class CountingLock:
        def __init__(self):
            self.enters = 0
            self._real = threading.Lock()

        def __enter__(self):
            self.enters += 1
            self._real.acquire()
            return self

        def __exit__(self, *exc_info):
            self._real.release()
            return False

    counting = CountingLock()
    monkeypatch.setattr(event_store, "_DEGRADED_LOCK", counting)
    event_store.clear_degraded()
    proj = SimpleNamespace(_path="/locked.db", _host=None, graph_name="locked",
                           db=None)

    def enters_during(fn):
        before = counting.enters
        fn()
        return counting.enters - before

    assert enters_during(
        lambda: event_store.record_degraded(proj, "append", "PointAdded", None,
                                            RuntimeError("lost"))) == 1, (
        "`record_degraded` must hold _DEGRADED_LOCK across its whole "
        "read-modify-write — `lost` is an exact count quoted to the consumer")
    assert enters_during(event_store.degraded_graphs) == 1, (
        "`degraded_graphs` must SNAPSHOT under the lock; /health walks a dict "
        "that a peer thread resizes")
    assert enters_during(lambda: event_store.degraded(proj)) == 0, (
        "`degraded()` is DOCUMENTED lock-free (one atomic `dict.get` over a "
        "freshly published dict) — taking the lock here would put every "
        "`poll_events` behind an emitting thread's error path")
    assert enters_during(event_store.clear_degraded) == 1, (
        "`clear_degraded` mutates the map, so it must hold the lock")
    event_store.clear_degraded()


def test_a_leaked_degradation_is_attributed_to_the_test_that_left_it():
    """The isolation fixture's attribution half NAMES the polluter (#9208 P1).

    `tests/conftest.py`'s `_change_feed_state_isolation` clears before and after
    every test. The loud half is the AFTER one, and it is the half that can name
    a polluter: the same fixture's post-test clear runs first, so a pre-test check
    could never see the previous test's leak — the defect the review found in the
    old shape, where the message promised attribution it could not deliver.

    The decision is pinned here because a fixture's teardown body cannot be
    asserted from inside a test: this is the same seam-in-a-module pattern as
    `tests/_signal_hygiene.py` and `tests/_tmpdir_hygiene.py`.
    """
    from tests._change_feed_isolation import leaked_state_message

    assert leaked_state_message([], "tests/test_x.py::t_clean") is None, (
        "a test that leaves nothing behind must not be failed")

    message = leaked_state_message(
        [{"store": "file:/a.db", "graph": "tortoise", "stage": "append"},
         {"store": "file:/a.db", "graph": "tortoise", "stage": "emit"}],
        "tests/test_x.py::t_polluter")
    assert "tests/test_x.py::t_polluter" in message, (
        "the failure must name the test that LEFT the state, not the next one: "
        f"{message}")
    assert "tortoise" in message and "2 degraded" in message, (
        f"it must name the affected graph(s) and how much was left: {message}")
    assert "clear_degraded" in message, (
        "it must name the remedy the polluter owes, or the next reader "
        f"re-derives it: {message}")
    assert "leaked into this test" not in message, (
        "the old wording blamed the SUCCESSOR; the attribution must be the "
        f"polluter: {message}")
