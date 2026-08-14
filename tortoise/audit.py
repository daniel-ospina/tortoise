"""Graph audit — check Tortoise graph wiring quality.

Identifies: missing sourceKind (point-level legacy + Source-level canonical),
missing sourceDate, superseded gaps (missing CORRECTS edge), live IMPL/NAND
edges into superseded points, naive-IMPL heuristics, missing mitigations, and
legacy ``mitigates`` edges.

Ontology (docs/ONTOLOGY.md §5 edge vocabulary):
  - Operators write ``(op:Point {is_operator:true})-[:IMPL|NAND]->(tgt)``.
  - Mitigations write ``(op)-[:mitigated_by]->(m)`` with the mitigation Point
    back-linking ``-[:IMPL]->`` the operator (sdk.mitigate_operator). The
    legacy graph-scripts-era edge was ``mitigates`` — audited as a separate
    legacy check, never merged into the canonical predicate.
  - Supersession writes ``(new)-[:CORRECTS]->(old)`` (supersede_point /
    invalidate_point); the graph-scripts-era ``SUPERSEDES`` edge is not a
    current write path.
  - Source tiering lives on Source nodes (``sourceKind`` / ``credibilityTier``
    — resolve_tier: explicit tier > sourceKind tier-form > registry default).
    Point-level ``sourceKind`` is legacy (#398).

Usage:
    from tortoise.audit import audit_graph
    result = audit_graph(proj, 'criteria')
    result.to_dict()   # structured JSON (SDK audit() return value)

Every check computes an UNCAPped count plus a capped sample list — counts are
totals, samples are representative (#348: the previous LIMIT-capped issues
were samples masquerading as totals). EXCEPT check 5 (impl_instead_of_nand):
its count is a fetch-capped UPPER BOUND, flagged "capped": true in the report
so consumers never read it as an exact total (#1258 conf 58).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Sample cap per check. COUNTS are computed uncapped; samples are capped so a
# large violations set does not balloon the JSON payload.
SAMPLE_LIMIT = 50

# Check 5 heuristic fetch bound: keyword candidates are fetched capped and
# word-boundary filtered Python-side (the embedded Cypher engine has no regex
# support). This is a LOW-severity advisory check — the documented residual
# cap is deliberate (see the check docstring).
CHECK5_FETCH_LIMIT = 10000

# Check 5 contradiction keywords — word-boundary matched against target
# content. Advisory only: this check NEVER auto-converts IMPL→NAND (a false
# NAND rewires belief propagation, NAND_BASE_WEIGHT=8.0). "no" is deliberately
# absent (too common a word — the old 'no ' substring hack flagged 'tornado ').
CONTRADICTION_KEYWORDS = ("not", "fail", "cannot", "never", "impossible", "contradict")

# Low-confidence operator threshold for mitigation_recommended (check 6).
MITIGATION_CONF_THRESHOLD = 0.35

SEVERITY_ORDER = ("high", "medium", "low")


@dataclass
class AuditIssue:
    issue_type: str        # check id, e.g. "missing_sourceKind"
    severity: str          # high | medium | low
    node_id: str | None = None
    detail: str = ""
    fix: str = ""
    legacy: bool = False   # legacy-era artifact (informational, not current-ontology)


@dataclass
class AuditResult:
    issues: list[AuditIssue] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    # check id → total violations (uncapped). `issues` holds capped samples.
    check_counts: dict[str, int] = field(default_factory=dict)
    # check id → legacy marker (legacy checks are informational).
    check_legacy: dict[str, bool] = field(default_factory=dict)
    # check id → count is UPPER-BOUNDED (fetch-capped), not a true total.
    # Consumers must not treat it as an exact violation count (#1258 conf 58).
    check_capped: dict[str, bool] = field(default_factory=dict)

    def high_count(self) -> int:
        return self._sev_total("high")

    def medium_count(self) -> int:
        return self._sev_total("medium")

    def low_count(self) -> int:
        return self._sev_total("low")

    def _sev_total(self, severity: str) -> int:
        """Uncapped severity totals when check_counts are present (fallback:
        sample counts for hand-built results)."""
        if not self.check_counts:
            return sum(1 for i in self.issues if i.severity == severity)
        sev_of: dict[str, str] = {}
        for iss in self.issues:
            sev_of.setdefault(iss.issue_type, iss.severity)
        return sum(
            c for cid, c in self.check_counts.items()
            if sev_of.get(cid) == severity
        )

    @property
    def exit_code(self) -> int:
        """0 = clean, 1 = issues found (check-consistency precedent: any
        inconsistency fails; severity does not escalate the code)."""
        if self.check_counts:
            return 0 if all(c == 0 for c in self.check_counts.values()) else 1
        return 0 if not self.issues else 1

    def to_dict(self) -> dict:
        """Structured JSON report — the SDK audit() return value.

        Per check: {id, severity, count (true total unless "capped": true —
        then an upper-bounded fetch sample), legacy, samples (capped)}.
        summary holds totals; exit_code = 0 clean / 1 issues.
        """
        by_type: dict[str, list[AuditIssue]] = {}
        for iss in self.issues:
            by_type.setdefault(iss.issue_type, []).append(iss)
        checks: list[dict] = []
        for itype, items in sorted(
            by_type.items(),
            key=lambda kv: (
                SEVERITY_ORDER.index(kv[1][0].severity)
                if kv[1][0].severity in SEVERITY_ORDER else len(SEVERITY_ORDER),
                kv[0],
            ),
        ):
            checks.append({
                "id": itype,
                "severity": items[0].severity,
                "count": self.check_counts.get(itype, len(items)),
                "legacy": bool(self.check_legacy.get(itype, False)),
                # conf 58: check-5's count is an upper-bounded fetch sample —
                # flagged so consumers never read it as an exact total.
                "capped": bool(self.check_capped.get(itype, False)),
                "samples": [
                    {"node_id": i.node_id, "detail": i.detail, "fix": i.fix}
                    for i in items[:SAMPLE_LIMIT]
                ],
            })
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "checks": checks,
            "summary": {
                "total_issues": self.total_count(),
                "high": self.high_count(),
                "medium": self.medium_count(),
                "low": self.low_count(),
                "clean": self.total_count() == 0,
            },
            "exit_code": self.exit_code,
        }

    def total_count(self) -> int:
        """Uncapped total violations (sum of per-check counts)."""
        if self.check_counts:
            return sum(self.check_counts.values())
        return len(self.issues)

    @classmethod
    def from_dict(cls, d: dict) -> "AuditResult":
        """Rebuild an AuditResult from to_dict() output (CLI human-mode path)."""
        issues: list[AuditIssue] = []
        check_counts: dict[str, int] = {}
        check_legacy: dict[str, bool] = {}
        check_capped: dict[str, bool] = {}
        for ch in d.get("checks", []):
            cid = ch["id"]
            check_counts[cid] = ch.get("count", len(ch.get("samples", [])))
            check_legacy[cid] = bool(ch.get("legacy", False))
            check_capped[cid] = bool(ch.get("capped", False))
            for s in ch.get("samples", []):
                issues.append(AuditIssue(
                    issue_type=cid,
                    severity=ch.get("severity", "low"),
                    node_id=s.get("node_id"),
                    detail=s.get("detail", ""),
                    fix=s.get("fix", ""),
                    legacy=bool(ch.get("legacy", False)),
                ))
        return cls(
            issues=issues,
            node_count=d.get("node_count", 0),
            edge_count=d.get("edge_count", 0),
            check_counts=check_counts,
            check_legacy=check_legacy,
            check_capped=check_capped,
        )


def _count(proj, query: str, params: dict | None = None) -> int:
    r = proj.g.query(query, params=params or {})
    return r.result_set[0][0] if r.result_set else 0


def _rows(proj, query: str, params: dict | None = None) -> list:
    r = proj.g.query(query, params=params or {})
    return r.result_set if r else []


def audit_graph(proj, point_kinds: list[str] | None = None) -> AuditResult:
    """Audit graph wiring for the given pointKind(s).

    Args:
        proj: FalkorProjection instance
        point_kinds: Optional list of pointKind values to scope the audit.
                     If None, all Points are audited (no filter applied).
    """
    kinds = point_kinds or []

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

    def _superseded_w(alias: str = "n") -> str:
        """Supersession filter: superseded/outdated status OR the legacy
        outdated=true flag (invalidate_point keeps the original status)."""
        return (f"({alias}.status IN ['superseded', 'outdated'] "
                f"OR {alias}.outdated = true)")

    params = {"kinds": kinds}
    issues: list[AuditIssue] = []
    check_counts: dict[str, int] = {}
    check_legacy: dict[str, bool] = {}
    check_capped: dict[str, bool] = {}

    def _record(check_id: str, new_issues: list[AuditIssue], count: int,
                legacy: bool = False, capped: bool = False) -> None:
        issues.extend(new_issues)
        check_counts[check_id] = count
        check_legacy[check_id] = legacy
        check_capped[check_id] = capped

    # ── 0. Count nodes/edges in scope ──────────────────────────────
    node_count = _count(
        proj, f"MATCH (n:Point) WHERE {_kinds_w('n')} RETURN count(n)",
        params=params,
    )
    # Count edges TO context-scoped points (correct traversal: operator -> point)
    edge_count = _count(
        proj,
        f"MATCH (n:Point) WHERE {_kinds_w('n')} "
        f"OPTIONAL MATCH (op:Point)-[e:IMPL|NAND]->(n) RETURN count(e)",
        params=params,
    )

    # ── 1. missing_sourceKind — point-level (LEGACY, low) ───────────
    # Point-level sourceKind is a legacy annotation (#398) — the canonical
    # check is the Source-level variant (check 7). Kept so legacy-era graphs
    # surface the artifact; LOW severity + legacy marker, never a current-
    # ontology violation.
    count1 = _count(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} AND ev.sourceKind IS NULL "
        "AND ev.is_operator = false\n"
        "WITH DISTINCT op, ev\n"
        "RETURN count(*)",
        params=params,
    )
    rows1 = _rows(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} AND ev.sourceKind IS NULL "
        "AND ev.is_operator = false\n"
        f"RETURN DISTINCT op.id, ev.id, ev.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("missing_sourceKind", [
        AuditIssue(
            issue_type="missing_sourceKind",
            severity="low",
            node_id=str(ev_id),
            detail=(f"Evidence '{ev_content}' (from operator {op_id}) has no "
                    "point-level sourceKind [legacy annotation]"),
            # Point-level sourceKind is legacy — tier the SOURCE node instead
            # (#398); see the missing_sourceKind_source check (7).
            fix=("tier the SOURCE node backing this point via "
                 "tortoise_set_source_tier(url, 'T0'..'T4') or "
                 "create_source(url, kind, tier=...)"),
            legacy=True,
        )
        for op_id, ev_id, ev_content in rows1
    ], count1, legacy=True)

    # ── 2. missing_sourceDate (low) ─────────────────────────────────
    count2 = _count(
        proj,
        f"MATCH (ev:Point) WHERE {_kinds_w('ev')} "
        "AND ev.sourceKind IS NOT NULL AND ev.sourceDate IS NULL\n"
        "RETURN count(ev.id)",
        params=params,
    )
    rows2 = _rows(
        proj,
        f"MATCH (ev:Point) WHERE {_kinds_w('ev')} "
        "AND ev.sourceKind IS NOT NULL AND ev.sourceDate IS NULL\n"
        f"RETURN ev.id, ev.content, ev.sourceKind LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("missing_sourceDate", [
        AuditIssue(
            issue_type="missing_sourceDate",
            severity="low",
            node_id=str(ev_id),
            detail=f"'{ev_content}' has sourceKind={sk} but no sourceDate",
            fix=f"MATCH (n:Point {{id:'{ev_id}'}}) SET n.sourceDate = '2026-01-01'",
        )
        for ev_id, ev_content, sk in rows2
    ], count2)

    # ── 3. superseded_no_edge (high) ──────────────────────────────
    # Supersession writes (new)-[:CORRECTS]->(old); a superseded/outdated
    # point with no incoming CORRECTS edge is an orphaned replacement gap.
    # (graph-scripts-era SUPERSEDES edges are NOT a current write path.)
    sup3_w = f"({_kinds_w('n')} AND {_superseded_w('n')})"
    count3 = _count(
        proj,
        f"MATCH (n:Point) WHERE {sup3_w}\n"
        "OPTIONAL MATCH (repl:Point)-[:CORRECTS]->(n)\n"
        "WITH n, repl WHERE repl IS NULL\n"
        "RETURN count(n.id)",
        params=params,
    )
    rows3 = _rows(
        proj,
        f"MATCH (n:Point) WHERE {sup3_w}\n"
        "OPTIONAL MATCH (repl:Point)-[:CORRECTS]->(n)\n"
        "WITH n, repl WHERE repl IS NULL\n"
        f"RETURN n.id, n.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("superseded_no_edge", [
        AuditIssue(
            issue_type="superseded_no_edge",
            severity="high",
            node_id=str(nid),
            detail=f"Superseded point '{content}' has no :CORRECTS edge from its replacement",
            fix=(f"Link the replacement: tortoise_supersede('{nid}', '<new_id>') "
                 f"or MATCH (old:Point {{id:'{nid}'}}), "
                 f"(new:Point {{id:'<replacement>'}}) CREATE (new)-[:CORRECTS]->(old)"),
        )
        for nid, content in rows3
    ], count3)

    # ── 4. superseded_active_edges (medium) ──────────────────────
    # Live (or legacy status-null) points still wiring IMPL/NAND INTO a
    # superseded point — supersede_point transfers edges to the replacement;
    # survivors are dangling wiring.
    sup4_w = f"({_kinds_w('sup')} AND {_superseded_w('sup')})"
    count4 = _count(
        proj,
        f"MATCH (sup:Point) WHERE {sup4_w}\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active:Point) "
        "WHERE (active.status IS NULL OR active.status = 'live')\n"
        "RETURN count(DISTINCT r)",
        params=params,
    )
    rows4 = _rows(
        proj,
        f"MATCH (sup:Point) WHERE {sup4_w}\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active:Point) "
        "WHERE (active.status IS NULL OR active.status = 'live')\n"
        f"RETURN DISTINCT sup.id, sup.content, type(r), active.id LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("superseded_active_edges", [
        AuditIssue(
            issue_type="superseded_active_edges",
            severity="medium",
            node_id=str(sup_id),
            detail=(f"Superseded '{sup_content}' has active {edge_type} edge "
                    f"from {active_id}"),
            fix=f"Remove edges into '{sup_id}' or mark {active_id} as superseded too",
        )
        for sup_id, sup_content, edge_type, active_id in rows4
    ], count4)

    # ── 5. impl_instead_of_nand (low, advisory heuristic) ──────────
    # Substring keyword matching produced false positives ("no " in
    # "tornado") at HIGH severity and incentivized false IMPL→NAND fixes
    # (NAND_BASE_WEIGHT=8.0 rewires belief propagation). Demoted to a LOW
    # advisory heuristic: word-boundary matched (Python-side — the embedded
    # Cypher engine has no regex), advisory-only fix string. Unit = distinct
    # (src, tgt) pairs.
    kw_clauses = " OR ".join(
        f"toLower(tgt.content) CONTAINS '{kw}'" for kw in CONTRADICTION_KEYWORDS
    )
    rows5 = _rows(
        proj,
        f"MATCH (src:Point)-[e:IMPL]->(tgt:Point)\n"
        f"WHERE {_op_in_kinds('src', 'tgt')} AND ({kw_clauses}) "
        "AND tgt.is_operator = false\n"
        f"RETURN src.id, tgt.id, tgt.content LIMIT {CHECK5_FETCH_LIMIT}",
        params=params,
    )
    patterns5 = [re.compile(rf"\b{re.escape(kw)}\b") for kw in CONTRADICTION_KEYWORDS]
    issues5: list[AuditIssue] = []
    seen5: set[tuple[str, str]] = set()
    count5 = 0
    for src_id, tgt_id, tgt_content in rows5:
        low = (tgt_content or "").lower()
        hit = next(
            (kw for kw, pat in zip(CONTRADICTION_KEYWORDS, patterns5) if pat.search(low)),
            None,
        )
        if hit is None:
            continue
        key = (str(src_id), str(tgt_id))
        if key in seen5:
            continue
        seen5.add(key)
        count5 += 1
        if len(issues5) < SAMPLE_LIMIT:
            issues5.append(AuditIssue(
                issue_type="impl_instead_of_nand",
                severity="low",
                node_id=str(src_id),
                detail=(f"IMPL edge {src_id} → '{str(tgt_content)[:80]}' "
                        f"(keyword: '{hit}') — verify whether NAND applies"),
                fix=f"Verify semantic contradiction BEFORE converting — if "
                    f"confirmed: tortoise_create_operator('NAND', '{src_id}', "
                    f"['{tgt_id}'])",
            ))
    # conf 58: count5 is an UPPER-BOUNDED fetch sample (LIMIT
    # CHECK5_FETCH_LIMIT candidates, word-boundary filtered Python-side) — not
    # a true total; flagged so summary/exit-code consumers read it as a bound.
    _record("impl_instead_of_nand", issues5, count5, capped=True)

    # ── 6. mitigation_recommended (medium) ──────────────────────
    # Canonical predicate is (op)-[:mitigated_by]->(m) — OUTBOUND from the
    # operator (sdk.mitigate_operator). The old `(tgt)<-[mit:mitigates]-()`
    # traversal could never see an SDK-created mitigation (wrong edge type AND
    # direction). Unit = distinct (op, tgt) pairs.
    conf_w = f"op.confidence <= {MITIGATION_CONF_THRESHOLD}"
    count6 = _count(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(tgt:Point)\n"
        f"WHERE {_op_in_kinds('op', 'tgt')} AND {conf_w}\n"
        "OPTIONAL MATCH (op)-[mit:mitigated_by]->(:Point)\n"
        "WITH DISTINCT op, tgt, mit WHERE mit IS NULL\n"
        "RETURN count(*)",
        params=params,
    )
    rows6 = _rows(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(tgt:Point)\n"
        f"WHERE {_op_in_kinds('op', 'tgt')} AND {conf_w}\n"
        "OPTIONAL MATCH (op)-[mit:mitigated_by]->(:Point)\n"
        "WITH DISTINCT op, tgt, mit WHERE mit IS NULL\n"
        f"RETURN op.id, op.confidence, tgt.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("mitigation_recommended", [
        AuditIssue(
            issue_type="mitigation_recommended",
            severity="medium",
            node_id=str(op_id),
            detail=(f"Low-confidence operator {op_id} (conf={conf}) → "
                    f"'{tgt_content}' has no mitigation"),
            # strength= is the SDK/MCP kwarg (0-1, 0=neutralized); the value
            # sits in the skill's documented relevance-attack range 0.10-0.50.
            fix=(f"tortoise_mitigate_operator('{op_id}', 'Relevant because...', "
                 "strength=0.3)"),
        )
        for op_id, conf, tgt_content in rows6
    ], count6)

    # ── 6b. legacy_mitigates_edge (low, LEGACY) ──────────────────
    # graph-scripts-era edges used `mitigates`; the only current write path is
    # `mitigated_by`. Surfaced for migration, never treated as coverage.
    count6b = _count(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[mit:mitigates]->(m:Point)\n"
        f"WHERE {_op_in_kinds('op', 'm')}\n"
        "RETURN count(DISTINCT mit)",
        params=params,
    )
    rows6b = _rows(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[mit:mitigates]->(m:Point)\n"
        f"WHERE {_op_in_kinds('op', 'm')}\n"
        f"RETURN DISTINCT op.id, m.id, m.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("legacy_mitigates_edge", [
        AuditIssue(
            issue_type="legacy_mitigates_edge",
            severity="low",
            node_id=str(op_id),
            detail=(f"Legacy mitigates edge {op_id} -[:mitigates]-> "
                    f"'{m_content}' — migrate to mitigated_by"),
            fix=(f"MATCH (op:Point {{id:'{op_id}'}})-[r:mitigates]->"
                 f"(m:Point {{id:'{m_id}'}}) DELETE r "
                 f"CREATE (op)-[:mitigated_by]->(m)"),
            legacy=True,
        )
        for op_id, m_id, m_content in rows6b
    ], count6b, legacy=True)

    # ── 7. missing_sourceKind_source (medium, #1158) ──────────────
    # Canonical source-tiering check: Sources backing in-scope evidence with
    # NO tier annotation at all (sourceKind AND credibilityTier both unset —
    # a Source with credibilityTier only is valid, resolve_tier reads it).
    count7 = _count(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)"
        "-[:extractedFrom]->(src:Source)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} "
        "AND src.sourceKind IS NULL AND src.credibilityTier IS NULL\n"
        "WITH DISTINCT src, ev\n"
        "RETURN count(*)",
        params=params,
    )
    rows7 = _rows(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)"
        "-[:extractedFrom]->(src:Source)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} "
        "AND src.sourceKind IS NULL AND src.credibilityTier IS NULL\n"
        f"RETURN DISTINCT src.url, ev.id, ev.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("missing_sourceKind_source", [
        AuditIssue(
            issue_type="missing_sourceKind_source",
            severity="medium",
            node_id=str(src_url),
            detail=(f"Source '{src_url}' (backing evidence '{ev_content}') has "
                    "no sourceKind / credibilityTier"),
            fix=(f"tier the source: tortoise_set_source_tier('{src_url}', "
                 f"'T0'..'T4') or create_source('{src_url}', kind, tier=...)"),
        )
        for src_url, ev_id, ev_content in rows7
    ], count7)

    return AuditResult(
        issues=issues,
        node_count=node_count,
        edge_count=edge_count,
        check_counts=check_counts,
        check_legacy=check_legacy,
        check_capped=check_capped,
    )


def print_audit(result: AuditResult) -> None:
    """Pretty-print audit results (uncapped totals + capped samples)."""
    total = result.total_count()
    print(f"\n{'='*60}")
    print(f"Tortoise Audit — {result.node_count} nodes, {result.edge_count} edges")
    print(f"Scope: {result.node_count} nodes, {result.edge_count} edges")
    print(f"Issues: {total} total "
          f"({result.high_count()} high, {result.medium_count()} medium, "
          f"{result.low_count()} low)")
    print(f"{'='*60}")

    if total == 0:
        print("\n✅ No issues found.")
        return

    for severity in SEVERITY_ORDER:
        sev_issues = [i for i in result.issues if i.severity == severity]
        if not sev_issues:
            continue
        print(f"\n── {severity.upper()} ──")
        by_type: dict[str, list[AuditIssue]] = {}
        for iss in sev_issues:
            by_type.setdefault(iss.issue_type, []).append(iss)
        for itype, items in sorted(by_type.items()):
            count = result.check_counts.get(itype, len(items))
            legacy = result.check_legacy.get(itype, False)
            capped = result.check_capped.get(itype, False)
            tag = " [legacy]" if legacy else ""
            if capped:
                tag += " [count capped — upper bound]"
            print(f"\n  [{itype}]{tag} — {count} total")
            for iss in items[:5]:
                print(f"    • {iss.detail[:120]}")
                if iss.fix:
                    print(f"      fix: {iss.fix[:120]}")
            if len(items) > 5:
                print(f"    … and {len(items) - 5} of {count} shown")
