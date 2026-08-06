"""Graph audit — check Tortoise graph wiring quality.

Identifies: missing sourceKind, missing sourceDate, superseded gaps,
edge type errors, and mitigation opportunities.

Usage:
    from tortoise.audit import audit_graph
    result = audit_graph(proj, 'criteria')
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AuditIssue:
    issue_type: str        # missing_sourceKind, missing_sourceDate, superseded_no_edge, etc.
    severity: str          # high, medium, low
    node_id: str | None = None
    detail: str = ""
    fix: str = ""


@dataclass
class AuditResult:
    issues: list[AuditIssue] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0

    def high_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "high")

    def medium_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "medium")

    def low_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "low")


def audit_graph(proj, point_kinds: list[str] | None = None) -> AuditResult:
    """Audit graph wiring for the given pointKind(s).

    Args:
        proj: FalkorProjection instance
        point_kinds: Optional list of pointKind values to scope the audit.
                     If None, all Points are audited (no filter applied).
    """
    if point_kinds is None:
        kinds = []  # empty = audit all (no filter)
    else:
        kinds = point_kinds

    def _kinds_w(alias: str = "n") -> str:
        """Build pointKind WHERE clause for the given node alias, parenthesized."""
        if not kinds:
            return "TRUE"
        return f"{alias}.pointKind IN $kinds"

    def _op_in_kinds(alias: str = "op", tgt_alias: str = "tgt") -> str:
        """pointKind WHERE for operators — matches by own pointKind or target's."""
        if not kinds:
            return "TRUE"
        own = _kinds_w(alias)
        tgt_q = f"{tgt_alias}.pointKind IN $kinds"
        return f"({own} OR {tgt_q})"

    issues: list[AuditIssue] = []

    # ── 0. Count nodes/edges in scope ──────────────────────────────
    r = proj.g.query(f"MATCH (n:Point) WHERE {_kinds_w('n')} RETURN count(n)", params={"kinds": kinds})
    node_count = r.result_set[0][0] if r.result_set else 0

    # Count edges TO context-scoped points (correct traversal: operator -> point)
    r = proj.g.query(
        f"MATCH (n:Point) WHERE {_kinds_w('n')} "
        f"OPTIONAL MATCH (op:Point)-[e:IMPL|NAND]->(n) RETURN count(e)",
        params={"kinds": kinds},
    )
    edge_count = r.result_set[0][0] if r.result_set else 0

    # ── 1a. missing_sourceKind via operators (medium) ──────────────
    # Operators in scope connecting to evidence without source tier.
    # Match operators by own context (post-#130) OR target context (pre-#130 compat).
    r = proj.g.query(
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)\n"
        f"WHERE {_op_in_kinds('op', 'ev')}\n"
        "AND ev.sourceKind IS NULL AND (ev.is_operator IS NULL OR ev.is_operator = false)\n"
        "RETURN DISTINCT op.id, ev.id, ev.content LIMIT 50",
        params={"kinds": kinds},
    )
    for row in r.result_set:
        op_id, ev_id, ev_content = row[0], row[1], (row[2] or "")[:80]
        issues.append(AuditIssue(
            issue_type="missing_sourceKind",
            severity="medium",
            node_id=str(ev_id),
            detail=f"Evidence '{ev_content}' (from operator {op_id}) has no sourceKind",
            fix=f"tortoise_annotate_operator('{ev_id}', sourceKind='T4')",
        ))

    # ── 2. missing_sourceDate (low) ─────────────────────────────────
    r = proj.g.query(
        f"MATCH (ev:Point) WHERE {_kinds_w('ev')}\n"
        "AND ev.sourceKind IS NOT NULL AND ev.sourceDate IS NULL\n"
        "RETURN ev.id, ev.content, ev.sourceKind LIMIT 50",
        params={"kinds": kinds},
    )
    for row in r.result_set:
        ev_id, ev_content, sk = row[0], (row[1] or "")[:80], row[2]
        issues.append(AuditIssue(
            issue_type="missing_sourceDate",
            severity="low",
            node_id=str(ev_id),
            detail=f"'{ev_content}' has sourceKind={sk} but no sourceDate",
            fix=f"MATCH (n:Point {{id:'{ev_id}'}}) SET n.sourceDate = '2026-01-01'",
        ))

    # ── 3. superseded_no_edge (high) ──────────────────────────────
    r = proj.g.query(
        f"MATCH (n:Point) WHERE {_kinds_w('n')} AND n.status = 'superseded'\n"
        "OPTIONAL MATCH (n)-[s:SUPERSEDES]->(:Point)\n"
        "WITH n, s WHERE s IS NULL\n"
        "RETURN n.id, n.content LIMIT 50",
        params={"kinds": kinds},
    )
    for row in r.result_set:
        nid, content = row[0], (row[1] or "")[:80]
        issues.append(AuditIssue(
            issue_type="superseded_no_edge",
            severity="high",
            node_id=str(nid),
            detail=f"Superseded point '{content}' has no :SUPERSEDES edge",
            fix=f"MATCH (old:Point {{id:'{nid}'}}), (new:Point {{id:'<replacement>'}})\n"
                 "CREATE (old)-[:SUPERSEDES]->(new)",
        ))

    # ── 4. superseded_active_edges (medium) ──────────────────────
    r = proj.g.query(
        f"MATCH (sup:Point) WHERE {_kinds_w('sup')} AND sup.status = 'superseded'\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active:Point {status: 'live'})\n"
        "RETURN DISTINCT sup.id, sup.content, type(r), active.id LIMIT 50",
        params={"kinds": kinds},
    )
    for row in r.result_set:
        sup_id, sup_content, edge_type, active_id = row[0], (row[1] or "")[:80], row[2], row[3]
        issues.append(AuditIssue(
            issue_type="superseded_active_edges",
            severity="medium",
            node_id=str(sup_id),
            detail=f"Superseded '{sup_content}' has active {edge_type} edge from {active_id}",
            fix=f"Remove edges from '{sup_id}' or mark {active_id} as superseded too",
        ))

    # ── 5. impl_instead_of_nand (high) ──────────────────────────
    contradiction_keywords = ["not ", "fail", "cannot", "no ", "never", "impossible", "contradict"]
    seen_impl_nand = set()
    for kw in contradiction_keywords:
        # Match by source context OR by target context (operators may lack context pre-#130)
        r = proj.g.query(
            f"MATCH (src:Point)-[e:IMPL]->(tgt:Point)\n"
            f"WHERE {_op_in_kinds('src', 'tgt')}\n"
            f"AND toLower(tgt.content) CONTAINS '{kw}' AND tgt.is_operator IS NULL\n"
            "RETURN src.id, tgt.id, tgt.content LIMIT 20",
            params={"kinds": kinds},
        )
        for row in r.result_set:
            src_id, tgt_id, tgt_content = row[0], row[1], (row[2] or "")[:80]
            key = (str(src_id), str(tgt_id))
            if key not in seen_impl_nand:
                seen_impl_nand.add(key)
                issues.append(AuditIssue(
                    issue_type="impl_instead_of_nand",
                    severity="high",
                    node_id=str(src_id),
                    detail=f"IMPL edge {src_id} → '{tgt_content}' (keyword: '{kw}') — may need NAND",
                    fix=f"Verify semantic contradiction, then: tortoise_create_operator('NAND', '{src_id}', ['{tgt_id}'])",
                ))

    # ── 6. mitigation_recommended (medium) ──────────────────────
    r = proj.g.query(
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(tgt:Point)\n"
        f"WHERE {_op_in_kinds('op', 'tgt')} AND op.confidence <= 0.35\n"
        "OPTIONAL MATCH (tgt)<-[mit:mitigates]-(:Point)\n"
        "WITH op, tgt, mit WHERE mit IS NULL\n"
        "RETURN DISTINCT op.id, op.confidence, tgt.content LIMIT 50",
        params={"kinds": kinds},
    )
    for row in r.result_set:
        op_id, conf, tgt_content = row[0], row[1], (row[2] or "")[:80]
        issues.append(AuditIssue(
            issue_type="mitigation_recommended",
            severity="medium",
            node_id=str(op_id),
            detail=f"Low-confidence operator {op_id} (conf={conf}) → '{tgt_content}' has no mitigation",
            fix=f"tortoise_mitigate_operator('{op_id}', 'Relevant because...', confidence=0.7)",
        ))

    return AuditResult(
        issues=issues,
        node_count=node_count,
        edge_count=edge_count,
    )


def print_audit(result: AuditResult) -> None:
    """Pretty-print audit results."""
    print(f"\n{'='*60}")
    print(f"Tortoise Audit — {result.node_count} nodes, {result.edge_count} edges")
    print(f"Scope: {result.node_count} nodes, {result.edge_count} edges")
    print(f"Issues: {len(result.issues)} total "
          f"({result.high_count()} high, {result.medium_count()} medium, {result.low_count()} low)")
    print(f"{'='*60}")

    if not result.issues:
        print("\n✅ No issues found.")
        return

    for severity in ["high", "medium", "low"]:
        sev_issues = [i for i in result.issues if i.severity == severity]
        if not sev_issues:
            continue
        print(f"\n── {severity.upper()} ({len(sev_issues)}) ──")
        by_type: dict[str, list[AuditIssue]] = {}
        for iss in sev_issues:
            by_type.setdefault(iss.issue_type, []).append(iss)
        for itype, items in by_type.items():
            print(f"\n  [{itype}] — {len(items)} instances")
            for iss in items[:5]:
                print(f"    • {iss.detail[:120]}")
                if iss.fix:
                    print(f"      fix: {iss.fix[:120]}")
            if len(items) > 5:
                print(f"    … and {len(items) - 5} more")
