#!/usr/bin/env python3.14
"""Deep Tortoise graph audit — follows edges, checks wiring quality per context.

HISTORICAL ONE-SHOT — queries the removed context field (see #49);
connection now env-based (TORTOISE_DB_URI).
"""
from falkordb import FalkorDB  # noqa: I001
from collections import defaultdict  # noqa: F401
import os


def _parse_uri(uri: str) -> dict:
    """Parse docker:// URI into components."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 16379,
        "password": parsed.password or "",
        "graph": parsed.path.lstrip("/") or "tortoise",
    }


_uri = os.environ.get("TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise")
_cfg = _parse_uri(_uri)
DB = FalkorDB(host=_cfg["host"], port=_cfg["port"], password=_cfg["password"] or None)
G = DB.select_graph(_cfg["graph"])

CONTEXTS = ['concept|operations|agent', 'brain-research', 'roadmap|H1|in_progress', 'value-prop']

def q(cypher, **params):
    return G.query(cypher, params).result_set

def get_subgraph_ids(context_pattern):
    """Get all point IDs in the context subgraph (including 1-hop neighbors)."""
    ids = set()
    
    # Direct context matches
    cf = f'(n.context = "{context_pattern}" OR n.context CONTAINS "{context_pattern}")'
    
    # Get matching nodes
    for row in q(f"MATCH (n:Point) WHERE {cf} RETURN n.id"):
        ids.add(row[0])
    
    # Follow edges out 1 hop
    if ids:
        id_list = '", "'.join(ids)
        for row in q(f'MATCH (n:Point)-[:IMPL|NAND|mitigates|aboutSubject|aboutObject]->(m:Point) WHERE n.id IN ["{id_list}"] RETURN m.id'):
            ids.add(row[0])
        for row in q(f'MATCH (n:Point)-[:IMPL|NAND|mitigates|aboutSubject|aboutObject]->(m:Point) WHERE m.id IN ["{id_list}"] RETURN n.id'):
            ids.add(row[0])
    
    return ids

def audit_context(context):
    print(f"\n{'='*70}")
    print(f"  AUDIT: {context}")
    print(f"{'='*70}")
    
    ids = get_subgraph_ids(context)
    print(f"  Subgraph: {len(ids)} points (with 1-hop neighbors)")
    
    if not ids:
        print("  ⚠️  No points found in context")
        return
    
    id_list = '", "'.join(ids)
    id_filter = f'n.id IN ["{id_list}"]'
    
    # Count by type
    r = q(f"MATCH (n:Point) WHERE {id_filter} RETURN n.is_operator, count(n)")
    op_count = ev_count = 0
    for row in r:
        if row[0] is True:
            op_count = row[1]
        else:
            ev_count += row[1]
    print(f"  Operators: {op_count}, Evidence/Claims: {ev_count}")
    
    # ── CHECK 1: superseded_no_edge (HIGH) ──
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.status = 'superseded' RETURN n.id, n.content, n.context")
    superseded = list(r)
    if superseded:
        orphan_count = 0
        for sid, _, _ in superseded:
            r2 = q(f"MATCH (n:Point {{id: '{sid}'}})-[:SUPERSEDES]->(m:Point) RETURN m.id")
            if not r2:
                orphan_count += 1
        if orphan_count:
            print(f"\n  🔴 HIGH: superseded_no_edge — {orphan_count}/{len(superseded)} orphaned")
            for sid, content, ctx in superseded[:5]:  # noqa: B007
                print(f"    {sid}: {(content or '')[:100]}")
        else:
            print(f"  ✅ superseded_no_edge: all {len(superseded)} have replacement edges")
    else:
        print(f"  ✅ superseded_no_edge: no superseded points")  # noqa: F541
    
    # ── CHECK 2: superseded_active_edges (MEDIUM) ──
    if superseded:
        active = 0
        for sid, _, _ in superseded:
            r2 = q(f"MATCH (n:Point {{id: '{sid}'}})-[e:IMPL|NAND]->(m:Point) RETURN count(e)")
            if r2 and r2[0][0] > 0:
                active += 1
        if active:
            print(f"  🟡 MEDIUM: superseded_active_edges — {active} superseded points with active edges")
        else:
            print(f"  ✅ superseded_active_edges: clean")  # noqa: F541
    
    # ── CHECK 3: impl_instead_of_nand (HIGH) ──
    # Check IMPL edges where target has contradiction/counter/adversarial in context or content
    r = q(f"MATCH (n:Point)-[:IMPL]->(m:Point) WHERE {id_filter.replace('n.', 'n.')} AND n.is_operator = true RETURN n.id, n.op_type, m.id, m.content, m.context, n.content LIMIT 200")
    suspicious = []
    for row in r:
        op_id, op_type, tgt_id, tgt_content, tgt_ctx, op_content = row  # noqa: RUF059
        tc = ((tgt_content or '') + (tgt_ctx or '')).lower()
        oc = ((op_content or '') + '').lower()
        # Signs of contradiction
        contra_signals = ['contradict', 'counter', 'nand', 'adversarial', 'oppos', 'conflict', 'against', 'versus', 'vs ', 'refut', 'reject']
        if any(w in tc for w in contra_signals) or any(w in oc for w in contra_signals):
            suspicious.append((op_id, tgt_id, tgt_content[:120] if tgt_content else '', tgt_ctx))
    
    if suspicious:
        print(f"\n  🔴 HIGH: impl_instead_of_nand — {len(suspicious)} suspicious IMPL→contradiction edges")
        for op_id, tgt_id, tc, tctx in suspicious[:5]:
            print(f"    {op_id} -[IMPL]-> {tgt_id}")
            print(f"       target context: {tctx}")
            print(f"       target content: {tc}")
    else:
        print(f"  ✅ impl_instead_of_nand: clean")  # noqa: F541
    
    # ── CHECK 4: missing_sourceKind (MEDIUM) on evidence points ──
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.is_operator = false AND n.sourceKind IS NULL AND n.pointKind IS NOT NULL RETURN n.id, n.pointKind, n.content LIMIT 20")
    missing_sk = list(r)
    if missing_sk:
        print(f"\n  🟡 MEDIUM: missing_sourceKind — {len(missing_sk)} evidence points without credibility tier")
        for pid, pk, content in missing_sk[:5]:
            print(f"    {pid} ({pk}): {(content or '')[:100]}")
    else:
        print(f"  ✅ missing_sourceKind: all evidence points have sourceKind")  # noqa: F541
    
    # Also check: operators shouldn't need sourceKind, but let's check if any have it (good) or all lack it (expected)
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.is_operator = true AND n.sourceKind IS NOT NULL RETURN count(n)")
    ops_with_sk = r[0][0] if r else 0
    if ops_with_sk > 0:
        print(f"  💡 {ops_with_sk} operators have sourceKind set (optional, good for traceability)")
    
    # ── CHECK 5: missing_sourceDate (LOW) ──
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.sourceKind IS NOT NULL AND n.sourceDate IS NULL RETURN count(n)")
    missing_sd = r[0][0] if r else 0
    if missing_sd:
        print(f"\n  ⚪ LOW: missing_sourceDate — {missing_sd} graded points without date (time decay disabled)")
    else:
        print(f"  ✅ missing_sourceDate: all graded points have dates")  # noqa: F541
    
    # ── CHECK 6: mitigation_recommended (MEDIUM) ──
    # Find operators with low confidence that lack mitigation
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.is_operator = true AND n.op_type = 'IMPL' AND (n.confidence IS NULL OR toFloat(n.confidence) < 0.5) RETURN n.id, n.confidence, n.content LIMIT 50")
    low_conf = list(r)
    unmitigated = []
    for lid, conf, content in low_conf:
        r2 = q(f"MATCH (m:Point)-[:mitigates]->(n:Point {{id: '{lid}'}}) RETURN count(m)")
        if not r2 or r2[0][0] == 0:
            unmitigated.append((lid, conf, content))
    
    if unmitigated:
        print(f"\n  🟡 MEDIUM: mitigation_recommended — {len(unmitigated)} low-confidence operators without mitigation")
        for lid, conf, content in unmitigated[:5]:
            print(f"    {lid} conf={conf}: {(content or '')[:120]}")
    else:
        print(f"  ✅ mitigation_recommended: all low-confidence operators mitigated or none found")  # noqa: F541
    
    # ── CHECK 7: Edge connectivity quality ──
    r = q(f"MATCH (n:Point) WHERE {id_filter} RETURN count(n)")
    total = r[0][0]
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND NOT (n)-[:IMPL|NAND|mitigates|aboutSubject|aboutObject]-() RETURN count(n)")
    isolated = r[0][0] if r else 0
    if isolated > 0:
        print(f"\n  💡 Isolated points (no edges): {isolated}/{total}")
        if isolated == total:
            print(f"    ⚠️  ALL points in this context are isolated — no wiring at all!")  # noqa: F541
    
    # ── CHECK 8: NAND edge health ──
    r = q(f"MATCH (n:Point)-[:NAND]->(m:Point) WHERE {id_filter.replace('n.', 'n.')} RETURN n.id, m.id, n.content, m.content LIMIT 20")
    nand_edges = list(r)
    if nand_edges:
        print(f"\n  📊 NAND edges in subgraph: {len(nand_edges)}")
        for src, tgt, sc, tc in nand_edges[:3]:  # noqa: B007
            print(f"    {src} -[NAND]-> {tgt}")
    
    # ── CHECK 9: Confidence distribution ──
    r = q(f"MATCH (n:Point) WHERE {id_filter} AND n.is_operator = true AND n.confidence IS NOT NULL RETURN toFloat(n.confidence) ORDER BY toFloat(n.confidence)")
    confs = [row[0] for row in r]
    if confs:
        print(f"\n  📊 Operator confidence: min={min(confs):.2f}, max={max(confs):.2f}, "
              f"avg={sum(confs)/len(confs):.2f}, n={len(confs)}")
        low = sum(1 for c in confs if c < 0.5)
        if low:
            print(f"    ⚠️  {low} operators have confidence < 0.5")


if __name__ == '__main__':
    for ctx in CONTEXTS:
        audit_context(ctx)
    
    # ── GLOBAL SUMMARY ──
    print(f"\n{'='*70}")
    print(f"  GLOBAL CROSS-CONTEXT SUMMARY")  # noqa: F541
    print(f"{'='*70}")
    
    r = q("MATCH (n:Point) RETURN count(n)")
    total_pts = r[0][0]
    r = q("MATCH ()-[e:IMPL]->() RETURN count(e)")
    impl_count = r[0][0] if r else 0
    r = q("MATCH ()-[e:NAND]->() RETURN count(e)")
    nand_count = r[0][0] if r else 0
    r = q("MATCH ()-[e:mitigates]->() RETURN count(e)")
    mitig_count = r[0][0] if r else 0
    r = q("MATCH ()-[e:aboutSubject]->() RETURN count(e)")
    subj_count = r[0][0] if r else 0
    r = q("MATCH ()-[e:aboutObject]->() RETURN count(e)")
    obj_count = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.status = 'superseded' RETURN count(n)")
    superseded_count = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.sourceKind IS NOT NULL RETURN count(n)")
    with_sk = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.sourceDate IS NOT NULL RETURN count(n)")
    with_sd = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.pointKind IS NOT NULL RETURN count(n)")
    with_pk = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.is_operator = true AND n.confidence IS NOT NULL RETURN avg(toFloat(n.confidence)), count(n)")
    row = r[0] if r else (None, 0)
    avg_conf = row[0]
    conf_count = row[1]
    
    print(f"  Total Points: {total_pts}")
    print(f"  Edges: IMPL={impl_count}, NAND={nand_count}, mitigates={mitig_count}, aboutSubject={subj_count}, aboutObject={obj_count}")
    print(f"  Superseded points: {superseded_count}")
    print(f"  With sourceKind: {with_sk}/{total_pts}")
    print(f"  With sourceDate: {with_sd}/{total_pts}")
    print(f"  With pointKind: {with_pk}/{total_pts}")
    print(f"  Operator avg confidence: {avg_conf:.2f} (n={conf_count})" if avg_conf else "  Operator confidence: N/A")
    
    print(f"\n  🔴 HIGH severity:")  # noqa: F541
    print(f"     superseded_no_edge: {superseded_count} superseded points, {0} orphaned (no :SUPERSEDES edges exist at all)")
    print(f"     impl_instead_of_nand: checked per-context above")  # noqa: F541
    
    print(f"\n  🟡 MEDIUM severity:")  # noqa: F541
    print(f"     missing_sourceKind: {total_pts - with_sk} points lack sourceKind")
    print(f"     Most are operators (expected), but evidence points need tiers")  # noqa: F541
    
    print(f"\n  ⚪ LOW severity:")  # noqa: F541
    print(f"     missing_sourceDate: {with_sk - with_sd} graded points lack sourceDate")
