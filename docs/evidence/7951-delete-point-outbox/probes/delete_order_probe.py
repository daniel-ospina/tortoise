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
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _journal(events: Path) -> list[dict]:
    from tortoise.log import EventLog

    path = events / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _deletes(events: Path, pid: str) -> list[dict]:
    return [e for e in _journal(events)
            if e.get("type") == "EntityMutated"
            and e.get("id") == pid and e.get("op") == "delete"]


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
        real_query = proj.g.query

        def spy_mutate(label, id_val, op, **kwargs):
            if op == "delete":
                order.append("journal-EntityMutated-delete")
            return real_mutate(label, id_val, op, **kwargs)

        def spy_query(_self, cypher, *args, **kwargs):
            if "DETACH DELETE n" in (cypher or ""):
                order.append("cache-DETACH-DELETE")
            return real_query(cypher, *args, **kwargs)

        type(proj.g).query = spy_query
        sdk._journal_entity_mutation = spy_mutate
        try:
            sdk.delete_point(pid)
        finally:
            type(proj.g).query = real_query
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
            try:
                sdk2.delete_point(pid2)
            except Exception as exc:  # the refusal is the point of the probe
                print(f"failed append raised:            {type(exc).__name__}"
                      f" (operation={getattr(exc, 'operation', '?')!r})")
            print(f"Point live after refused delete: {_live(proj2, pid2)}  "
                  "(1 = nothing was destroyed)")
            print(f"delete records committed:        {len(_deletes(events2, pid2))}")
            refusal_ok = (_live(proj2, pid2) == 1
                          and not _deletes(events2, pid2))
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
            real3 = proj3.g.query

            def failing_delete(_self, cypher, *args, **kwargs):
                if "DETACH DELETE n" in (cypher or ""):
                    raise RuntimeError("simulated engine failure (probe)")
                return real3(cypher, *args, **kwargs)

            type(proj3.g).query = failing_delete
            try:
                sdk3.delete_point(pid3)
            except Exception as exc:
                print(f"failed cache delete raised:      {type(exc).__name__}")
            type(proj3.g).query = real3
            committed = len(_deletes(events3, pid3))
            print(f"record committed despite failure: {committed}")
            print(f"Point live before rebuild:       {_live(proj3, pid3)}")
            proj3.rebuild_all(str(events3), confirm_destructive=True)
            healed = _live(proj3, pid3)
            print(f"Point live after rebuild:        {healed}")
            print("COMMIT VERDICT: "
                  f"{'COMMITTED + REPLAY-HEALABLE' if committed == 1 and healed == 0 else 'LOST'}")
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

        def spy_query4(_self, cypher, *args, **kwargs):
            if "DETACH DELETE n" in (cypher or ""):
                order4.append("cache-DETACH-DELETE")
            return real_query(cypher, *args, **kwargs)

        type(proj.g).query = spy_query4
        sdk._journal_entity_mutation = spy_mutate4
        try:
            sdk.delete_entity(p4)
        finally:
            type(proj.g).query = real_query
            sdk._journal_entity_mutation = real_mutate4
        print(f"delete_entity order:             {order4}")
        print("RESIDUAL: the raw handle is NOT closed, and `_delete_entity` is "
              "still apply-first — both recorded on #7951; increment 4a routes "
              "ONE delete door (`delete_point`).")
        return 0 if (journal_first and refusal_ok) else 1
    finally:
        sdk.close()


if __name__ == "__main__":
    sys.exit(main())
