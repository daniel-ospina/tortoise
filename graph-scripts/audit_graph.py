#!/usr/bin/env python3.14
"""Tortoise graph audit — checks wiring quality per context."""
from falkordb import FalkorDB
import json, sys

DB = FalkorDB(host='localhost', port=6379, password='wz+D5kSSuwvRoO8tv+fyXGTz3XLH7XJV')
G = DB.select_graph('tortoise')

CONTEXTS = [
    'concept|operations|agent',
    'brain-research',
    'roadmap|H1|in_progress',
    'value-prop',
]

def q(cypher, **params):
    return G.query(cypher, params).result_set

def context_filter(context):
    """Match exact or contained contexts."""
    return f'(n.context = "{context}" OR n.context CONTAINS "{context}")'

def audit_context(context):
    print(f"\n{'='*60}")
    print(f"  AUDIT: {context}")
    print(f"{'='*60}")

    cf = context_filter(context)
    
    # ── 1. superseded_no_edge (HIGH) ──
    r = q(f"MATCH (n:Point) WHERE n.status = 'superseded' AND {cf} RETURN n.id, n.content, n.context")
    superseded = [(row[0], row[1], row[2]) for row in r]
    if superseded:
        print(f"\n  ⚠️  HIGH: superseded points ({len(superseded)})")
        for sid, content, ctx in superseded[:10]:
            edges = q(f"MATCH (n:Point {{id: '{sid}'}})-[:SUPERSEDES]->(m:Point) RETURN m.id")
            if not edges:
                print(f"    {sid} — no :SUPERSEDES edge → orphan")
                print(f"       content: {content[:120]}")
                print(f"       context: {ctx}")
    else:
        print(f"\n  ✅ superseded_no_edge: none")

    # ── 2. superseded_active_edges (MEDIUM) ──
    if superseded:
        active_count = 0
        for sid, _, _ in superseded:
            r = q(f"MATCH (n:Point {{id: '{sid}'}})-[e:IMPL|NAND]->(m:Point) RETURN count(e)")
            cnt = r[0][0] if r else 0
            if cnt > 0:
                active_count += 1
        if active_count:
            print(f"\n  ⚠️  MEDIUM: superseded_active_edges — {active_count} superseded points with active edges")
        else:
            print(f"  ✅ superseded_active_edges: none")
    else:
        print(f"  ✅ superseded_active_edges: N/A (no superseded points)")

    # ── 3. impl_instead_of_nand (HIGH) ──
    # Find NAND edges and check if endpoints have source_rationale suggesting contradiction
    r = q(f"MATCH (n:Point)-[:IMPL]->(m:Point) WHERE ({cf.replace('n.', 'n.')}) OR ({cf.replace('n.', 'm.')}) AND n.is_operator = 'True' RETURN n.id, n.op_type, m.id, m.content, n.content LIMIT 50")
    # Check if any IMPL connects to things that semantically should be NAND
    impl_issues = []
    for row in r:
        op_id, op_type, target_id, target_content, op_content = row
        # Heuristic: if target context contains 'contradiction', 'counter', 'nand', or 'adversarial'
        tc = (target_content or '').lower()
        if any(w in tc for w in ['contradict', 'counter', 'nand', 'adversarial', 'opposing']):
            impl_issues.append((op_id, op_type, target_id, target_content[:100]))
    if impl_issues:
        print(f"\n  ⚠️  HIGH: impl_instead_of_nand — {len(impl_issues)} suspicious IMPL edges")
        for op_id, ot, tid, tc in impl_issues[:5]:
            print(f"    {op_id} -[{ot}]-> {tid}")
            print(f"       target: {tc}")
    else:
        print(f"  ✅ impl_instead_of_nand: none")

    # ── 4. missing_sourceKind (MEDIUM) ──
    r = q(f"MATCH (n:Point) WHERE {cf} AND n.is_operator IS NULL AND n.sourceKind IS NULL AND n.pointKind IS NOT NULL RETURN count(n)")
    missing_sk = r[0][0] if r else 0
    r2 = q(f"MATCH (n:Point) WHERE {cf} AND n.sourceKind IS NULL RETURN count(n)")
    total_missing_sk = r2[0][0] if r2 else 0
    if total_missing_sk > 0:
        print(f"\n  ⚠️  MEDIUM: missing_sourceKind — {total_missing_sk} points (operators: {total_missing_sk - missing_sk}, evidence: {missing_sk})")
        # Show a sample
        r = q(f"MATCH (n:Point) WHERE {cf} AND n.sourceKind IS NULL RETURN n.id, n.content, n.context LIMIT 5")
        for row in r:
            print(f"    {row[0]} — {(row[1] or '')[:100]}")
    else:
        print(f"  ✅ missing_sourceKind: none")

    # ── 5. missing_sourceDate (LOW) ──
    r = q(f"MATCH (n:Point) WHERE {cf} AND n.sourceKind IS NOT NULL AND n.sourceDate IS NULL RETURN count(n)")
    missing_sd = r[0][0] if r else 0
    if missing_sd > 0:
        print(f"\n  ⚠️  LOW: missing_sourceDate — {missing_sd} graded evidence points without date")
    else:
        print(f"  ✅ missing_sourceDate: none")

    # ── 6. mitigation_recommended (MEDIUM) ──
    r = q(f"MATCH (n:Point)-[:mitigates]->(m:Point) WHERE {cf.replace('n.', 'n.')} RETURN count(n)")
    has_mitigations = r[0][0] if r else 0
    # Count low-relevance operators without mitigations
    r = q(f"MATCH (n:Point) WHERE {cf} AND (n.context CONTAINS 'low-relevance' OR n.context CONTAINS 'weak') AND n.is_operator = 'True' RETURN n.id, n.content, n.context LIMIT 10")
    low_rel = list(r)
    if low_rel:
        mitigated = 0
        for lid, _, _ in low_rel:
            r2 = q(f"MATCH (n:Point {{id: '{lid}'}})<-[:mitigates]-(m:Point) RETURN count(m)")
            if r2 and r2[0][0] > 0:
                mitigated += 1
        unmitigated = len(low_rel) - mitigated
        if unmitigated > 0:
            print(f"\n  ⚠️  MEDIUM: mitigation_recommended — {unmitigated}/{len(low_rel)} low-relevance operators unmitigated")
            for lid, content, ctx in low_rel[:3]:
                print(f"    {lid} — {(content or '')[:120]}")
        else:
            print(f"  ✅ mitigation_recommended: all mitigated")
    else:
        # Check across broader context — any operators with low confidence
        r = q(f"MATCH (n:Point) WHERE {cf} AND n.is_operator = 'True' AND (n.confidence < '0.4' OR n.confidence IS NULL) AND n.op_type = 'IMPL' RETURN n.id, n.confidence, n.content LIMIT 10")
        low_conf = list(r)
        if low_conf:
            print(f"\n  💡 MEDIUM: mitigation_recommended — {len(low_conf)} low-confidence operators might benefit")
            for lid, conf, content in low_conf[:3]:
                print(f"    {lid} conf={conf} — {(content or '')[:120]}")
        else:
            print(f"  ✅ mitigation_recommended: none")

    # ── Summary stats ──
    r = q(f"MATCH (n:Point) WHERE {cf} RETURN count(n)")
    total = r[0][0] if r else 0
    r = q(f"MATCH (n:Point) WHERE {cf} AND n.is_operator = 'True' RETURN count(n)")
    ops = r[0][0] if r else 0
    print(f"\n  📊 Summary: {total} points ({ops} operators) in context")


if __name__ == '__main__':
    for ctx in CONTEXTS:
        audit_context(ctx)
    
    print(f"\n{'='*60}")
    print(f"  CROSS-CONTEXT SUMMARY")
    print(f"{'='*60}")
    
    # Global stats
    r = q("MATCH (n:Point {status:'superseded'}) WHERE NOT (n)-[:SUPERSEDES]->() RETURN count(n)")
    global_superseded_no_edge = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.sourceKind IS NULL AND n.is_operator IS NULL AND n.pointKind IS NOT NULL RETURN count(n)")
    global_missing_sk = r[0][0] if r else 0
    r = q("MATCH (n:Point) WHERE n.sourceKind IS NOT NULL AND n.sourceDate IS NULL RETURN count(n)")
    global_missing_sd = r[0][0] if r else 0
    
    print(f"  🔴 HIGH: superseded_no_edge (global): {global_superseded_no_edge}")
    print(f"  🟡 MEDIUM: missing_sourceKind (global): {global_missing_sk}")
    print(f"  ⚪ LOW: missing_sourceDate (global): {global_missing_sd}")
