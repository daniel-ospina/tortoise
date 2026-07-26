#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""(#6709) Wire IMPL edges from stream-d comparison Points to the approach Points they compare.

The stream-d context has comparison Points (e.g. "[COMPARISON] Stream D: BFS vs Tortoise")
that are not connected to the approach Points they reference (e.g. "[ALGORITHM] propagate_shock()").
This script adds IMPL edges from each comparison Point to the specific approach Point(s) it discusses.

Idempotent: checks for existing IMPL edges before creating new ones.
"""
from __future__ import annotations

import sys
sys.path.insert(0, '/Users/home/eldato/negation-game-explorations/tortoise')

from tortoise.log import EventLog
from tortoise.api import EventAPI, provenance
from tortoise.projection import FalkorProjection

LOG_PATH = "/Users/home/eldato/negation-game-explorations/tortoise/events.jsonl"
DB_PATH  = "/Users/home/eldato/negation-game-explorations/tortoise/tortoise.db"

from redislite.falkordb_client import FalkorDB
db = FalkorDB(DB_PATH)
g = db.select_graph('tortoise')

log = EventLog(LOG_PATH)
proj = FalkorProjection(DB_PATH)
api = EventAPI(log, initiated_by="user", agent_id="fix-6709@pi",
               projection=proj)

SRC = "fix-6709"
PROV = provenance(SRC, (0, 0), "", extracted_by="manual@6709")

# ── Mapping: comparison Point ULIDs → approach Point ULIDs ─────────────
WIRING = [
    # BFS comparison → BFS propagate_shock algorithm + Stream D implementation
    ("01KXH8WJP331KYC6PWE8TPCXNB",  # [COMPARISON] BFS, max_depth=2, localized
     "01KXH8WJMMQSX0GDZMP8XKTN1D",  # [ALGORITHM] propagate_shock()
     "(#6709) IMPL(BFS comparison → BFS propagation algorithm)"),
    ("01KXH8WJP331KYC6PWE8TPCXNB",  # [COMPARISON] BFS, max_depth=2, localized
     "01KXH8WJM0CKT4S136A1E5BY2K",  # [IMPLEMENTATION] Stream D (#5350)
     "(#6709) IMPL(BFS comparison → Stream D implementation)"),

    # max_depth=2 comparison → BFS propagate_shock (max_depth is a BFS parameter)
    ("01KXH8WJR90KNJRQJ9XVZKTBY2",  # [COMPARISON] max_depth=2
     "01KXH8WJMMQSX0GDZMP8XKTN1D",  # [ALGORITHM] propagate_shock()
     "(#6709) IMPL(max_depth comparison → BFS propagation)"),

    # in-memory comparison → Stream D implementation (storage model)
    ("01KXH8WJPMXBSXN0CVJS65TSN4",  # [COMPARISON] in-memory vs FalkorDB
     "01KXH8WJM0CKT4S136A1E5BY2K",  # [IMPLEMENTATION] Stream D (#5350)
     "(#6709) IMPL(in-memory comparison → Stream D implementation)"),

    # weighted ratio comparison → compute_confidence algorithm
    ("01KXH8WJQ4QBE3ZBVG8H1VFYPT",  # [COMPARISON] weighted edge ratio vs linear solve
     "01KXH8WJMDD987V6Q8TWE44101",  # [ALGORITHM] compute_confidence()
     "(#6709) IMPL(weighted ratio comparison → confidence algorithm)"),

    # subscriptions comparison → Subscriptions feature
    ("01KXH8WJQHRJC92XPVKFNF3CCM",  # [COMPARISON] subscriptions built in
     "01KXH8WJMT7A202C75EXMG4C51",  # [FEATURE] Subscriptions
     "(#6709) IMPL(subscriptions comparison → subscriptions feature)"),

    # lifecycle comparison → Claim lifecycle feature
    ("01KXH8WJR1P799G61M8MJ362R0",  # [COMPARISON] lifecycle states
     "01KXH8WJNC74PMBZBWSZMW6CAS",  # [FEATURE] Claim lifecycle
     "(#6709) IMPL(lifecycle comparison → lifecycle feature)"),
]

# ── Idempotency: check existing IMPL edges ─────────────────────────────
print("=== #6709: Wiring comparison Points → approach Points ===\n")

created = 0
skipped = 0
for comp_id, appr_id, label in WIRING:
    # Check if IMPL edge already exists from comp_id to appr_id
    # Check if any operator Point with this marker already connects comp → appr
    existing = g.query(
        "MATCH (op:Point) WHERE op.content CONTAINS $marker "
        "MATCH (op)-[:IMPL]->(c:Point {id:$comp}) "
        "MATCH (op)-[:IMPL]->(a:Point {id:$appr}) "
        "RETURN count(op)",
        params={"comp": comp_id, "appr": appr_id, "marker": "#6709"}
    ).result_set[0][0]

    if existing > 0:
        print(f"  SKIP: {label}")
        skipped += 1
        continue

    # Wire: comparison → approach (IMPL — the comparison discusses the approach)
    api.add_operator("IMPL", [comp_id, appr_id], "stream-d", PROV, content=label)
    print(f"  CREATED: {label}")
    created += 1

# ── Verification ───────────────────────────────────────────────────────
print(f"\nCreated: {created}, Skipped: {skipped}")

impl_ct = g.query("MATCH ()-[r:IMPL]->() RETURN count(r)").result_set[0][0]
nand_ct = g.query("MATCH ()-[r:NAND]->() RETURN count(r)").result_set[0][0]
input_ct = g.query("MATCH ()-[r:INPUT]->() RETURN count(r)").result_set[0][0]
print(f"State: {impl_ct} IMPL, {nand_ct} NAND, {input_ct} INPUT")

# Recompute grounding so new edges propagate
print("\nRecomputing grounding with new edges...")
grounding = proj.compute_grounding(lam=0.6)
grounded = {pid: v for pid, v in grounding.items() if v > 0}
print(f"  Points with grounding > 0: {len(grounded)}")

proj.close()
db.close()
print("\n=== Done (#6709) ===")
