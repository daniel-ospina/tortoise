"""#7951 §17.1 — `delete_point` through the OUTBOX ordering.

`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0 (owner ruling, 2026-10-08) makes
**Postgres the system of record and FalkorDB a derived hot cache**; §17.1 states
the rule that makes a derived cache derived:

    > every write goes to the truth store first, and the cache is projected
    > from it.
    >
    > **A derived cache that is also written to directly is not derived.**

On a DELETE that rule is not merely about losing a write. `delete_point`'s
`EntityMutated` op=delete row is the ONLY durable carrier of the destruction
(`PointRetracted` is a `:GraphEvent`-only subscriber row — #3300), which is why
#7951 moved the append ABOVE the `DETACH DELETE` and #9207 moved it back BELOW.

The two windows are the two directions a derived cache can be wrong, and they
are not equally harmful:

* the record first (#7951) — a cache write that cannot be issued (a refused
  statement, an engine error, a kill) leaves the journal claiming a destruction
  the cache never performed. The cache SERVES a Point the truth says is gone,
  which §17.1's own definition forbids (the cache must never be AHEAD of the
  truth), and only a DESTRUCTIVE `rebuild_all` resolves it. ⛔ The trigger is
  NOT the §17.1 write door in a REFUSING state: that surface has three doors
  (#9124 part 1) and this statement is issued at the third one — the
  projection's write handle (`proj.g.query`) — which is DETECTED but not yet
  REFUSING (`_HANDLE_GUARD_ENFORCES` is `False` until #9124 part 2), so the
  probe in this directory prints `RAW HANDLE LANE (proj.g.query): SUCCEEDED`
  with the guard measuring. So the window is a generic engine/transport failure
  with the node still present: RARE. The ordering is justified by the DIRECTION
  of the divergence it leaves, not by its frequency. (⚠️ This door COMPLETES
  under an armed handle: #9206 declares its apply-first lane
  (`@_declares_apply_first_write_lane("delete_point")`) — #9207's decision put
  the `DETACH DELETE` first, so it holds no journal-first lane and is no longer
  inventoried in `_APPLY_FIRST_DOORS`. Pinned by
  `test_9079_handle_write_authority.py::
  test_a_declared_apply_first_door_writes_under_the_armed_guard` and by this
  directory's own probe, which still prints `RAW HANDLE LANE` for the raw-query
  residuals, NOT for the door.)
* the cache first (#9207) — a `DETACH DELETE` that fails or matches nothing
  appends NOTHING, so the record can never over-claim. The remaining window is
  the append failing after a landed destroy: the cache is ahead of the truth
  only in the sense that it dropped a node the truth still holds, which is the
  direction a replay REPAIRS — the truth wins, the Point is restored, and the
  caller (who is told, never silently served a success) re-issues.

#9207 REVERSED that ordering on this door, so the suite now pins #9207's:

* the `DETACH DELETE` is issued BEFORE the `EntityMutated` record, which is
  written only once the statement reports a landed destroy (T1);
* a cache write that did not land leaves NO destruction record — the journal can
  never claim a destruction the cache did not perform (T2);
* a landed destroy whose append failed is reported, never silent; its only
  repair is a destructive `rebuild_all`, which re-creates the Point (T3, T8);
* a raise does not prove "nothing was destroyed": the door READS the cache back,
  so a lost reply over an applied write is still recorded (T3b), and an
  unreadable state writes nothing and claims nothing (T3c);
* a delete of a non-existent id journals nothing (T4);
* one record, `op="delete"`, no `state`, and no JSONL `PointRetracted` (T5);
* the `:GraphEvent` subscriber row is unchanged for a completed delete (T6);
* the `None` outcome (no journal configured — the DEFAULT embedded shape) is not
  read as a refusal (T7);
* an append that LANDS and then raises is reported as UNCONFIRMED
  (`record_committed=False` — the record may durably exist) and the landed
  record is applied by the next replay (T8);
* the evidence probe's own class-attribute patch is restored to the CLASS
  FUNCTION, so each section measures its OWN graph (T9);
* a `graph was deleted or replaced` abort whose graph KEY is gone is NOT read
  back as a landed destroy — on this engine a Cypher read against a missing key
  AUTO-CREATES it and answers `count(n)=0`, so the read-back is gated on
  CONFIRMED presence and issues no read at all (T3e); the same gate is paid on the
  TRANSPORT family, where the abort classifier returns no arm at all and the
  first cut skipped the probe (T3f); the one arm that pays NEITHER probe nor
  read is the STATE-INDEPENDENT one (write slot / MISCONF), where nothing was
  applied by construction — reading there would auto-create an absent key and be
  taken as a landed destroy (T3g); a read-back that answers NO ROWS is not a
  verdict either — it says nothing about the cache, so it takes the same
  "claim nothing" arm instead of reporting a landed destroy (T3i);
* the message names THIS door's record (`EntityMutated op=delete`, derived from
  the door, never `PointAdded`) and attaches the graph to the CACHE clause, not
  the record's (T10).

⛔ SCOPE — what this suite does NOT claim. It does not claim the raw guarded
handle (`proj.g.query`) is closed, and it does not claim the remaining
apply-first doors are converted (`_delete_entity`, `_session_event_write`,
`_update_entity`, `create_operator` are named in `tortoise/sdk.py`'s module-top
KNOWN DEVIATIONS and in plan §7 increment 4). It pins the ORDERING on
`delete_point` and the replay-healability of a destroy whose record did not
confirm or was lost. It deliberately does NOT claim the mirror state any more:
since #9207 the append FOLLOWS the destroy, so a record whose cache write did
not land can no longer be produced by this door (a failed destroy records
nothing — T2).

Plan of record: `docs/plans/2026-10-18-7951-delete-point-outbox.md`.
"""
from __future__ import annotations

import importlib.util as _ilu
import inspect
from pathlib import Path

import pytest

from tortoise.log import EventLog

_DELETE_CYPHER = "DETACH DELETE n"

#: The evidence probe this suite's T9 pins (the evidence dir is hyphenated, so
#: it is loaded by path — the same spec-loader pattern
#: `tests/test_7950_capstone_probe.py` uses for its probe).
_PROBE_PATH = (Path(__file__).resolve().parent.parent / "docs" / "evidence"
               / "7951-delete-point-outbox" / "probes"
               / "delete_order_probe.py")


def _load_probe():
    spec = _ilu.spec_from_file_location("delete_order_probe_7951",
                                        str(_PROBE_PATH))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _count_on_raw_handle(graph, pid: str) -> int:
    """Count through the UNDERLYING handle, bypassing `_GuardedGraph.query`.

    A probe patches `type(graph).query` (a PROCESS-GLOBAL class attribute), so
    a count taken through `graph.query` cannot tell a correctly-restored class
    attribute from a bound method captured from another instance.
    """
    rows = graph._g.query("MATCH (n:Point {id:$id}) RETURN count(n)",
                          params={"id": pid}).result_set
    return int(rows[0][0]) if rows else 0


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired — the guard-arming shape."""
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "del-outbox.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        yield sdk, events
    finally:
        sdk.close()


def _journal(events) -> list[dict]:
    path = Path(events) / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _mutations(events, id_val: str) -> list[dict]:
    return [e for e in _journal(events)
            if e.get("type") == "EntityMutated" and e.get("id") == id_val]


def _count(proj, cypher: str, **params) -> int:
    rows = proj.g.query(cypher, params=params or None).result_set
    return int(rows[0][0]) if rows else 0


def _live(proj, pid: str) -> bool:
    return _count(proj, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid) > 0


# ── T1: the cache delete precedes the record (the #9207 order) ────────────

def test_the_cache_delete_lands_before_the_destruction_record(env, monkeypatch):
    """#9207's ORDER on the hard-delete door — cache first, then the record.

    The record's meaning is "the cache no longer holds this Point". A record
    written BEFORE the `DETACH DELETE` therefore *claims a cache state the
    cache may never reach* whenever the write cannot be issued — and #9207
    measured exactly that with §17.1's write door armed: the append committed,
    the delete was refused, and `get_point` still served the Point.

    The spy records the two events in the order they happen: ``journal`` when
    the `EntityMutated` delete record is emitted, ``cache`` when the
    `DETACH DELETE` reaches the handle. POSITIONAL, not counted: a count of
    either event passes whether or not the order changed.

    MUTATION: move the `_journal_entity_mutation(..., "delete")` call back
    ABOVE the `DETACH DELETE` and this fails — ``order`` becomes
    ``["journal", "cache"]``, which is the #7951 ordering #9207 reverses.
    """
    sdk, _events = env
    proj = sdk._get_proj()
    order: list[str] = []

    real_mutate = sdk._journal_entity_mutation

    def spy_mutate(label, id_val, op, **kwargs):
        if op == "delete":
            order.append("journal")
        return real_mutate(label, id_val, op, **kwargs)

    monkeypatch.setattr(sdk, "_journal_entity_mutation", spy_mutate)

    real_query = proj.g.query

    def spy_query(_self, cypher, *args, **kwargs):
        if _DELETE_CYPHER in (cypher or ""):
            order.append("cache")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", spy_query)

    pid = sdk.create_point("observation", "delete me, in order")["id"]
    assert sdk.delete_point(pid) is True

    assert order.count("journal") == 1, (
        f"exactly one EntityMutated delete record per hard delete: {order!r}")
    assert "cache" in order, (
        "the DETACH DELETE never reached the handle — the spy missed it, so "
        f"this test would be vacuous: {order!r}")
    assert order.index("cache") < order.index("journal"), (
        "delete_point recorded the destruction BEFORE the cache performed it "
        f"(the #9207 divergence: a record the cache cannot honour): {order!r}")


# ── T2: a cache write that did not land leaves NO destruction record ──────

def test_a_failed_cache_write_appends_no_destruction_record(env, monkeypatch):
    """THE #9207 INVARIANT: the record may only claim what the cache DID.

    The failure injected is a cache write that did not reach the engine and
    raised with the Point still present — the SHAPE #9207 cares about, injected
    here because the reachable trigger is a generic engine/transport failure:
    §17.1's third door (#9124 part 1) is this same raw handle (`proj.g.query`),
    and it DETECTS a mutating statement but does not yet REFUSE one
    (`_HANDLE_GUARD_ENFORCES` is `False`; part 2 arms it), so nothing raises
    `DirectCacheWriteRefused` here in the landed state — the lane's own probe
    measured `RAW HANDLE LANE (proj.g.query): SUCCEEDED` with the guard
    measuring. The reachable trigger is therefore rare; what the ordering buys
    is the DIRECTION of the divergence it leaves. Pre-fix (the #7951
    append-first order) the journal ALREADY held the `EntityMutated` op=delete
    record at that point while the cache still served the Point — a committed
    record claiming a cache state the cache does not have, whose only repair is
    a destructive `rebuild_all` that deletes a Point the caller never managed
    to delete.

    The LOSS is asserted, not the code path: no destruction record for this id,
    the node still live, and no `:GraphEvent` `PointRetracted` row published to
    the change feed.

    MUTATION: append the record above the `DETACH DELETE` (the pre-#9207 order)
    and every assertion below fails.
    """
    from tortoise.projection import DirectCacheWriteRefused

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "refused, so nothing is recorded")["id"]

    real_query = type(proj.g).query

    def failing_delete(_self, cypher, *args, **kwargs):
        if _DELETE_CYPHER in (cypher or ""):
            raise DirectCacheWriteRefused(
                "§17.1: this statement reached the cache without authorization")
        return real_query(_self, cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", failing_delete)

    with pytest.raises(DirectCacheWriteRefused):
        sdk.delete_point(pid)

    monkeypatch.setattr(type(proj.g), "query", real_query)

    assert _live(proj, pid) is True, (
        "premise: the cache delete did not land")
    assert _mutations(events, pid) == [], (
        "the journal recorded a destruction the cache never performed — the "
        "#9207 divergence, whose only repair is a destructive `rebuild_all`: "
        f"{_mutations(events, pid)!r}")
    assert not [e for e in sdk.poll_events(after=None)["events"]
                if e.get("type") == "PointRetracted"], (
        "a `:GraphEvent` subscriber row was published for a destruction that "
        "never happened")


# ── T3: a landed destroy whose append failed is reported, not silent ──────

def test_a_landed_destroy_whose_record_failed_is_reported(env, monkeypatch):
    """The one window #9207's order still has, and it is neither silent nor lost.

    The destroy lands and the journal append then fails. The state is the
    mirror of T2 (the cache dropped a node the truth still holds), so the door
    must SAY so — a silent success over an unrecorded destruction is the
    pre-#7951 defect — and it must resolve in the TRUTH's direction: the truth
    still holds the Point, and the only repair is a DESTRUCTIVE `rebuild_all`,
    which re-creates the Point from the journal and so UNDOES the deletion
    (after which the delete is re-issued). `recover_from_log` does not help: it
    rebuilds only an EMPTY graph, and this one has nodes. That is the trade
    #9207 makes, and this test is where it is pinned.

    MUTATION: return ``True`` from the door when the append does not confirm
    and the first assertion fails (a silent success over an unrecorded
    destruction); drop the replay assertion and the recovery half of the
    refusal's promise becomes untested.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "destroyed, record refused")["id"]

    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"
    real_append = log.append

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(log, "append", _boom)

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.delete_point(pid)

    assert ei.value.operation == "delete_point", (
        "the refusal must name the door that raised it (#7209)")
    assert ei.value.record_committed is False, (
        "the append raised, so nothing may claim the destruction is committed")
    _msg = str(ei.value)
    assert "did not confirm" in _msg, (
        "the refusal must report the record as UNCONFIRMED — an append can "
        "land and then raise, so it must not assert that nothing was written")
    assert "not committed" not in _msg, (
        "`False` does not mean the record failed to commit — a buffered append "
        f"can land it and then raise: {_msg!r}")
    assert "Nothing was destroyed in the cache" not in _msg, (
        "the destroy DID land before the append — the refusal must not claim "
        f"the cache still holds the Point: {_msg!r}")

    monkeypatch.setattr(log, "append", real_append)
    assert _mutations(events, pid) == [], (
        "premise: the append never landed, so no record exists")
    assert _live(proj, pid) is False, "premise: the destroy landed"

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _live(proj, pid) is True, (
        "the divergence did not resolve in the TRUTH's direction: the journal "
        "never recorded the destruction, so the rebuild RE-CREATES the Point "
        "(undoing the delete) and the caller then re-issues it")


# ── T3b: a raise does not prove "nothing was destroyed" (#9207 review P1) ──

def test_a_lost_reply_over_an_applied_destroy_is_still_recorded(env,
                                                               monkeypatch):
    """`_graph_write_with_retry` does not retry the TRANSPORT family, and that
    is the window a bare `raise` would open.

    `retryable_transient` deliberately excludes a timeout / dropped connection
    (`tortoise/retry.py`), because there the server may have APPLIED the write
    and lost the reply. So under #9207's order a `DETACH DELETE` can land, raise
    a transport error, and — without a read-back — leave the Point destroyed
    with NO record and no report: the silent unrecorded destroy, which is the
    pre-#7951 defect arriving by another road.

    The door therefore READS the cache before deciding what the failure meant.
    Here the statement applies and then raises, and the door must write the
    destruction record anyway (the cache is the thing it records).

    MUTATION: drop the `_delete_landed_anyway` read-back (a bare `raise` in the
    except, #9207's first cut of this diff) and this fails: the record is [] and
    the node is gone — the cache destroyed it and the journal never knew.
    """
    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "applied, reply lost")["id"]

    real_query = type(proj.g).query

    def land_then_lose(_self, cypher, *args, **kwargs):
        if _DELETE_CYPHER in (cypher or ""):
            real_query(_self, cypher, *args, **kwargs)
            raise ConnectionError("simulated lost reply over an applied write")
        return real_query(_self, cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", land_then_lose)
    try:
        assert sdk.delete_point(pid) is True, (
            "the destroy DID land (the read-back proves it), so the call is a "
            "completed delete, not a failure")
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)

    assert _live(proj, pid) is False, "premise: the statement applied"
    recs = _mutations(events, pid)
    assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")], (
        "the destruction landed and the journal never recorded it — a replay "
        f"would resurrect the Point silently: {recs!r}")


# ── T3d: the landed-destroy arm must be true on BOTH paths that reach it ──

def test_the_landed_destroy_arm_does_not_claim_the_statement_succeeded(env,
                                                                     monkeypatch):
    """A lost reply over an APPLIED destroy can ALSO fail to record.

    The reason this arm gives the caller must be true whichever way the destroy
    landed. T3 reaches it after the statement RETURNED; here the statement
    APPLIED and then raised a transport error — the door proves the destroy from
    the read-back, logs that the statement raised and the cache no longer holds
    the Point, and only the append then fails. So the arm's cause clause may not
    say "the `DETACH
    DELETE` did not report a failure of its own": on this path it did, and the
    caller would be looking for a fault that is already known not to matter.

    MUTATION: restore that clause in `tortoise/exceptions.py`'s delete door
    (`destroy_verified=True` arm) and this reddens while every other test stays
    green — the whole-diff review found exactly that.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "applied, reply lost, no record")["id"]
    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"

    real_query = type(proj.g).query

    def land_then_lose(_self, cypher, *args, **kwargs):
        if _DELETE_CYPHER in (cypher or ""):
            real_query(_self, cypher, *args, **kwargs)
            raise ConnectionError("simulated lost reply over an applied write")
        return real_query(_self, cypher, *args, **kwargs)

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(type(proj.g), "query", land_then_lose)
    monkeypatch.setattr(log, "append", _boom)
    try:
        with pytest.raises(WriteNotLandedError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)

    assert ei.value.record_committed is False
    assert ei.value.destroy_verified is True, (
        "the read-back PROVED the destroy landed, so this is the verified arm")
    msg = str(ei.value)
    assert "did not report a failure of its own" not in msg, (
        "the `DETACH DELETE` DID raise on this path — the clause is false here "
        f"even though it is true when the statement returns: {msg!r}")
    assert ".." not in msg, f"a doubled period in a caller-facing message: {msg!r}"
    assert _mutations(events, pid) == [], (
        "premise: the append never landed, so no record exists")
    assert _live(proj, pid) is False, "premise: the destroy landed"


# ── T3c: when the state cannot be read back, claim NOTHING ────────────────

def test_an_unreadable_after_state_writes_no_record_and_claims_nothing(
        env, monkeypatch):
    """The third branch: neither verdict is provable, so the journal stays empty.

    If the `DETACH DELETE` raises AND the read-back raises too, the door cannot
    tell whether the destroy landed. Writing the record could claim a
    destruction the cache still holds (the #9207 defect); writing nothing and
    saying nothing would be the silent unrecorded destroy. So it raises the
    door's own refusal with NO record, and the message must not assert either
    cache state.

    MUTATION: make `_delete_landed_anyway`'s exception arm return ``True`` (the
    door then appends a record for a destroy nobody observed) or ``False`` (it
    then asserts the node is still there, when it may not be). Both redden this.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "state unreadable")["id"]

    real_query = type(proj.g).query

    def everything_fails(_self, cypher, *args, **kwargs):
        stmt = cypher or ""
        # The door's own pre-write existence probe is `... RETURN count(n) > 0`
        # and must keep working: only the DELETE and the POST-FAILURE read-back
        # are made to fail.
        readback = "RETURN count(n)" in stmt and "> 0" not in stmt
        if _DELETE_CYPHER in stmt or readback:
            raise ConnectionError("simulated unreachable engine")
        return real_query(_self, cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", everything_fails)
    try:
        with pytest.raises(WriteNotLandedError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)

    assert ei.value.record_committed is False
    assert ei.value.operation == "delete_point"
    assert ei.value.destroy_verified is False, (
        "this arm must be distinguishable from the landed-destroy arm; without "
        "the flag the shared prose asserts a cache state nobody verified")
    msg = str(ei.value)
    assert "NOT VERIFIED" in msg or "UNVERIFIED" in msg, (
        "the refusal must say the state could not be verified, not that the "
        f"cache still holds the Point: {msg!r}")
    # The claim this arm CANNOT make. Both of these were in the message the
    # door raised here before the flag existed: the first is false because the
    # `DETACH DELETE` DID raise (that is how this arm is reached), the second
    # because nothing read the cache after it.
    assert "did not report a failure of its own" not in msg, (
        f"the `DETACH DELETE` DID raise on this arm: {msg!r}")
    assert "the cache no longer holds this Point" not in msg, (
        "nothing verified the cache after the failure, so the message must not "
        f"assert what it holds: {msg!r}")
    # The PRESENT branch may not say the cache is BEHIND the truth: this arm
    # writes no record, so a present node means the two stores agree. The ABSENT
    # branch names the direction CORRECTLY — cache destroyed with no record is
    # `missing_in_cache` (the record holds the Point, the cache does not), i.e.
    # the cache is BEHIND the TRUTH, not ahead of it.
    present_branch = msg.split("PRESENT")[-1].split("ABSENT")[0]
    # SCOPED to `present_branch` and CASED to the ARM under test: the ABSENT
    # branch is SUPPOSED to say `the cache is BEHIND the TRUTH` (uppercase, in
    # `tortoise/exceptions.py`), so an assertion over the whole `msg` would be
    # green on an inverted PRESENT branch that merely agrees in case with the
    # ABSENT one — and a lowercase needle would never match the uppercase
    # spelling the inverted wording would carry either.
    assert "behind the truth" not in present_branch.lower(), (
        "the first cut of this clause called a present node 'behind a TRUTH "
        f"that no longer records a destruction for it': {present_branch!r}")
    assert "re-issue" in present_branch.lower() or "RE-ISSUE" in present_branch, (
        f"the PRESENT branch must send the caller to a re-issue: {present_branch!r}")
    assert "rebuild_all" not in present_branch, (
        "a destructive rebuild is the ABSENT branch's repair, not this one: "
        f"{present_branch!r}")
    absent_branch = msg.split("ABSENT")[-1]
    assert "BEHIND the TRUTH" in absent_branch, (
        "a destroyed cache with no record is `missing_in_cache` — the cache is "
        f"BEHIND the truth, and the message must not invert it: {absent_branch!r}")
    assert "ahead" not in absent_branch.lower(), (
        f"the inverted direction came back: {absent_branch!r}")
    assert _mutations(events, pid) == [], (
        "an unverified cache state must never be recorded")
    assert _live(proj, pid) is True, (
        "premise: the injected failure never reached the engine")


# ── T3e: a deleted graph key is never read back as "landed" ────────────────

def test_a_deleted_graph_key_is_never_read_as_a_landed_destroy(env, monkeypatch):
    """A `graph was deleted or replaced` abort must not be swallowed by the
    read-back.

    MEASURED engine fact: a Cypher read against a graph whose KEY is gone
    AUTO-CREATES an empty graph (the repo pins the same behaviour in
    `tests/test_graph_write_repair.py`), so the read-back would answer
    `count(n) = 0` — indistinguishable from "the Point was destroyed" — while its
    real effect was to re-materialise the deleted key. The door would then write
    a destruction record and return True over a graph that no longer exists.

    So the read is gated on CONFIRMED presence: with `EXISTS` answering 0 no
    record is written and NO read is issued (asserted — the read is what would
    re-create the key), and the engine's own error surfaces. That is the loud
    #7685 surface, which tells the operator the key is absent — not a delete that
    returned True over a graph that no longer exists.

    MUTATION: drop the `_graph_presence` gate (or treat "absent" as present) and
    the read-back RUNS — asserted here, and the assertion is what reddens: this
    fixture leaves the Point in the graph, so the read finds it and the door
    raises the loud error instead of writing a record. (On a real v6 server the
    same read answers 0 and the door RETURNS True with a written record — that is
    the defect this gate exists to prevent, measured on a live engine when the
    gate was absent.)
    """
    import redis.exceptions as redis_exc

    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "graph deleted mid-delete")["id"]

    real_query = type(proj.g).query
    real_exec = type(proj.g).execute_command
    reads_after_abort: list[str] = []
    aborted = {"v": False}

    def abort_then_read(_self, cypher, *args, **kwargs):
        stmt = cypher or ""
        if _DELETE_CYPHER in stmt:
            aborted["v"] = True
            raise redis_exc.ResponseError(
                "graph was deleted or replaced while the query was running, "
                "aborting")
        if aborted["v"] and "RETURN count(n)" in stmt and "> 0" not in stmt:
            reads_after_abort.append(stmt)
        return real_query(_self, cypher, *args, **kwargs)

    def key_is_gone(_self, *args, **kwargs):
        if args and args[0] == "EXISTS":
            return 0
        return real_exec(_self, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", abort_then_read)
    monkeypatch.setattr(type(proj.g), "execute_command", key_is_gone)
    try:
        with pytest.raises(redis_exc.ResponseError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)
        monkeypatch.setattr(type(proj.g), "execute_command", real_exec)

    assert reads_after_abort == [], (
        "the read-back ran against a graph whose key is gone — on this engine "
        "that AUTO-CREATES the deleted graph, which is the silent-success path "
        f"#9207 must not have: {reads_after_abort!r}")
    assert not isinstance(ei.value, WriteNotLandedError), (
        "an absent key is the graph-abort family's loud surface, not this "
        "door's unverified arm")
    assert not [r for r in _journal(events)
                if r.get("op") == "delete" or r.get("type") == "EntityMutated"], (
        "no destruction record may be written when the cache state is unknown")


# ── T3f: the presence gate is paid on the TRANSPORT family too ────────────

def test_a_transport_failure_pays_the_presence_probe_too(env, monkeypatch):
    """A NON-family failure must not skip the gate either (#9207 review).

    T3e pins the graph-abort family. The gate that shipped FIRST keyed on
    `graph_abort_family(exc)`, and `_abort_arm` returns ``None`` for anything
    that is not a `redis.exceptions.ResponseError` — so a transport failure
    (timeout, dropped connection: the family for which the read-back exists at
    all) skipped the presence probe and issued the read against whatever key
    state the server was in. On an ABSENT key that read AUTO-CREATES the empty
    graph and answers `count(n) = 0`, which the door reads as "landed": it
    would write a destruction record and RETURN True over a graph that no
    longer exists.

    The gate now covers EVERY family except the state-independent one
    (write-slot / MISCONF, which #7405's
    `test_a_write_slot_exhaustion_pays_no_graph_presence_probe` keeps probe-free).

    MUTATION: restore `graph_abort_family(exc)` as the gate condition (or drop
    the probe for non-ResponseError raises) and this reddens: the read-back runs
    — asserted here — and, on this fixture's live key, the door raises the loud
    error instead of the engine's own; on a real v6 server the same read answers
    0 and the door RETURNS True with a written record.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "transport failure mid-delete")["id"]

    real_query = type(proj.g).query
    real_exec = type(proj.g).execute_command
    reads_after_failure: list[str] = []
    failed = {"v": False}
    exists_calls: list[object] = []

    def fail_then_read(_self, cypher, *args, **kwargs):
        stmt = cypher or ""
        if _DELETE_CYPHER in stmt:
            failed["v"] = True
            raise ConnectionError("connection reset by peer")
        if failed["v"] and "RETURN count(n)" in stmt and "> 0" not in stmt:
            reads_after_failure.append(stmt)
        return real_query(_self, cypher, *args, **kwargs)

    def key_is_gone(_self, *args, **kwargs):
        if args and args[0] == "EXISTS":
            exists_calls.append(args)
            return 0
        return real_exec(_self, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", fail_then_read)
    monkeypatch.setattr(type(proj.g), "execute_command", key_is_gone)
    try:
        with pytest.raises(ConnectionError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)
        monkeypatch.setattr(type(proj.g), "execute_command", real_exec)

    assert exists_calls, (
        "the presence probe was SKIPPED for a transport failure — the read-back "
        "below then runs against an unseen key state, and on an absent key that "
        "read re-creates the graph and reports a landed destroy")
    assert reads_after_failure == [], (
        "the read-back ran without a confirmed key on a NON-family failure: "
        f"{reads_after_failure!r}")
    assert not isinstance(ei.value, WriteNotLandedError), (
        "an absent key is the engine's own loud error, not this door's "
        "unverified arm")
    assert not [r for r in _journal(events)
                if r.get("op") == "delete" or r.get("type") == "EntityMutated"], (
        "no destruction record may be written for a destroy nobody verified")
    assert _live(proj, pid) is True, (
        "premise: the injected transport failure never reached the engine")


# ── T3g: the STATE-INDEPENDENT arm pays neither probe nor read ────────────

def test_a_state_independent_refusal_is_never_read_as_a_landed_destroy(
        env, monkeypatch):
    """The write-slot / MISCONF arm needs NO read-back, and must not read (#9207).

    `tortoise/retry.py` records what this arm means: the write-slot refusal is
    raised from `execute_query_write` *before the slot is claimed*, and
    ``MISCONF`` is a pre-execution command rejection. Nothing was applied, by
    construction — so `landed` cannot legitimately be ``True`` here and a read
    can only ADD a wrong answer, never information: the read is the statement
    that AUTO-CREATES an absent key, and `#7405`'s retry loop re-issues this arm
    WITHOUT consulting `graph_exists` (only the graph-abort family is
    state-gated), so a rebuild that drops the key during the 2–8 s backoff is
    not excluded. The read then answers `count(n)=0` and the door writes an
    `EntityMutated op=delete` record and returns True for a statement the engine
    REFUSED — the silent success the presence gate exists to prevent, left open
    on the one arm with no probe.

    MUTATION: fall through to the presence/read-back block on this arm (the
    pre-fix shape, `if _abort_arm(exc) != _ARM_STATE_INDEPENDENT: …probe…` then
    an unconditional read). The read-back runs against this fixture's
    swapped-graph answer `count(n)=0`, so the door RETURNS True with a written
    record instead of raising, and `readbacks` is non-empty — both assertions
    redden.
    """
    import types

    import redis.exceptions as redis_exc

    # The retry loop sleeps 2 s + 4 s on its way to exhaustion; the assertion is
    # about what the error path does, not how long it waits.
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "write slot contention")["id"]

    real_query = type(proj.g).query
    real_exec = type(proj.g).execute_command
    readbacks: list[str] = []
    exists_calls: list[object] = []

    def slot_abort_then_zero(_self, cypher, *args, **kwargs):
        stmt = cypher or ""
        if _DELETE_CYPHER in stmt:
            raise redis_exc.ResponseError(
                "another write is in progress, retry the query")
        if "RETURN count(n)" in stmt and "> 0" not in stmt:
            # A rebuild that dropped the key during the backoff: the read would
            # answer 0 (and on this engine, re-create the key doing it).
            readbacks.append(stmt)
            return types.SimpleNamespace(result_set=[[0]])
        return real_query(_self, cypher, *args, **kwargs)

    def count_exists(_self, *args, **kwargs):
        if args and args[0] == "EXISTS":
            exists_calls.append(args)
        return real_exec(_self, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", slot_abort_then_zero)
    monkeypatch.setattr(type(proj.g), "execute_command", count_exists)
    try:
        with pytest.raises(redis_exc.ResponseError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)
        monkeypatch.setattr(type(proj.g), "execute_command", real_exec)

    assert str(ei.value).startswith("another write is in progress"), (
        "a state-independent refusal is returned to the caller as the engine's "
        f"own error — not swallowed as a success: {ei.value!r}")
    assert readbacks == [], (
        "the read-back ran on an arm where nothing was applied by "
        f"construction: {readbacks!r}")
    assert exists_calls == [], (
        "#7405's contract: a state-independent exhaustion pays no EXISTS probe")
    assert _mutations(events, pid) == [], (
        "a statement the engine REFUSED must never produce a destruction record")
    assert _live(proj, pid) is True, (
        "premise: the injected slot abort never reached the engine")


# ── T3i: an EMPTY read-back is not a verdict ─────────────────────────────

def test_an_empty_read_back_claims_nothing(env, monkeypatch):
    """"No rows" from the read-back must not be read as "the Point is gone".

    `_delete_landed_anyway` decides whether a destruction record may be written,
    and the one answer it must never produce without a read is ``True``. An
    aggregate `RETURN count(n)` always yields one row on this engine, so this is
    a DEFENSIVE branch — but it stops being defensive the moment the read-back
    is restated (a `WHERE`, a `ro_query` path, a proxy or a transport that hands
    back a truncated result set), and then an empty `result_set` read as
    `not 0` → ``True`` would append `EntityMutated op=delete` for a Point no read
    showed absent: the #9207 defect mirrored, and unrecoverable in this
    direction (the repair is a DESTRUCTIVE `rebuild_all`).

    So an empty read-back is the UNVERIFIED arm: no record, and the refusal
    claims neither store. `tortoise.sdk._sync_witness` treats the same empty-rows
    case as "not landed" rather than raising it to a verdict.

    MUTATION: restore `return not (int(rows[0][0]) if rows else 0)` in
    `_delete_landed_anyway` and this reddens on the first assertion — the door
    returns True and writes a destruction record for a destroy nobody read.
    """
    import types

    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "empty read-back")["id"]

    real_query = type(proj.g).query
    readbacks: list[str] = []
    failed = {"v": False}

    def fail_then_read_empty(_self, cypher, *args, **kwargs):
        stmt = cypher or ""
        if _DELETE_CYPHER in stmt:
            failed["v"] = True
            raise ConnectionError("connection reset by peer")
        if failed["v"] and "RETURN count(n)" in stmt and "> 0" not in stmt:
            readbacks.append(stmt)
            return types.SimpleNamespace(result_set=[])
        return real_query(_self, cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", fail_then_read_empty)
    try:
        with pytest.raises(WriteNotLandedError) as ei:
            sdk.delete_point(pid)
    finally:
        monkeypatch.setattr(type(proj.g), "query", real_query)

    assert readbacks, "premise: the read-back was issued (key confirmed present)"
    assert ei.value.destroy_verified is False, (
        "no row was read, so the destroy is UNVERIFIED — the door must not claim "
        "a cache state it did not read")
    assert ei.value.record_committed is False
    assert _mutations(events, pid) == [], (
        "an empty read-back established nothing, so no destruction record may be "
        "written — the #9207 defect in the other direction")
    assert _live(proj, pid) is True, (
        "premise: the injected transport failure never reached the engine")


# ── T4: nothing is journaled for a delete that cannot happen ──────────────

def test_a_missing_point_journals_nothing(env):
    """The existence probe still runs FIRST: no node ⇒ no record.

    Journal-first must not mean "journal a delete that never happened": the
    door returns False for an unknown id, exactly as before.

    MUTATION: hoist the append above the existence probe and this fails — a
    `delete` record is written for an id the graph never held.
    """
    sdk, events = env
    pid = "p_does_not_exist_7951"

    assert sdk.delete_point(pid) is False
    assert _mutations(events, pid) == [], (
        "a no-op delete journalled a destruction — a replay would then delete "
        "a node this call never touched")


# ── T5: one record, the honest shape ─────────────────────────────────────

def test_the_delete_record_keeps_its_declared_shape(env):
    """Exactly one `EntityMutated` op=delete, no `state`, JSONL-only.

    The appended position changed; the RECORD must not (a shape change would be
    a replay-contract change smuggled in with an ordering fix).

    MUTATION: attach a `state` to the delete record (or restore a non-delete
    `record` default) — the shape assertion is what holds the replay contract.
    """
    sdk, events = env
    pid = sdk.create_point("observation", "one honest record")["id"]
    sdk.delete_point(pid)

    recs = _mutations(events, pid)
    assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")]
    assert "state" not in recs[0], \
        "#3299's recorded shape carries nothing for a delete"
    assert not [e for e in _journal(events)
                if e.get("type") == "PointRetracted"], (
        "PointRetracted must NOT reach the JSONL — its fold tombstones, which "
        "is the wrong meaning for a hard delete (#3300)")


# ── T6: the subscriber surface is unchanged for a completed delete ────────

def test_the_graph_event_row_is_still_written_after_the_delete(env):
    """`PointRetracted` stays a `:GraphEvent` row, and only for a real delete.

    It is the store's statement of what the call DID (hence the unconditional
    `deleted: true` marker), so it must still be published for a completed
    delete — and (T2) NOT published for a refused one.

    MUTATION: return from the door before the `:GraphEvent` publication, or
    publish it on the refused path too — either reddens this row.
    """
    sdk, _events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "subscribers still see this")["id"]
    sdk.delete_point(pid)

    polled = sdk.poll_events(after=None)["events"]
    assert any(e["type"] == "PointRetracted"
               and e["payload"] == {"id": pid, "deleted": True}
               for e in polled), (
        "the :GraphEvent subscriber row vanished — the ordering fix must not "
        f"change what subscribers see: {[e['type'] for e in polled]!r}")
    assert _live(proj, pid) is False, "premise: the delete landed"


# ── T7: the journal-LESS arm (the default embedded/dev shape) ─────────────

def test_a_journal_less_sdk_still_deletes(tmp_path):
    """`None` must not be read as a refusal.

    `_journal_entity_mutation` returns ``None`` when there is nothing to log or
    no journal is configured. The door's gate is ``is False`` — an identity
    test, not truthiness — because a journal-less SDK is the DEFAULT embedded
    shape: a truthiness bug would refuse every delete on every such SDK, which
    no other test in this file covers (verify P2).

    MUTATION: change the door's `... is False` gate to a truthiness test
    (`if not self._journal_entity_mutation(...)`) and this fails with a refusal
    instead of the True return.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "no-journal.db"))
    try:
        pid = sdk.create_point("observation", "deleted with no journal")["id"]
        assert sdk.delete_point(pid) is True, (
            "a journal-less SDK must delete, not refuse — there is no record "
            "to fail to commit")
        assert _live(sdk._get_proj(), pid) is False
    finally:
        sdk.close()



# ── T8: the append LANDS and then raises ──────────────────────────────────

def test_an_append_that_lands_then_raises_is_reported_as_unconfirmed(
        env, monkeypatch):
    """`False` covers a landed-but-unconfirmed append, and the door says so.

    `_emit_event` returns ``False`` for ANY exception from ``log.append``, and
    `EventLog.append` can put the record on disk and still raise (a partial
    `write(2)` stopped by ENOSPC/EIO, or a write whose close failed — #5612
    removed the buffered flush that used to make this the common case). Under
    #9207's order the destroy has already landed when this is raised, so the
    refusal is `record_committed=False` — the record is UNCONFIRMED, not
    known-absent — and the TRUTH may or may not hold the destruction. Here it
    DID land, so the next replay applies it and the Point stays deleted. Both
    halves are asserted, because the door's message promises exactly that.

    MUTATION: make the door claim `record_committed=True` on this arm, or claim
    the cache still holds the Point, and the assertions below fail.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "append lands then raises")["id"]

    log = sdk._get_event_log()
    assert log is not None
    real_append = log.append

    def _land_then_raise(event):
        real_append(event)
        raise OSError("simulated post-write journal failure")

    monkeypatch.setattr(log, "append", _land_then_raise)

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.delete_point(pid)

    assert ei.value.record_committed is False, (
        "the door cannot confirm the append, so it must not claim the record "
        "is committed — but it also must not claim nothing was written")
    assert ei.value.operation == "delete_point"
    assert _live(proj, pid) is False, (
        "premise of #9207's order: the destroy landed BEFORE the append, so "
        "the refusal must not claim the cache still holds the Point")

    monkeypatch.setattr(log, "append", real_append)

    recs = _mutations(events, pid)
    assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")], (
        "premise: the append DID land, which is why the message cannot assert "
        f"that nothing was written: {recs!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _live(proj, pid) is False, (
        "the landed record must be applied by the replay — the deletion IS "
        "committed, and the refusal's other half (replay resolves this arm) "
        "must hold")


# ── T9: the evidence probe measures each SDK's OWN graph ──────────────────

def test_the_evidence_probe_rebinds_the_guarded_handle_per_instance(tmp_path):
    """The probe's class-level spy must be restored to the CLASS FUNCTION.

    `_GuardedGraph` defines `query` on the CLASS (`__slots__ = ("_g", "_proj")`
    forbids an instance attribute), so the probe HAS to swap a process-global
    class attribute to instrument the door. The pre-fix probe restored it with
    `type(proj.g).query = real_query`, where `real_query` was `proj.g.query` —
    a BOUND method. A bound method assigned to a class is NOT re-bound: it is
    returned as-is for every instance, so every SDK constructed afterwards
    dispatched all its statements to SDK #1's graph. The probe's verdicts still
    looked right (its creates and its reads were misrouted CONSISTENTLY) while
    section 3's `rebuild_all` destructively wiped section 1's graph — i.e. the
    probe was not measuring what its own sections claim.

    MUTATION: change `_patch_query` to capture `graph.query` (the bound method)
    and this fails — SDK #2's Point lands in SDK #1's graph and these assertions
    see it there / missing from #2's own handle.
    """
    from tortoise.sdk import TortoiseSDK

    probe = _load_probe()
    a = TortoiseSDK(str(tmp_path / "a.db"))
    b = TortoiseSDK(str(tmp_path / "b.db"))
    try:
        ga, gb = a._get_proj().g, b._get_proj().g
        assert type(ga) is type(gb), "premise: one guarded-handle class"
        original = type(ga).query

        seen: list[str] = []
        _orig, restore = probe._patch_query(
            ga, lambda real: probe._record_deletes(real, seen))
        try:
            doomed = a.create_point("observation", "deleted under the spy")["id"]
            a.delete_point(doomed)
        finally:
            restore()

        assert seen == ["cache-DETACH-DELETE"], (
            f"premise: the spy watched the delete through the class attribute: "
            f"{seen!r}")
        assert not inspect.ismethod(type(ga).query), (
            "the probe restored a BOUND method onto the guarded-handle CLASS — "
            "every SDK created afterwards will address THAT instance's graph")
        assert type(ga).query is original, (
            "the class attribute must be the captured CLASS FUNCTION again")

        # Behavioural half: SDK #2's write must land in SDK #2's graph. Read
        # through the UNDERLYING handle, so a misrouted class attribute cannot
        # mask the misroute by misrouting the read the same way.
        pid_b = b.create_point("observation", "belongs to graph b")["id"]
        assert _count_on_raw_handle(gb, pid_b) == 1, (
            "SDK #2's Point did not land in its own graph — the patched class "
            "attribute is still redirecting it")
        assert _count_on_raw_handle(ga, pid_b) == 0, (
            "SDK #2's Point landed in SDK #1's graph — the restore kept a "
            "BOUND method on the class")
    finally:
        a.close()
        b.close()


# ── T10: the message names THIS door's record, and the graph is the cache's ─

def test_the_message_names_the_doors_own_record_and_graph():
    """`record` is derived from the door; the graph belongs to the cache clause.

    #7951 review round 4. A caller that omits `record` used to get "The
    PointAdded record WAS appended before the DETACH DELETE" — a record this
    door never writes — and the unconfirmed arm said "the PointAdded record's
    landing is UNCONFIRMED". Separately, `_where` (the CACHE graph) was
    interpolated into the RECORD clause, so the sentence read "DESTROYED in the
    record on graph X and not in the cache", pointing a debugging caller at the
    store that does NOT hold the destruction.

    MUTATION: restore `record: str = "PointAdded"` and the first two assertions
    of each block fail; move `_where` back into the record clause and the
    placement assertion fails.
    """
    from tortoise.exceptions import WriteNotLandedError

    committed_msg = str(WriteNotLandedError(
        "p_1", "cache_graph", record_committed=True, operation="delete_point"))
    assert "EntityMutated op=delete" in committed_msg
    assert "PointAdded" not in committed_msg
    assert "in the record and not in the cache on graph 'cache_graph'" in committed_msg, (
        "the graph name IS the cache graph; attaching it to the record clause "
        "sends a caller debugging a divergence to the wrong store")

    unconfirmed_msg = str(WriteNotLandedError(
        "p_1", "cache_graph", operation="delete_point"))
    assert "EntityMutated op=delete" in unconfirmed_msg
    assert "PointAdded" not in unconfirmed_msg

    # The two flags describe opposite histories (a record that PRECEDED the
    # write vs a read-back that PROVED the write landed first), so the pair must
    # never render: the message it produced asserted "DESTROYED in the record …
    # no read-back is involved on this arm" — self-refuting.
    with pytest.raises(ValueError, match="contradicts"):
        WriteNotLandedError("p_1", "cache_graph", record_committed=True,
                            operation="delete_point", destroy_verified=True)

    # The same defect, one door over: `update_point` writes `PointRevised`
    # (#7951 increment 2), so a caller that omits `record` must not be told a
    # `PointAdded` record is involved. No producer passes `record_committed=`
    # for that door today, which is why this is asserted here (constructing the
    # exception) rather than exercised through the SDK.
    revised_msg = str(WriteNotLandedError(
        "p_1", "cache_graph", record_committed=True, operation="update_point"))
    assert "PointRevised" in revised_msg, revised_msg
    assert "PointAdded" not in revised_msg, (
        "update_point never writes a PointAdded record: " + revised_msg)


# ── T11: the probe's exit code is never narrower than its verdict ──────────

@pytest.mark.parametrize(
    "reported, recorded, live, restored, expected_ok",
    [
        # the ONE passing shape: unconfirmed caller, no record on disk, the
        # destroy landed, and the replay restored the Point from the truth.
        (True, 0, 0, 1, True),
        # each term alone flipped — every one must fail the verdict AND the exit
        (False, 0, 0, 1, False),   # the caller was not told
        (True, 1, 0, 1, False),    # the append landed after all
        (True, 0, 1, 1, False),    # the destroy did not land
        (True, 0, 0, 0, False),    # the rebuild did NOT restore the Point
        (True, 0, 2, 0, False),    # several terms wrong at once
    ],
)
def test_the_probe_exit_code_cannot_pass_a_verdict_it_prints_as_lost(
        reported, recorded, live, restored, expected_ok):
    """A printed `COMMIT VERDICT: LOST` must not be able to exit 0.

    #9236 review (P3). Section 3's printed sentence is computed from FOUR terms
    (`reported and recorded == 0 and live == 0 and restored == 1`), but the
    process exit code was computed from three of them
    (`cache_first and no_record_ok and reported`). So `restored != 1` — the
    rebuild did NOT bring the Point back, i.e. the truth never held the destroy
    the door reported, which falsifies the trade this ordering makes — printed
    `COMMIT VERDICT: LOST` and still exited 0. The README and the PR body cite
    "probe exit 0" as the evidence for exactly that section, so a `LOST` verdict
    would have been quoted as a pass.

    The fix is by construction, not by a second predicate: the sentence and the
    exit term come from ONE call (`_section3_verdict`), and `main()` returns
    that same object's `ok`. This test pins the coupling — a subset predicate
    (the pre-fix `reported`-only shape) reds the `restored=0`, `recorded=1` and
    `live=1` rows.

    MUTATION: recompute `ok` from any subset of the four terms and the rows
    whose flipped term is outside that subset fail; recompute the returned
    sentence independently of `ok` and the sentence assertion fails.
    """
    probe = _load_probe()
    sentence, ok = probe._section3_verdict(reported, recorded, live, restored)
    assert ok is expected_ok, (
        f"verdict {ok!r} disagrees with the printed sentence's terms "
        f"(reported={reported}, recorded={recorded}, live={live}, "
        f"restored={restored})")
    assert (sentence == probe._SECTION3_OK) is expected_ok, (
        "the sentence printed and the term the exit code uses must be the same "
        f"decision, got sentence={sentence!r} for ok={ok!r}")
