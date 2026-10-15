#!/usr/bin/env python3
"""#7951 increment 1 — the OUTBOX ordering on `create_point`, demonstrated.

Runs on the EMBEDDED lane (no server needed):

    env -u TORTOISE_DB_URI TORTOISE_TEST_CARVE_OUT=1 \
      uv run python docs/evidence/7951-create-point-outbox/probes/order_probe.py

It demonstrates, against the shipped modules:

  1. `create_point` arms the §17.1 guard (the journal is wired) and appends its
     `PointAdded` record BEFORE any Point cache write — the ordering §17.1
     requires, and the opposite of the apply-first deviation the capstone proved.
  2. The committed record is what a replay folds: `rebuild_all` materialises the
     Point from the journal alone.
  3. If the cache write fails, the RECORD still stands and a later replay heals
     the cache — the ordering contract's replay-healable window.
  4. The RECORDED RESIDUAL is shown, not hidden: a raw-handle write
     (`proj.g.query("CREATE …")`) still reaches the cache directly. §17.1's
     mechanism (1) does not close the raw handle; the `OVERRIDES:` line on #7951
     records that departure until the remaining doors are routed.
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path


def _journal(events: Path) -> list[dict]:
    from tortoise.log import EventLog

    path = events / "events.jsonl"
    return EventLog(str(path)).read_all() if path.exists() else []


def _count(proj, cypher: str, **params) -> int:
    rows = proj.g.query(cypher, params=params or None).result_set
    return int(rows[0][0]) if rows else 0


def main() -> int:
    from tortoise.sdk import TortoiseSDK

    tmp = Path(tempfile.mkdtemp(prefix="tt7951-"))
    events = tmp / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp / "outbox.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        proj = sdk._get_proj()
        print(f"journal_backed (guard armed): {proj._journal_backed}")

        # ── 1. the ordering: journal BEFORE the Point cache write ──────────
        order: list[str] = []
        real_emit = sdk._emit_event

        def spy_emit(type_, *args, **kwargs):
            if type_ == "PointAdded":
                order.append("journal-PointAdded")
            return real_emit(type_, *args, **kwargs)

        real_query = proj.g.query

        def spy_query(_self, cypher, *args, **kwargs):
            if ("CREATE (n:Point" in (cypher or "")
                    or re.search(r"MERGE \([a-z]*:Point", cypher or "")):
                order.append("cache-Point-write")
            return real_query(cypher, *args, **kwargs)

        type(proj.g).query = spy_query
        sdk._emit_event = spy_emit
        try:
            first = sdk.create_point("statement", "a journal-first claim")
            pid = first["id"]
        finally:
            type(proj.g).query = real_query
            sdk._emit_event = real_emit

        print(f"create_point order:              {order}")
        journal_before_cache = (order.count("journal-PointAdded") == 1
                                and "cache-Point-write" in order
                                and order.index("journal-PointAdded")
                                < order.index("cache-Point-write"))
        print(f"ORDERING VERDICT: {'JOURNAL-FIRST' if journal_before_cache else 'APPLY-FIRST (DEFECT)'}")

        # ── 2. the record is the record: a replay materialises the Point ───
        added = [e for e in _journal(events) if e.get("type") == "PointAdded"]
        print(f"journal PointAdded records:      {len(added)}")
        proj.rebuild_all(str(events), confirm_destructive=True)
        replayed = _count(proj, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid)
        print(f"rebuild_all materialised Point:  {replayed}")
        print(f"REPLAY VERDICT: {'RECORD REPRODUCES THE LIVE NODE' if replayed == 1 else 'DIVERGENT'}")

        # ── 3. a failed cache write leaves the committed record healable ───
        tmp2 = Path(tempfile.mkdtemp(prefix="tt7951-fail-"))
        events2 = tmp2 / "events"
        events2.mkdir()
        sdk2 = TortoiseSDK(str(tmp2 / "fail.db"),
                           event_log_path=str(events2 / "events.jsonl"))
        try:
            proj2 = sdk2._get_proj()
            real_query2 = proj2.g.query
            state = {"fail": True}

            def spy_query2(_self, cypher, *args, **kwargs):
                if state["fail"] and "CREATE (n:Point" in (cypher or ""):
                    raise RuntimeError("simulated cache write failure (probe)")
                return real_query2(cypher, *args, **kwargs)

            type(proj2.g).query = spy_query2
            try:
                sdk2.create_point("statement", "a claim whose cache write fails")
            except Exception as exc:  # the failure is the point of the probe
                print(f"failed cache write raised:        {type(exc).__name__}")
            type(proj2.g).query = real_query2

            added2 = [e for e in _journal(events2) if e.get("type") == "PointAdded"]
            pid2 = (added2[0].get("id") or (added2[0].get("point") or {}).get("id")) if added2 else None
            print(f"record committed despite failure: {len(added2)}")
            state["fail"] = False
            proj2.rebuild_all(str(events2), confirm_destructive=True)
            healed = _count(proj2, "MATCH (n:Point {id:$id}) RETURN count(n)", id=pid2) if pid2 else -1
            print(f"replay healed the cache:         {healed}")
            print(f"COMMIT VERDICT: {'COMMITTED + REPLAY-HEALABLE' if added2 and healed == 1 else 'LOST'}")
        finally:
            sdk2.close()

        # ── 4. the recorded residual: the raw handle is still open ─────────
        rows = proj.g.query(
            "CREATE (n:S1Probe {id:'raw-handle'}) RETURN 'via-raw-handle'"
        ).result_set
        print(f"RAW HANDLE LANE (proj.g.query):  SUCCEEDED, rows = {rows}")
        print("RESIDUAL: the raw handle is NOT closed — recorded on #7951 "
              "(the OVERRIDES: line); increment 1 routes one door, not 288.")
        return 0 if (journal_before_cache and replayed == 1) else 1
    finally:
        sdk.close()


if __name__ == "__main__":
    sys.exit(main())
