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
(`PointRetracted` is a `:GraphEvent`-only subscriber row — #3300), and the door
`DETACH DELETE`d the node and appended the record afterwards. `_emit_event` is
deliberately best-effort (#4240) and never raises, so a failed append left the
node **gone from the cache and absent from the truth**: `rebuild_all` re-folds
the `PointAdded` line and the deleted Point comes back as live. Apply-first on a
delete RESURRECTS, which is a stronger harm than the lost write the same window
causes on a create.

This suite pins the fix — the append is the COMMIT POINT of the hard delete:

* the `EntityMutated` record is appended BEFORE the `DETACH DELETE` (T1);
* a failed append means the delete is NOT issued and the caller is told (T2);
* a committed record whose cache delete did not land is healed by a replay, not
  by a resurrect (T3);
* a delete of a non-existent id journals nothing (T4);
* one record, `op="delete"`, no `state`, and no JSONL `PointRetracted` (T5);
* the `:GraphEvent` subscriber row is unchanged for a completed delete (T6);
* the `None` outcome (no journal configured — the DEFAULT embedded shape) is not
  read as a refusal (T7);
* an append that LANDS and then raises is reported as UNCONFIRMED
  (`record_committed=False`, no cache write) and the landed record is applied by
  the next replay (T8);
* the evidence probe's own class-attribute patch is restored to the CLASS
  FUNCTION, so each section measures its OWN graph (T9);
* the message names THIS door's record (`EntityMutated op=delete`, derived from
  the door, never `PointAdded`) and attaches the graph to the CACHE clause, not
  the record's (T10).

⛔ SCOPE — what this suite does NOT claim. It does not claim the raw guarded
handle (`proj.g.query`) is closed, and it does not claim the remaining
apply-first doors are converted (`_delete_entity`, `_session_event_write`,
`_update_entity`, `create_operator` are named in `tortoise/sdk.py`'s module-top
KNOWN DEVIATIONS and in plan §7 increment 4). It pins the ORDERING on
`delete_point` and the replay-healability of a committed delete record whose
cache write did not land.

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


# ── T1: the record precedes the cache write (the defect) ──────────────────

def test_delete_point_appends_the_record_before_the_cache_delete(env,
                                                                monkeypatch):
    """§17.1's ORDER on the hard-delete door — journal, then cache.

    The spy records the two events in the order they happen: ``journal`` when
    the `EntityMutated` delete record is emitted, ``cache`` when the
    `DETACH DELETE` reaches the handle. The assertion is POSITIONAL, not a
    count, because a count of either event passes whether or not the order
    changed.

    MUTATION: move the `_journal_entity_mutation(..., "delete")` call back below
    the `DETACH DELETE` and this fails — ``order`` becomes ``["cache",
    "journal"]``, which is the #7951 defect this increment closes.
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
    assert order.index("journal") < order.index("cache"), (
        "delete_point DESTROYED the node before recording the destruction "
        f"(apply-first, the #7951 resurrection window): {order!r}")


# ── T2: a failed append must not delete the node ──────────────────────────

def test_a_failed_append_does_not_delete_the_point(env, monkeypatch):
    """Derived can never LEAD truth: no record ⇒ no destruction.

    The append's own per-call outcome (`_emit_event` → `False`) is the commit
    signal. If it fails, the `DETACH DELETE` must not be issued — otherwise the
    node is destroyed with no record and the next replay resurrects it while
    telling the caller the delete succeeded.

    MUTATION: drop the ``_delete_committed is False`` gate and the node is
    deleted with no record — `_live(...)` becomes False and `_mutations(...)`
    stays empty, i.e. exactly the pre-fix behaviour.
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, _events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "must survive a broken journal")["id"]

    log = sdk._get_event_log()
    assert log is not None, "the fixture must wire a journal for this arm"

    def _boom(_event):
        raise OSError("simulated journal append failure")

    monkeypatch.setattr(log, "append", _boom)

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.delete_point(pid)

    assert ei.value.operation == "delete_point", (
        "the refusal must name the door that raised it — a caller cannot tell "
        "two doors with one message apart (#7209)")
    assert ei.value.record_committed is False, (
        "the append failed, so nothing is committed and the delete must not "
        "have been issued")
    assert _live(proj, pid) is True, (
        "the Point was destroyed although the destruction was never recorded "
        "— the next replay would RESURRECT it (§17.1 apply-first window)")
    # The old assertion here was `_mutations(...) == []`, which the spy's own
    # raise guaranteed: it could never fail (review P2). The positive evidence
    # is `_live` above. What this arm must also do is NOT overclaim: a buffered
    # append can land the record and still raise, so the message says "did not
    # confirm" rather than "nothing was written" — pinned here, and the landed
    # case has its own test (T8).
    _msg = str(ei.value)
    assert "did not confirm" in _msg, (
        "the refusal must not assert that nothing was written — `False` only "
        "means the append raised, and an append can land and then raise")
    # #7951 review round 3 — the NEGATIVE half, which a positive substring check
    # cannot make: this arm shared `create_point`'s created-shaped text, whose
    # headline said "was not committed" and whose cause said "no rebuild record
    # was committed for it". Both are FALSE on a delete door, where `False` only
    # means the append did not confirm and the record may be durably on disk
    # (T8 pins exactly that state). The old positive assertion passed while the
    # message stated the opposite of the test's premise.
    assert "not committed" not in _msg, (
        "`False` does not mean the record failed to commit — a buffered append "
        f"can land it and then raise (T8): {_msg!r}")
    assert "no rebuild record was committed" not in _msg, (
        "the destruction record may be on disk; a delete-door refusal must not "
        f"assert it never landed: {_msg!r}")
    assert "was not applied" not in _msg, (
        "a blind re-issue appends a SECOND delete record for one intent, so "
        f"the message must not invite one as if nothing was recorded: {_msg!r}")


# ── T3: a committed record whose cache delete did not land ────────────────

def test_a_committed_delete_record_is_healed_by_replay(env, monkeypatch):
    """The residual window is the replay-healable one, not a resurrect.

    Force the `DETACH DELETE` to fail after the record committed: TRUTH says
    the Point is deleted and the cache is `extra_in_cache` (it holds a node the
    record no longer has — `tortoise/reconciliation.py`'s chokepoint class, NOT
    a lag; a lag is `missing_in_cache`). `rebuild_all` must then
    remove the node. Pre-fix the same crash left NO record,
    so the replay re-folded the `PointAdded` line and the Point stayed live
    forever; that is the assertion this test makes fail on the old ordering.

    MUTATION: journal after the delete and this fails at the second
    assertion — `rebuild_all` resurrects the Point instead of removing it.
    """
    sdk, events = env
    proj = sdk._get_proj()
    pid = sdk.create_point("observation", "deleted, cache lags")["id"]

    real_query = proj.g.query

    def failing_delete(_self, cypher, *args, **kwargs):
        if _DELETE_CYPHER in (cypher or ""):
            raise RuntimeError("simulated engine failure on the DETACH DELETE")
        return real_query(cypher, *args, **kwargs)

    monkeypatch.setattr(type(proj.g), "query", failing_delete)

    from tortoise.exceptions import WriteNotLandedError

    with pytest.raises(WriteNotLandedError) as ei:
        sdk.delete_point(pid)

    # #7951 review P1 — the COMMITTED arm is caller-facing. The record is on
    # disk before the cache write, so this failure means "destroyed in the
    # TRUTH, still served by the cache", never "the delete did not happen".
    # A bare engine error here would invite the caller to re-issue, which
    # would append a SECOND delete record for one intent.
    assert ei.value.record_committed is True, (
        "the caller must be told the destruction is committed — the record "
        "was appended BEFORE the cache delete")
    assert ei.value.operation == "delete_point"
    assert ei.value.record == "EntityMutated op=delete"

    monkeypatch.setattr(type(proj.g), "query", real_query)

    recs = _mutations(events, pid)
    assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")], (
        "the committed record must survive the failed cache write — it is the "
        f"COMMIT POINT of the delete: {recs!r}")
    assert _live(proj, pid) is True, "premise: the cache delete did not land"

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _live(proj, pid) is False, (
        "replay did NOT remove the Point the journal recorded as deleted — the "
        "record and its fold disagree, and a rebuild resurrects the deletion")


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

    MUTATION: change `is False` to `if not _delete_committed:` and this fails
    with a refusal instead of the True return.
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
    `EventLog.append` is a buffered ``open/write/close``: an append can put the
    record on disk and still raise. The delete is then refused (the ordering
    skips the cache write), so nothing is destroyed eagerly — but the record is
    durable, so the next replay removes the Point. Both halves are asserted
    here, because the door's message promises exactly that residual.

    MUTATION: make the door claim `record_committed=True` on this arm and the
    first assertion fails; drop the replay assertion and the "and a replay
    removes it" half of the message becomes an untested promise.
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
    assert _live(proj, pid) is True, (
        "the DETACH DELETE must not be issued on an unconfirmed append")

    monkeypatch.setattr(log, "append", real_append)

    recs = _mutations(events, pid)
    assert [(r["op"], r["label"]) for r in recs] == [("delete", "Point")], (
        "premise: the append DID land, which is why the message cannot assert "
        f"that nothing was written: {recs!r}")

    proj.rebuild_all(str(events), confirm_destructive=True)

    assert _live(proj, pid) is False, (
        "the landed record must be applied by the replay — that is the "
        "residual the refusal message promises")


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
