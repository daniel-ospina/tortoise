#!/usr/bin/env python3
"""Merge endometriosis_melasma → endometriosis_melasma_ep, then delete source graph."""
from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=16379)
src = db.select_graph("endometriosis_melasma")
dst = db.select_graph("endometriosis_melasma_ep")

# ── Pre-merge snapshot ──────────────────────────────────────────
pre_dst_nodes = dst.query("MATCH (n) RETURN count(n)").result_set[0][0]
pre_dst_rels = dst.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
print(f"Before: dst={pre_dst_nodes} nodes, {pre_dst_rels} rels")

src_nodes = src.query("MATCH (n) RETURN count(n)").result_set[0][0]
src_rels = src.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
print(f"Source: {src_nodes} nodes, {src_rels} rels")

# ── Copy nodes ──────────────────────────────────────────────────
nodes = src.query("MATCH (n) RETURN properties(n), labels(n)").result_set
copied_nodes = 0
for props, labels in nodes:
    clean = {}
    for k, v in props.items():
        if v is not None and str(v).strip():
            clean[k] = v
    if not clean:
        continue

    label_str = ":".join(labels)
    # Build CREATE safely with inline values (small graph, no injection risk)
    setters = []
    for k, v in clean.items():
        if isinstance(v, (int, float)):
            setters.append(f"n.{k} = {v}")
        elif isinstance(v, bool):
            setters.append(f"n.{k} = {str(v).lower()}")
        else:
            escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
            setters.append(f"n.{k} = '{escaped}'")
    set_clause = ", ".join(setters)
    dst.query(f"CREATE (n:{label_str}) SET {set_clause}")
    copied_nodes += 1
print(f"Copied nodes: {copied_nodes}")

# ── Copy relationships ──────────────────────────────────────────
rels = src.query(
    "MATCH (a)-[r]->(b) RETURN a.name, b.name, type(r), properties(r)"
).result_set
copied_rels = 0
for a_name, b_name, rtype, rprops in rels:
    if not a_name or not b_name:
        continue
    a_name = str(a_name).replace("\\", "\\\\").replace("'", "\\'")
    b_name = str(b_name).replace("\\", "\\\\").replace("'", "\\'")

    clean_rp = {}
    for k, v in (rprops or {}).items():
        if v is not None and str(v).strip():
            clean_rp[k] = v

    if clean_rp:
        rset = []
        for k, v in clean_rp.items():
            escaped = str(v).replace("\\", "\\\\").replace("'", "\\'")
            rset.append(f"r.{k} = '{escaped}'")
        dst.query(
            f"MATCH (a {{name: '{a_name}'}}), (b {{name: '{b_name}'}}) "
            f"MERGE (a)-[r:{rtype}]->(b) SET {', '.join(rset)}"
        )
    else:
        dst.query(
            f"MATCH (a {{name: '{a_name}'}}), (b {{name: '{b_name}'}}) "
            f"MERGE (a)-[r:{rtype}]->(b)"
        )
    copied_rels += 1
print(f"Copied relationships: {copied_rels}")

# ── Post-merge verification ─────────────────────────────────────
post_nodes = dst.query("MATCH (n) RETURN count(n)").result_set[0][0]
post_rels = dst.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
print(f"After: dst={post_nodes} nodes, {post_rels} rels")
print(f"Expected: {pre_dst_nodes + copied_nodes} nodes, {pre_dst_rels + copied_rels} rels")

if post_nodes == pre_dst_nodes + copied_nodes:
    print("✅ Node count matches — merge successful")
else:
    print(f"⚠️ Node count mismatch: expected {pre_dst_nodes + copied_nodes}, got {post_nodes}")

if post_rels == pre_dst_rels + copied_rels:
    print("✅ Relationship count matches — merge successful")
else:
    print(f"⚠️ Rel count mismatch: expected {pre_dst_rels + copied_rels}, got {post_rels}")

# ── Label breakdown ─────────────────────────────────────────────
labels = dst.query(
    "MATCH (n) RETURN labels(n)[0] as label, count(n) as cnt ORDER BY cnt DESC"
).result_set
print("\nFinal labels:")
for l, c in labels[:15]:  # noqa: E741
    print(f"  {l}: {c}")
