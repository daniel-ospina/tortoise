#!/usr/bin/env python3
# Historical — uses embedded tortoise.db. Do not run against production Docker.
"""(#6704) Make Tortoise graph operational for comparing approaches.

Three fixes:
  1. Create 3 resolution events to seed the `a` vector.
  2. Re-project stream-d-wiring with correct ULID inputs.
  3. Auto-hook compute_grounding() on resolution-event additions.

Idempotent: safe to re-run — skips resolution events if already present.
"""
from __future__ import annotations  # noqa: I001

import sys
sys.path.insert(0, '/Users/home/eldato/negation-game-explorations/tortoise')

from tortoise.log import EventLog  # noqa: I001
from tortoise.api import EventAPI, provenance
from tortoise.projection import FalkorProjection

LOG_PATH = "/Users/home/eldato/negation-game-explorations/tortoise/events.jsonl"

log = EventLog(LOG_PATH)
proj = FalkorProjection()
g = proj.g
api = EventAPI(log, initiated_by="user", agent_id="fix-6704@pi",
               projection=proj)

SRC = "fix-6704"
PROV = provenance(SRC, (0, 0), "",
                  extracted_by="manual@6667")

# ── Idempotency guard ──────────────────────────────────────────────────

# Check for resolution events by unique content markers (not '#6704' which
# appears in operator bridge content too).
existing = g.query(
    "MATCH (n:Point {context:'resolution-event'}) "
    "WHERE n.content STARTS WITH 'RESOLVED' AND ("
    "  n.content CONTAINS $m1 OR n.content CONTAINS $m2 OR n.content CONTAINS $m3"
    ") RETURN count(n)",
    params={"m1": "Stream D shipped (#5350)",
            "m2": "LLM extraction now handles markdown",
            "m3": "NAND edges now queryable"}
).result_set[0][0]
if existing >= 3:
    print(f"Resolution events already exist ({existing} found). Skipping Fix 1.")
    # Find existing resolution event IDs for wiring
    res_rows = g.query(
        "MATCH (n:Point {context:'resolution-event'}) "
        "WHERE n.content STARTS WITH 'RESOLVED' AND ("
        "  n.content CONTAINS $m1 "
        "  OR n.content CONTAINS $m2 "
        "  OR n.content CONTAINS $m3 "
        ") RETURN n.id, n.content",
        params={"m1": "Stream D shipped (#5350)",
                "m2": "LLM extraction now handles markdown",
                "m3": "NAND edges now queryable"}
    ).result_set
    ev1 = ev2 = ev3 = None
    for rid, rcontent in res_rows:
        if 'Stream D shipped' in rcontent and '#5350' in rcontent:
            ev1 = rid
        elif 'LLM extraction' in rcontent and '#6679' in rcontent:
            ev2 = rid
        elif 'NAND edges' in rcontent and '#6680' in rcontent:
            ev3 = rid
    skip_fix1 = all([ev1, ev2, ev3])
    if skip_fix1:
        print(f"  Found existing resolution events: {ev1[:20]}..., {ev2[:20]}..., {ev3[:20]}...")
else:
    skip_fix1 = False


# ── Fix 2 FIRST: Retract broken stream-d-wire operators ─────────────────
# Must run BEFORE Fix 1 so compute_grounding() never sees broken edges (#6704 review).

print("\n=== Fix 2: Re-projecting stream-d-wiring operators ===")

# Delete the 5 broken stream-d-wire operator Points (zero edges, wrong input IDs)
BROKEN_IDS = [
    "01KXH900CDG4063DZ7YX0D1GPB",  # NAND(917, 949)
    "01KXH900CMFNG0V2FFP468AYDM",  # NAND(919, 949)
    "01KXH900CSDNQW7AX3C5AA28ST",  # NAND(918, 950)
    "01KXH900CW2RGA0A7MSFRMR7S8",  # IMPL(917, 949)
    "01KXH900D0JYEXKPW33AZRKAZA",  # IMPL(915, 915)
]

# Check which are still active (not yet retracted)
active = g.query(
    "MATCH (n:Point {id:$id}) RETURN count(n)",
    params={"id": BROKEN_IDS[0]}
).result_set[0][0]
if active > 0:
    print("Deleting broken stream-d-wire operator Points...")
    for pid in BROKEN_IDS:
        api.retract_point(pid, corrects="fix-6704")
        print(f"  retracted: {pid}")
else:
    print("Broken operator Points already retracted. Skipping deletion.")

# Mapping: short ID → stream-d Point ULID (by semantic content match)
ID_MAP = {
    "915": "01KXH8WJNC74PMBZBWSZMW6CAS",  # [FEATURE] Claim lifecycle
    "917": "01KXH8WJP331KYC6PWE8TPCXNB",  # [COMPARISON] BFS vs PageRank
    "918": "01KXH8WJQ4QBE3ZBVG8H1VFYPT",  # [COMPARISON] weighted ratio vs linear solve
    "919": "01KXH8WJPMXBSXN0CVJS65TSN4",  # [COMPARISON] in-memory vs FalkorDB
    # 949 & 950 both map to Tortoise grounding comparison — that single Point
    # covers both PageRank and linear-solve aspects of the Tortoise approach.
    "949": "01KXH8WJRECKX42BGYA4GTFECJ",  # [COMPARISON] Tortoise grounding
    "950": "01KXH8WJRECKX42BGYA4GTFECJ",  # same — both PageRank & linear solve aspects
}

# Guard: only re-create if the new operators don't already exist
# (check by content pattern unique to the fix script)
existing_recreated = g.query(
    "MATCH (n:Point) WHERE n.content CONTAINS 'competing propagation scopes' "
    "AND n.context = 'stream-d-wire' RETURN count(n)"
).result_set[0][0]

if existing_recreated == 0:
    ctx_wire = "stream-d-wire"

    api.add_operator("NAND", [ID_MAP["917"], ID_MAP["949"]], ctx_wire, PROV,
                     content="NAND(BFS, Tortoise grounding) — competing propagation scopes")
    print(f"  NAND(BFS, Tortoise): {ID_MAP['917'][:20]}... ⊥ {ID_MAP['949'][:20]}...")

    api.add_operator("NAND", [ID_MAP["919"], ID_MAP["949"]], ctx_wire, PROV,
                     content="NAND(in-memory, Tortoise grounding) — competing storage models")
    print(f"  NAND(in-memory, Tortoise): {ID_MAP['919'][:20]}... ⊥ {ID_MAP['949'][:20]}...")

    api.add_operator("NAND", [ID_MAP["918"], ID_MAP["950"]], ctx_wire, PROV,
                     content="NAND(weighted ratio, linear solve) — competing confidence models")
    print(f"  NAND(weighted ratio, linear solve): {ID_MAP['918'][:20]}... ⊥ {ID_MAP['950'][:20]}...")

    api.add_operator("IMPL", [ID_MAP["917"], ID_MAP["949"]], ctx_wire, PROV,
                     content="IMPL(BFS, Tortoise) — BFS can be ported to FalkorDB")
    print(f"  IMPL(BFS, Tortoise): {ID_MAP['917'][:20]}... → {ID_MAP['949'][:20]}...")

    api.add_operator("IMPL", [ID_MAP["915"], ID_MAP["915"]], ctx_wire, PROV,
                     content="IMPL(lifecycle, lifecycle) — lifecycle → gap in Tortoise approach")
    print(f"  IMPL(lifecycle): self-loop gap marker")  # noqa: F541
else:
    print("  Re-created operators already exist. Skipping.")

# ── Fix 1: Create 3 resolution events ──────────────────────────────────

print("\n=== Fix 1: Creating 3 resolution events ===")

ctx_res = "resolution-event"

if skip_fix1:
    print("  Skipping — resolution events already present.")
else:
    ev1 = api.add_point(
        "RESOLVED: Stream D shipped (#5350) — in-memory belief graph with BFS shock "
        "propagation, claim lifecycle (draft→live→superseded), subscriptions, and "
        "weighted edge ratio confidence. Shipped and tested. This is the operational "
        "baseline against which Tortoise grounding is compared.",
        ctx_res, PROV)

    ev2 = api.add_point(
        "RESOLVED: Tortoise LLM extraction now handles markdown/docs (#6679). "
        "Previously returned 0 points on documentation files — fixed. Evidence: "
        "PR merged on negation-game-explorations. This unblocks extracting knowledge "
        "from project docs for epistemic graph seeding.",
        ctx_res, PROV)

    ev3 = api.add_point(
        "RESOLVED: NAND edges now queryable as :NAND relationship type in FalkorDB (#6680). "
        "Evidence: commit 7645e52 merged. Operator Points with op_type=NAND/IMPL now "
        "have typed edges for graph traversal and grounding propagation.",
        ctx_res, PROV)

    print(f"  {ev1}")
    print(f"  {ev2}")
    print(f"  {ev3}")

# Wire resolution events → stream-d comparison Points via IMPL
# Guard: only if resolution event IDs are known and wiring doesn't exist
if not skip_fix1 or all([ev1, ev2, ev3]):
    resolution_points = [ev1, ev2, ev3]
    stream_d_targets = [
        "01KXH8WJP331KYC6PWE8TPCXNB",  # BFS vs PageRank comparison
        "01KXH8WJRECKX42BGYA4GTFECJ",  # Tortoise grounding comparison
        "01KXH8WJS0M01N6D9XJ5MKVJ2C",  # INSIGHT: complementary
        "01KXH8WJRQJQK1RAE19XP5BX9D",  # INSIGHT: Stream D right STARTING POINT
    ]

    # Check if wiring already exists
    existing_wiring = g.query(
        "MATCH (r:Point {context:'resolution-vector'}) WHERE r.content CONTAINS 'Resolution evidence supports' "
        "RETURN count(r)"
    ).result_set[0][0]

    if existing_wiring == 0:
        print("\nWiring resolution events → stream-d comparison Points:")
        for res_id in resolution_points:
            for tgt_id in stream_d_targets:
                api.add_operator("IMPL", [res_id, tgt_id], "resolution-vector",
                                 PROV, content="Resolution evidence supports comparison")
        print(f"  Created {len(resolution_points) * len(stream_d_targets)} IMPL edges")
    else:
        print(f"\nResolution wiring already exists ({existing_wiring} edges). Skipping.")


# ── Fix 3: compute_grounding() ─────────────────────────────────────────

print("\n=== Fix 3: Computing grounding ===")
grounding = proj.compute_grounding(lam=0.6)
grounded = {pid: v for pid, v in grounding.items() if v > 0}
print(f"  Points with grounding > 0: {len(grounded)}")

if grounded:
    ranked = sorted(grounded.items(), key=lambda x: -x[1])[:10]
    print("  Top grounding scores:")
    for pid, g_val in ranked:
        r = g.query("MATCH (n:Point {id:$id}) RETURN n.content, n.context",
                    params={"id": pid})
        content = r.result_set[0][0][:80] if r.result_set else "?"
        ctx = r.result_set[0][1] if r.result_set else "?"
        print(f"    [{g_val:.4f}] {pid[:20]}... ({ctx}): {content}")

# Verification queries
n_grounded = g.query("MATCH (n:Point) WHERE n.grounding > 0 RETURN count(n)").result_set[0][0]
n_total = g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]

# ponytail: single-line verification
nand_ct = g.query("MATCH ()-[r:NAND]->() RETURN count(r)").result_set[0][0]
impl_ct = g.query("MATCH ()-[r:IMPL]->() RETURN count(r)").result_set[0][0]
print(f"\n  State: {n_grounded}/{n_total} grounded, {nand_ct} NAND, {impl_ct} IMPL")

proj.close()

print("\n=== Done (#6704) ===")
