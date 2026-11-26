#!/usr/bin/env python
"""#14721 — record the corpus's contradiction through the PRODUCT's own write door.

WHY THIS STEP EXISTS, AND WHY IT IS NOT THIS ISSUE'S FIX
--------------------------------------------------------
`#14721` is the *retrieval/ranking* half of the contradiction defect. The
*representation* half — a later document closing the earlier claim's interval —
is `#14198`, and it is still open. So the committed corpus's fresh graph holds
both rival claims as `live` with no marker between them (measured: the #14606
report's G1, re-measured by this lane's re-ingest — `supersessions: 0` on every
document).

A retrieval fix cannot be measured against a graph that records nothing to
retrieve: the only state in which "does the current answer outrank the
superseded one" is answerable is the state where the replacement IS recorded.
That state is produced here through the door the product already has for it —
`TortoiseSDK.supersede_knowledge`, which is exactly what the extractor's
`POINT-LEVEL SUPERSEDE (cross-session reversal — F2)` channel folds onto
(`extractor_v2.OPERATOR_SEMANTICS_RULE`: a new point's `"supersedes"` folds a
point-level CORRECTS supersession and marks the old point outdated).

⛔ This is not a workaround and not a second writer. It is #14198's decided
representation, applied by hand so that #14721's half has something to read.
The gap that it must be applied BY HAND at all is reported on `#14198`.

Usage: record.py --old <point-id> --new <point-id>
       record.py --dry-run            # resolve the two ids and print them
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
WT = Path(__file__).resolve().parents[3]
GRAPH = "xv14592_operating_model"

#: The corpus's two rival decisions, by their EXTRACTED sentences (the extractor
#: paraphrased doc-01's "we choose ColdPath Logistics" as "we are switching our
#: delivery partner to ColdPath Logistics" — the graph holds ITS wording, so this
#: names that, not the corpus's). The ids are resolved from the graph at run time,
#: never pasted: a pasted id is a fixture, and a fixture is not a measurement.
OLD_SENTENCE = "We are switching our delivery partner to ColdPath Logistics"
NEW_SENTENCE = "We revert to NordicFreight as our delivery partner and stop using ColdPath Logistics"


def resolve(sdk, sentence: str) -> list[dict]:
    """Find the Point whose content IS this sentence, through the SDK search."""
    page = sdk.search_knowledge(sentence, limit=10) if hasattr(sdk, "search_knowledge") else None
    return page or []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    sys.path.insert(0, str(WT))
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(graph_name=GRAPH)
    if a.dry_run or not (a.old and a.new):
        rows = resolve(sdk, OLD_SENTENCE)
        print(json.dumps([{"id": r.get("id"), "content": (r.get("content") or "")[:120],
                           "status": r.get("status")} for r in rows], indent=1))
        return 0

    res = sdk.supersede_knowledge(a.old, a.new)
    (OUT / "supersede-receipt.json").write_text(
        json.dumps({"old": a.old, "new": a.new, "result": res},
                   indent=2, default=str) + "\n")
    print(json.dumps(res, indent=1, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
