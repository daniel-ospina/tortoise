#!/usr/bin/env python3
"""S1 probe — does a write reach the cache directly after #8389 (4c801fb18)?

Creates a real `TortoiseSDK` with a configured JSONL journal (which ARMS
§17.1's guard via `FalkorProjection._bind_journal`), then attempts three
writes against the projection:

  1. `proj.apply(ev)`            — the fold lane; expect DirectCacheWriteRefused
  2. `proj.query("CREATE ...")`  — the public raw-query lane; expect refused
  3. `proj.g.query("CREATE ...")`— the RAW HANDLE lane; #8389's own commit
     message says this is "deliberately NOT closed", so it should SUCCEED

The journal is inspected afterwards: a write that reaches the cache with no
journal record is exactly the §17.1 failure mode ("a derived cache that is also
written to directly is not derived").

Run:  TORTOISE_DB_URI=... uv run python docs/evidence/7950-capstone/probes/s1_bypass_probe.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from tortoise.projection import DirectCacheWriteRefused
from tortoise.sdk import TortoiseSDK


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="s1-probe-"))
    journal = tmp / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp / "probe.db"), event_log_path=str(journal))
    try:
        proj = sdk._get_proj()
        sdk._journal_path_for_test = journal
        print(f"journal_backed (guard armed): {proj._journal_backed}")

        # 1 — the fold lane (guarded)
        try:
            proj.apply({"type": "PointAdded", "id": "s1-probe-fold", "text": "x"})
            print("FOLD LANE (proj.apply):          NO RAISE  <-- chokepoint NOT enforced")
        except DirectCacheWriteRefused as exc:
            print(f"FOLD LANE (proj.apply):          refused ({type(exc).__name__})")

        # 2 — the public raw-query lane (guarded since #8389)
        try:
            proj.query("CREATE (:S1Probe {id: 'public-lane'})")
            print("PUBLIC RAW LANE (proj.query):    NO RAISE  <-- NOT enforced")
        except DirectCacheWriteRefused as exc:
            print(f"PUBLIC RAW LANE (proj.query):    refused ({type(exc).__name__})")

        # 3 — the RAW HANDLE lane (`proj.g.query`) — the SDK's own live write lane
        raw_ok = False
        try:
            proj.g.query("CREATE (:S1Probe {id: 'raw-handle'})")
            raw_ok = True
            print("RAW HANDLE LANE (proj.g.query):   SUCCEEDED (write reached the cache)")
        except Exception as exc:
            print(f"RAW HANDLE LANE (proj.g.query):   refused ({type(exc).__name__}: {exc})")

        # Did the raw-handle write land? (read it back)
        res = proj.g.query("MATCH (n:S1Probe) RETURN n.id")
        rows = sorted(r[0] for r in (res.result_set or []))
        print(f"cache rows for :S1Probe after all three attempts: {rows}")

        # Did the journal see the raw-handle write?
        jlines = []
        if journal.exists():
            jlines = [ln for ln in journal.read_text().splitlines() if ln.strip()]
        print(f"journal records after all three attempts: {len(jlines)}")
        for ln in jlines:
            try:
                ev = json.loads(ln)
                print(f"  journal event: {ev.get('type')}")
            except Exception:
                print(f"  journal raw: {ln[:120]}")

        verdict = "BYPASS CONFIRMED" if (raw_ok and not jlines) else "no bypass observed"
        print(f"S1 VERDICT: {verdict}")
        return 0
    finally:
        sdk.close()


if __name__ == "__main__":
    sys.exit(main())
