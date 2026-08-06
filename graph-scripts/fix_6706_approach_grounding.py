#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""(#6706) Wire evidence-to-approach IMPL edges so grounding reaches approaches.

Resolution events (g≈1.27) exist but approaches (g=0) are disconnected.
Three IMPL edges:
  Stream D shipped    → IMPL → BFS approach
  Doc extraction      → IMPL → LLM edge discovery
  NAND edges fix      → IMPL → FalkorDB PageRank

Idempotent: safe to re-run — checks for existing wiring before creating.
"""
from __future__ import annotations

import sys
sys.path.insert(0, '/Users/home/eldato/negation-game-explorations/tortoise')

from tortoise.log import EventLog
from tortoise.api import EventAPI, provenance
from tortoise.projection import FalkorProjection

LOG_PATH = "/Users/home/eldato/negation-game-explorations/tortoise/events.jsonl"
DB_PATH  = "/Users/home/eldato/negation-game-explorations/tortoise/tortoise.db"

g = db.select_graph('tortoise')

log = EventLog(LOG_PATH)
proj = FalkorProjection()
api = EventAPI(log, initiated_by="user", agent_id="fix-6706@pi",
               projection=proj)

SRC = "fix-6706"
PROV = provenance(SRC, (0, 0), "", extracted_by="manual@6706")

# ── Resolution events (from #6704) ─────────────────────────────────────

RES_EVENTS = {
    "stream_d": "01KXH9JPV0CNW2A38FA3EQMQAB",   # Stream D shipped (#5350)
    "doc_ext":  "01KXH9JQ0E339E7Q1HAGG89GWQ",   # Doc extraction fix (#6679)
    "nand_fix": "01KXH9JQ31CQ9423B1HVF1C4M6",   # NAND edges fix (#6680)
}

# ── Approach targets ───────────────────────────────────────────────────

# BFS approach: Stream D implementation + algorithm nodes
BFS_TARGETS = [
    "01KXH8WJM0CKT4S136A1E5BY2K",  # [IMPLEMENTATION] Stream D: in-memory belief graph, BFS shock propagation
    "01KXH8WJMMQSX0GDZMP8XKTN1D",  # [ALGORITHM] propagate_shock(): BFS from epicenter
]

# LLM edge discovery approach
LLM_TARGETS = [
    "01KXH2CDAK8RMHZFMGD1F41WKS",  # [APPROACH] LLM edge discovery
]

# FalkorDB PageRank approach
FALKOR_TARGETS = [
    "01KXH2CDAGC2RQ1FNTYEKPSH50",  # [APPROACH] FalkorDB GraphBLAS PageRank
]

WIRING = [
    (RES_EVENTS["stream_d"], BFS_TARGETS,   "Stream D shipped → BFS approach"),
    (RES_EVENTS["doc_ext"],  LLM_TARGETS,   "Doc extraction → LLM edge discovery"),
    (RES_EVENTS["nand_fix"], FALKOR_TARGETS, "NAND edges fix → FalkorDB PageRank"),
]

# ── Idempotency guard ──────────────────────────────────────────────────

ctx = "evidence-to-approach"  # ponytail: distinct context for idempotency check

existing = g.query(
    "MATCH (n:Point {context:$ctx}) WHERE n.content CONTAINS 'IMPL' "
    "RETURN count(n)", params={"ctx": ctx}
).result_set[0][0]

if existing > 0:
    print(f"Wiring already exists ({existing} IMPL edges in context '{ctx}'). Skipping.")
    skip_wiring = True
else:
    skip_wiring = False

# ── Wire IMPL edges ────────────────────────────────────────────────────

if not skip_wiring:
    print("=== Wiring evidence-to-approach IMPL edges ===\n")
    total = 0
    for src_id, tgt_ids, label in WIRING:
        print(f"  {label}:")
        for tgt_id in tgt_ids:
            api.add_operator("IMPL", [src_id, tgt_id], ctx, PROV,
                             content=f"IMPL(resolution → approach): {label}")
            total += 1
            print(f"    {src_id[:20]}... → {tgt_id[:20]}...")
    print(f"\n  Created {total} IMPL edges")

# ── Re-run grounding ───────────────────────────────────────────────────

print("\n=== Recomputing grounding ===\n")
grounding = proj.compute_grounding(lam=0.6)

# ── Verification ───────────────────────────────────────────────────────

# Check approaches
approach_ids = [t for _, tgts, _ in WIRING for t in tgts]
for pid in approach_ids:
    r = g.query("MATCH (n:Point {id:$id}) RETURN n.content, n.grounding",
                params={"id": pid})
    if r.result_set:
        content, g_val = r.result_set[0]
        status = "✅" if (g_val or 0) > 0 else "❌"
        print(f"  {status} g={g_val:.4f} | {content[:80]}...")

# Summary
n_grounded = g.query("MATCH (n:Point) WHERE n.grounding > 0 RETURN count(n)").result_set[0][0]
n_total = g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
n_approaches_grounded = g.query(
    "MATCH (n:Point) WHERE n.content STARTS WITH '[APPROACH]' AND n.grounding > 0 "
    "RETURN count(n)"
).result_set[0][0]

print(f"\n  State: {n_grounded}/{n_total} grounded, {n_approaches_grounded}/6 approaches grounded")

proj.close()
db.close()

print("\n=== Done (#6706) ===")
