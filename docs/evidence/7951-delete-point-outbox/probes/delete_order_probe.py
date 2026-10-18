#!/usr/bin/env python3
"""#7951 increment 4a — the OUTBOX ordering on `delete_point`, demonstrated.

Runs on the EMBEDDED lane (no server needed):

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
      .venv/bin/python docs/evidence/7951-delete-point-outbox/probes/delete_order_probe.py

It demonstrates, against the shipped modules:

  1. `delete_point` appends its `EntityMutated` op=delete record BEFORE the
     `DETACH DELETE` — the ordering §17.1 requires on the door where the
     apply-first window does not merely LOSE a write, it lets the next replay
     UNDO the deletion.
  2. A FAILED append refuses the delete: the node survives, the journal holds
     nothing, and the caller is told (`WriteNotLandedError`). Pre-fix the node
     was destroyed with no record and `rebuild_all` resurrected it.
  3. A committed record whose cache delete did not land is healed by a replay —
     the residual window is the replay-healable one.
  4. The RECORDED RESIDUALS are shown, not hidden: the raw handle
     (`proj.g.query`) still writes the cache directly, and `delete_entity` (the
     generic entity delete) is still apply-first — both recorded on #7951 and in
     plan §7 increment 4.

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

        # ── 1. the ordering: journal BEFORE the cache delete ───────────────
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
        journal_first = (order.count("journal-EntityMutated-delete") == 1
                         and "cache-DETACH-DELETE" in order
                         and order.index("journal-EntityMutated-delete")
                         < order.index("cache-DETACH-DELETE"))
        print("ORDERING VERDICT: "
              f"{'JOURNAL-FIRST' if journal_first else 'APPLY-FIRST (DEFECT)'}")
        print(f"delete records committed:        {len(_deletes(events, pid))}")
        print(f"Point live after the delete:     {_live(proj, pid)}")
        proj.rebuild_all(str(events), confirm_destructive=True)
        print(f"Point live after rebuild_all:    {_live(proj, pid)}  "
              "(0 = the deletion is durable)")

        # ── 2. a failed append refuses the delete (no resurrection) ────────
        tmp2 = Path(tempfile.mkdtemp(prefix="tt7951-del-fail-"))
        events2 = tmp2 / "events"
        events2.mkdir()
        sdk2 = TortoiseSDK(str(tmp2 / "fail.db"),
                           event_log_path=str(events2 / "events.jsonl"))
        try:
            proj2 = sdk2._get_proj()
            pid2 = sdk2.create_point("observation", "must survive")["id"]
            log2 = sdk2._get_event_log()

            def _boom(_event):
                raise OSError("simulated journal append failure (probe)")

            log2.append = _boom
            refused: BaseException | None = None
            try:
                sdk2.delete_point(pid2)
            except Exception as exc:  # the refusal is the point of the probe
                refused = exc
                print(f"failed append raised:            {type(exc).__name__}"
                      f" (operation={getattr(exc, 'operation', '?')!r},"
                      f" record_committed={getattr(exc, 'record_committed', '?')!r})")
            print(f"Point live after refused delete: {_live(proj2, pid2)}  "
                  "(1 = nothing was destroyed)")
            print(f"delete records committed:        {len(_deletes(events2, pid2))}")
            # The NO-record arm: a caller that re-issues is right here.
            no_record_arm = (refused is not None
                             and type(refused).__name__ == "WriteNotLandedError"
                             and getattr(refused, "record_committed", None) is False)
            refusal_ok = (_live(proj2, pid2) == 1
                          and not _deletes(events2, pid2)
                          and no_record_arm)
            print("REFUSAL VERDICT: "
                  f"{'NOTHING DESTROYED, NOTHING SKIPPED FROM THE RECORD' if refusal_ok else 'VIOLATED'}")
        finally:
            sdk2.close()

        # ── 3. a committed record heals a failed cache delete ──────────────
        tmp3 = Path(tempfile.mkdtemp(prefix="tt7951-del-heal-"))
        events3 = tmp3 / "events"
        events3.mkdir()
        sdk3 = TortoiseSDK(str(tmp3 / "heal.db"),
                           event_log_path=str(events3 / "events.jsonl"))
        try:
            proj3 = sdk3._get_proj()
            pid3 = sdk3.create_point("observation", "cache lags")["id"]
            _orig3, restore3 = _patch_query(proj3.g, _fail_deletes)
            raised: BaseException | None = None
            try:
                sdk3.delete_point(pid3)
            except Exception as exc:
                raised = exc
                print(f"failed cache delete raised:      {type(exc).__name__}"
                      f" (operation={getattr(exc, 'operation', '?')!r},"
                      f" record_committed={getattr(exc, 'record_committed', '?')!r},"
                      f" record={getattr(exc, 'record', '?')!r})")
            finally:
                restore3()
            committed = len(_deletes(events3, pid3))
            # The COMMITTED arm: the record landed and the cache write did not,
            # so the caller must be told "do NOT re-issue blindly".
            committed_arm = (raised is not None
                             and type(raised).__name__ == "WriteNotLandedError"
                             and getattr(raised, "record_committed", None) is True
                             and getattr(raised, "record", None)
                             == "EntityMutated op=delete")
            print(f"record committed despite failure: {committed}")
            print(f"caller told COMMITTED (no blind re-issue): {committed_arm}")
            print(f"Point live before rebuild:       {_live(proj3, pid3)}")
            proj3.rebuild_all(str(events3), confirm_destructive=True)
            healed = _live(proj3, pid3)
            print(f"Point live after rebuild:        {healed}")
            print("COMMIT VERDICT: "
                  f"{'COMMITTED + REPLAY-HEALABLE' if committed == 1 and healed == 0 and committed_arm else 'LOST'}")
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
        return 0 if (journal_first and refusal_ok and committed_arm) else 1
    finally:
        sdk.close()


if __name__ == "__main__":
    sys.exit(main())
