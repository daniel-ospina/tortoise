#!/usr/bin/env python3
"""#7951 increment 4a — the OUTBOX ordering on `delete_point`, demonstrated.

Runs on the EMBEDDED lane (no server needed):

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
      .venv/bin/python docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py

It demonstrates, against the shipped modules:

  1. `delete_point` issues its `DETACH DELETE` BEFORE it appends the
     `EntityMutated` op=delete record (#9207), so the record cannot claim a
     destruction the cache still holds. #7951 had this the other way round; the
     probe now measures #9207's order and prints which one ran.
  2. A cache write that RAISES appends NO record: the node is still there, the
     journal is untouched, and the caller gets the engine's own error.
     Pre-#9207 the journal already held the destruction at that point — the
     divergence #9207 measured. ⛔ That trigger is NOT a systematic pre-write
     refusal, and the reason is the GUARD'S ARMING STATE: §17.1's write surface
     has three doors (#9124 part 1) and this statement is issued at the THIRD
     one — the projection's write handle, `proj.g.query`. It is DETECTED there
     (a mutating statement with no declared lane is counted and logged), but its
     REFUSAL is not armed (`_HANDLE_GUARD_ENFORCES` is `False` in part 1), so
     nothing refuses the statement today — which is why section 4 below (the
     residuals) prints `RAW HANDLE LANE (proj.g.query): SUCCEEDED` with that
     door measuring. What is reachable is a generic engine/transport failure
     with the node still present: RARE, and why the ordering is argued from the
     DIRECTION of the divergence rather than its frequency. (⚠️ This door
     COMPLETES under an armed handle: #9206 declares its apply-first lane
     (`@_declares_apply_first_write_lane("delete_point")`) — #9207's decision
     put the `DETACH DELETE` first, so the door holds no journal-first lane and
     is NOT inventoried in `_APPLY_FIRST_DOORS` any more. The
     `RAW HANDLE LANE` lines section 4 prints below are the RAW-QUERY
     residuals, never this door.) (A ZERO-ROW write is
     different, and IS recorded: the node is absent either way, so the record is
     true of the cache — see the door's own comment and
     `tests/test_7405_remaining_writes.py`.)
  3. A destroy that LANDED and then failed to record is reported, never silent
     (`WriteNotLandedError`, `record_committed=False` since the append raised).
     It does not heal by itself: `recover_from_log` rebuilds only an EMPTY
     graph, so the repair is a DESTRUCTIVE `rebuild_all`, which re-creates the
     Point from the journal (undoing the delete) — the caller then re-issues.
  4. The RECORDED RESIDUALS are shown, not hidden: the write handle
     (`proj.g.query`) is DETECTED but not yet REFUSING (#9124 part 1 —
     measure mode, `_HANDLE_GUARD_ENFORCES` is `False`), and `delete_entity`
     (the generic entity delete) holds the apply-first DECLARATION since #9206
     (so it completes under an armed handle) — both recorded on #7951
     and in plan §7 increment 4.

Instrumenting the door means swapping `query` on the GUARDED GRAPH CLASS
(`_GuardedGraph` keeps its state in `__slots__` and defines `query` on the
class, so no instance attribute can shadow it) — see `_patch_query` below for
why the RESTORE half of that swap is the part that has to be right.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# The cache statement the probe watches for.
_DELETE_STMT = "DETACH DELETE n"


def _journal(events: Path) -> list[dict]:
    from tortoise.log import EventLog

    path = events / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _deletes(events: Path, pid: str) -> list[dict]:
    return [e for e in _journal(events)
            if e.get("type") == "EntityMutated"
            and e.get("id") == pid and e.get("op") == "delete"]


def _patch_query(graph, make_spy):
    """Swap ``query`` on the GUARDED GRAPH CLASS and return ``(orig, restore)``.

    ``_GuardedGraph`` defines ``query`` on the CLASS (``__slots__ = ("_g",
    "_proj")`` — no instance attribute can shadow it), so instrumenting one
    projection means swapping a PROCESS-GLOBAL class attribute. Which makes the
    restore half the part that has to be right:

    * ``orig = cls.query`` captures the plain CLASS FUNCTION, and ``restore()``
      puts THAT back. Capturing ``graph.query`` instead yields a BOUND method;
      assigning a bound method to the class does not re-bind it, it is returned
      as-is for EVERY instance — so every SDK constructed later in this process
      would silently dispatch its reads and writes to the graph captured here.
      That is a false measurement, not a crash: the probe's own sections 2-4
      would create their Points in section 1's graph and `rebuild_all` in
      section 3 would wipe it.
    * the spy is called with the instance as its first argument (it is a plain
      function on the class), and calls the captured class function explicitly
      rather than recursing through the patched attribute.
    """
    cls = type(graph)
    orig = cls.query
    cls.query = make_spy(orig)

    def restore() -> None:
        cls.query = orig

    return orig, restore


def _record_deletes(orig, order: list[str]):
    """Spy that records every ``DETACH DELETE`` issued through the handle."""

    def spy(_self, cypher, *args, **kwargs):
        if _DELETE_STMT in (cypher or ""):
            order.append("cache-DETACH-DELETE")
        return orig(_self, cypher, *args, **kwargs)

    return spy


def _fail_deletes(orig):
    """Spy that refuses every ``DETACH DELETE`` with an engine-style error."""

    def spy(_self, cypher, *args, **kwargs):
        if _DELETE_STMT in (cypher or ""):
            raise RuntimeError("simulated engine failure (probe)")
        return orig(_self, cypher, *args, **kwargs)

    return spy


def _count(proj, cypher: str, **params) -> int:
    rows = proj.g.query(cypher, params=params or None).result_set
    return int(rows[0][0]) if rows else 0


def _live(proj, pid: str) -> int:
    return _count(proj, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid)


#: The sentence section 3 prints when the destroy is recovered only by a
#: destructive rebuild — the shape the README and the PR body cite.
_SECTION3_OK = "REPORTED, REPAIRED ONLY BY A DESTRUCTIVE REBUILD"


def _section3_verdict(reported: bool, recorded: int, live: int,
                     restored: int) -> tuple[str, bool]:
    """The section-3 sentence AND the term the process exit code uses — ONE call.

    ⚠️ The exit code must never be NARROWER than the sentence printed above it.
    The README and the PR body cite "probe exit 0" as this section's evidence,
    so an exit code computed from a SUBSET of these four terms would let a
    printed `COMMIT VERDICT: LOST` exit 0 — a claim the artifact cannot
    support.  Both readers of the verdict come from this one call, so the two
    cannot drift apart (#9236 review, P3); recomputing the terms at the return
    site is what made them disagree.

    `restored != 1` is the sharp case: the rebuild did not bring the Point
    back, i.e. the truth never held the destroy this door reported, which
    falsifies the whole trade the ordering makes.
    """
    ok = bool(reported) and recorded == 0 and live == 0 and restored == 1
    return (_SECTION3_OK if ok else "LOST"), ok


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    tmp = Path(tempfile.mkdtemp(prefix="tt7951-del-"))
    events = tmp / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp / "del-outbox.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        proj = sdk._get_proj()
        print(f"journal_backed (guard armed): {proj._journal_backed}")

        # ── 1. the ordering: cache delete BEFORE the record (#9207) ────────
        pid = sdk.create_point("observation", "a deletion in order")["id"]
        order: list[str] = []
        real_mutate = sdk._journal_entity_mutation

        def spy_mutate(label, id_val, op, **kwargs):
            if op == "delete":
                order.append("journal-EntityMutated-delete")
            return real_mutate(label, id_val, op, **kwargs)

        _orig_query, restore_query = _patch_query(
            proj.g, lambda orig: _record_deletes(orig, order))
        sdk._journal_entity_mutation = spy_mutate
        try:
            sdk.delete_point(pid)
        finally:
            restore_query()
            sdk._journal_entity_mutation = real_mutate

        print(f"delete_point order:              {order}")
        cache_first = (order.count("journal-EntityMutated-delete") == 1
                       and "cache-DETACH-DELETE" in order
                       and order.index("cache-DETACH-DELETE")
                       < order.index("journal-EntityMutated-delete"))
        print("ORDERING VERDICT: "
              f"{'CACHE-FIRST (#9207)' if cache_first else 'RECORD-FIRST (the #9207 DEFECT)'}")
        print(f"delete records committed:        {len(_deletes(events, pid))}")
        print(f"Point live after the delete:     {_live(proj, pid)}")
        proj.rebuild_all(str(events), confirm_destructive=True)
        print(f"Point live after rebuild_all:    {_live(proj, pid)}  "
              "(0 = the deletion is durable)")

        # ── 2. a cache write that did not land appends NO record (#9207) ───
        tmp2 = Path(tempfile.mkdtemp(prefix="tt7951-del-fail-"))
        events2 = tmp2 / "events"
        events2.mkdir()
        sdk2 = TortoiseSDK(str(tmp2 / "fail.db"),
                           event_log_path=str(events2 / "events.jsonl"))
        try:
            proj2 = sdk2._get_proj()
            pid2 = sdk2.create_point("observation", "must survive")["id"]
            _orig2, restore2 = _patch_query(proj2.g, _fail_deletes)
            raised2: BaseException | None = None
            try:
                sdk2.delete_point(pid2)
            except Exception as exc:  # the refusal is the point of the probe
                raised2 = exc
                print(f"failed cache delete raised:      {type(exc).__name__}"
                      f" (operation={getattr(exc, 'operation', '?')!r},"
                      f" record_committed={getattr(exc, 'record_committed', '?')!r})")
            finally:
                restore2()
            recorded2 = len(_deletes(events2, pid2))
            live2 = _live(proj2, pid2)
            print(f"Point live after refused delete: {live2}  "
                  "(1 = the destroy did not land)")
            print(f"delete records committed:        {recorded2}  "
                  "(0 = the journal claims nothing the cache did not do)")
            # No record for a destroy that never happened. Pre-#9207 this read
            # 1 — the record was already on disk before the refused write.
            no_record_ok = (raised2 is not None and live2 == 1
                            and recorded2 == 0)
            print("REFUSAL VERDICT: "
                  f"{'NO RECORD FOR A DESTROY THAT DID NOT HAPPEN' if no_record_ok else 'THE RECORD OVER-CLAIMED (DEFECT)'}")
        finally:
            sdk2.close()

        # ── 3. a landed destroy whose append failed: reported, truth wins ──
        tmp3 = Path(tempfile.mkdtemp(prefix="tt7951-del-heal-"))
        events3 = tmp3 / "events"
        events3.mkdir()
        sdk3 = TortoiseSDK(str(tmp3 / "heal.db"),
                           event_log_path=str(events3 / "events.jsonl"))
        try:
            proj3 = sdk3._get_proj()
            pid3 = sdk3.create_point("observation", "destroyed, not recorded")["id"]
            log3 = sdk3._get_event_log()

            def _boom3(_event):
                raise OSError("simulated journal append failure (probe)")

            real_append3 = log3.append
            log3.append = _boom3
            raised3: BaseException | None = None
            try:
                sdk3.delete_point(pid3)
            except Exception as exc:
                raised3 = exc
                print(f"failed append after the destroy: {type(exc).__name__}"
                      f" (operation={getattr(exc, 'operation', '?')!r},"
                      f" record_committed={getattr(raised3, 'record_committed', '?')!r},"
                      f" record={getattr(raised3, 'record', '?')!r})")
            finally:
                log3.append = real_append3
            recorded3 = len(_deletes(events3, pid3))
            live3 = _live(proj3, pid3)
            # The arm is UNCONFIRMED, never "the cache still holds it": the
            # destroy landed before the append was attempted.
            reported = (raised3 is not None
                        and type(raised3).__name__ == "WriteNotLandedError"
                        and getattr(raised3, "record_committed", None) is False
                        and getattr(raised3, "record", None)
                        == "EntityMutated op=delete"
                        and "Nothing was destroyed in the cache"
                        not in str(raised3))
            print(f"record committed despite failure: {recorded3}  "
                  "(0 = the append never landed)")
            print(f"caller told UNCONFIRMED (not silent): {reported}")
            print(f"Point live before rebuild:       {live3}  "
                  "(0 = the destroy landed)")
            proj3.rebuild_all(str(events3), confirm_destructive=True)
            restored = _live(proj3, pid3)
            print(f"Point live after rebuild:        {restored}  "
                  "(1 = the TRUTH still held it and the replay restored it)")
            sentence3, commit_ok = _section3_verdict(
                reported, recorded3, live3, restored)
            print(f"COMMIT VERDICT: {sentence3}")
        finally:
            sdk3.close()

        # ── 4. the recorded residuals, shown rather than hidden ────────────
        rows = proj.g.query(
            "CREATE (n:S1Probe {id:'raw-handle'}) RETURN 'via-raw-handle'"
        ).result_set
        print(f"RAW HANDLE LANE (proj.g.query):  SUCCEEDED, rows = {rows}")
        p4 = sdk.create_point("observation", "delete_entity door")["id"]
        order4: list[str] = []
        real_mutate4 = sdk._journal_entity_mutation

        def spy_mutate4(label, id_val, op, **kwargs):
            if op == "delete":
                order4.append("journal-EntityMutated-delete")
            return real_mutate4(label, id_val, op, **kwargs)

        _orig4, restore4 = _patch_query(
            proj.g, lambda orig: _record_deletes(orig, order4))
        sdk._journal_entity_mutation = spy_mutate4
        try:
            sdk.delete_entity(p4)
        finally:
            restore4()
            sdk._journal_entity_mutation = real_mutate4
        print(f"delete_entity order:             {order4}")
        print("RESIDUAL: the raw handle is NOT closed, and `_delete_entity` is "
              "still apply-first — both recorded on #7951; increment 4a routes "
              "ONE delete door (`delete_point`).")
        # The exit code reads the SAME verdict object section 3 printed, so a
        # printed `LOST` can never accompany a passing exit status (#9236
        # review, P3).  Do not recompute the terms here.
        return 0 if (cache_first and no_record_ok and commit_ok) else 1
    finally:
        sdk.close()


if __name__ == "__main__":
    sys.exit(main())
