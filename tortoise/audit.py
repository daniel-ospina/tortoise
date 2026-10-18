"""Graph audit — check Tortoise graph wiring quality.

Identifies: missing sourceKind (point-level legacy + Source-level canonical),
missing sourceDate, superseded gaps (missing CORRECTS edge), live IMPL/NAND
edges into superseded points, naive-IMPL heuristics, missing mitigations,
legacy ``mitigates`` edges, and inverted validity windows (``validTo`` before
``validFrom`` — the point is unreadable to temporal queries, #5361).

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
    Point-level ``sourceKind`` is legacy (#398): the point-level check only
    flags evidence with NO Source provenance chain (true legacy gaps); tiering
    gaps on Sources are owned by the Source-level check.

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

import logging
import re
from dataclasses import dataclass, field

from tortoise.live import (  # #7927: traversal-position widening + the ONE
    # two-key identity declaration (``EPISTEMIC_ID_KEYS``) every widened
    # surface must resolve through.
    epistemic_disjunction,
    epistemic_identity,
)

# resolve_tier precedence: explicit credibilityTier (T0-T4) > sourceKind
# tier-form (T0-T4) > registry default > None (neutral). The Source-level check
# (7) keys on this OUTCOME, not the raw fields (#1158): a Source whose
# sourceKind is unregistered (registry default None) or whose credibilityTier is
# malformed ('T9') resolves neutral and is flagged. Registry-tiered kinds are
# computed dynamically so runtime register_source_kind_default() mappings are
# honored (SOURCE_KIND_DEFAULTS non-None entries are exactly T0-T4 by default).
from tortoise.source_credibility import SOURCE_KIND_DEFAULTS, TIER_PRIORS

_log = logging.getLogger(__name__)

# Cypher list literal of canonical tier forms, e.g. ['T0', 'T1', 'T2', 'T3', 'T4'].
_TIER_LIT = str(sorted(TIER_PRIORS))


def _registry_tiered_kinds_lit() -> str:
    """Cypher list literal of sourceKind values that resolve to a non-neutral
    tier via the registry (T0-T4 forms plus any runtime-registered mappings)."""
    return str(sorted(k for k, v in SOURCE_KIND_DEFAULTS.items()
                      if v in TIER_PRIORS))

# Sample cap per check. COUNTS are computed uncapped; samples are capped so a
# large violations set does not balloon the JSON payload.
SAMPLE_LIMIT = 50

# Check 5 heuristic fetch bound: keyword candidates are fetched capped and
# word-boundary filtered Python-side (the embedded Cypher engine has no regex
# support). This is a LOW-severity advisory check — the documented residual
# cap is deliberate (see the check docstring).
CHECK5_FETCH_LIMIT = 10000
# Check 8's inversion predicate is a Python primitive, so it cannot be counted
# in Cypher — the scan is paged instead: O(page) client memory, exact count.
CHECK8_PAGE = 5000

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
    def from_dict(cls, d: dict) -> "AuditResult":  # noqa: UP037
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


def _node_pattern(alias: str, keys: dict[str, object]) -> str | None:
    """``(m {id:…, eventId:…})`` — every key the node carries, or None.

    Rendering EVERY present key is necessary but not sufficient: a pattern with
    FEWER keys matches a SUPERSET, and the common case is exactly that (every
    ``:Point``/``:Subject``/``:Object`` is id-only). Two nodes may also share
    every key value, and an int id renders byte-identically to the string id
    beside it — so no property pattern can prove "this is that node".

    The proof therefore belongs to the CALLER, against the edge it is about to
    rewrite, using internal node ids (see ``audit_graph``'s
    ``_legacy_repair``): a pattern verified only by counting matching NODES was
    both too weak (emitted a statement for a twin) and too strong (refused an
    exact repair because an unrelated node of another label shared the id).
    """
    present = {k: v for k, v in keys.items() if v is not None}
    if not present:
        return None
    rendered = ", ".join(f"{k}:{_quoted(v)}" for k, v in present.items())
    return f"({alias} {{{rendered}}})"


def _quoted(value: object) -> str:
    """Render ``value`` as a single-quoted literal safe to EMBED in a ``fix``.

    Every repair string in this module is a fresh statement a caller may paste
    and run (that is what a ``fix`` IS), so a graph value inside one has to be
    a LITERAL, not raw text (#7927 review). An id is caller-supplied
    (``create_point(..., id=…)`` accepts any string) or ingest-supplied, so
    without this an ordinary id containing an apostrophe (``claim-o'brien``)
    makes the emitted repair a ``ResponseError`` — the same
    emitted-but-unapplicable repair the key-agnostic MATCH below exists to
    close — and a crafted one can widen or truncate the statement. Measured on
    the embedded engine: ``'cla\\'im-1'`` round-trips exactly, while a bare
    ``'cla'im-1'`` is a ResponseError.

    Both escape orders are NOT equivalent: ``\\`` must be escaped FIRST, or an
    escaped backslash left in front of a quote re-opens the literal. Control
    characters are escaped the same way (a raw newline is not inside a legal
    Cypher string). The result is also a legal PYTHON literal, which is why the
    three SDK-call suggestions can use the same helper.
    """
    text = (str(value)
            .replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t"))
    return "'" + text + "'"


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
        outdated=true flag (invalidate_point keeps the original status).

        Deliberately NOT ``live.TERMINAL_EXCLUDED_STATUSES`` (#2901): this is
        the SUPERSESSION shape, not the terminal shape. ``retracted`` /
        ``archived`` / ``deprecated`` are terminal but are not produced by a
        supersession write, so folding them in would make checks 3/4 flag
        retraction/archival as "superseded with no CORRECTS edge" — a false
        positive. The narrow set is the reason; the terminal set is the OTHER
        question, answered once in live.py.
        """
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
    # #7927 — SCOPE OF THIS SURFACE, recorded here rather than left as an
    # accident of history. Owner ruling #7813 made confidence propagate across
    # :Point / :Subject / :Object / :Event; the question below is which of
    # THOSE the audit must see, and the answer is not the same for every check.
    #
    # STAYS ``:Point``-ONLY (deliberate, and a widening here would be a
    # regression, not a fix):
    #
    #   * this count block and every ``pointKind``-scoped leg — ``pointKind``
    #     is a POINT-only property, so an entity is addressable only by
    #     dropping the scope filter the caller asked for;
    #   * checks 1 and 2 (missing_sourceKind / missing_sourceDate) audit
    #     point-level provenance annotations that non-Point labels do not
    #     carry at all. Admitting them would flag EVERY Subject/Object/Event
    #     as a legacy provenance gap — a false positive generated by the
    #     widening itself;
    #   * check 3 (superseded_no_edge) is :Point-anchored on BOTH sides: its
    #     subject is a superseded claim and a supersession write mints Points,
    #     so the replacement it looks for is a Point too;
    #   * check 8 (inverted_validity_window) reads ``validFrom``/``validTo``,
    #     also Point-level window properties — an entity carries neither, so
    #     "absent bound" reads as an open interval and the check has nothing
    #     to say about it.
    #
    # WIDENED (#7927), because these are WIRING checks and #7902 made a
    # :Subject/:Object/:Event a legal operator endpoint: the endpoint / traversal
    # positions of checks 4, 5, 6, 6b and 7 now admit the four labels through
    # ``live.epistemic_disjunction``. Leaving them ``:Point``-only was a FALSE
    # CLEAN, not a scope choice: a NAND operator whose only target is a
    # :Subject reported ``mitigation_recommended: 0`` — an integrity result that
    # is indistinguishable from a true one, which is the failure mode a read
    # path is not allowed to have (#6146). The anchored/scoped operand of each
    # check (the operator, the superseded point) stays a ``:Point`` — it is the
    # node whose ``pointKind`` and provenance the check is about.
    #
    # CONSEQUENCE OF THE SPLIT, for a consumer of ``check_counts``: the widened
    # checks are NO LONGER a subset of the counted universe (``node_count`` /
    # ``edge_count`` stay Point-only), so a check can report a finding whose
    # node is outside the count. That is the intended shape — the count answers
    # "how big is the Point wiring graph", the checks answer "is the wiring
    # broken" — and reading one as a density of the other is the mistake this
    # sentence exists to prevent.
    #
    # If entity labels need provenance coverage of their own, that is a NEW
    # check keyed on the entity label's provenance edges — not these widened
    # until they report everyone.
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
    # Point-level sourceKind is a legacy annotation (#398). This check ONLY
    # flags a true legacy provenance gap: evidence with no point-level
    # sourceKind AND no Source provenance chain (no extractedFrom edge to ANY
    # Source) — pre-#398 points. Points backed by a Source are never flagged
    # here: their tiering gap is owned by the Source-level check (7), so a
    # correct Source-level annotation clears the only relevant metric (#1158).
    no_provenance_w = (
        "OPTIONAL MATCH (ev)-[:extractedFrom]->(src:Source)\n"
        "WITH op, ev, src WHERE src IS NULL\n"
    )
    count1 = _count(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} AND ev.sourceKind IS NULL "
        "AND ev.is_operator = false\n"
        f"{no_provenance_w}"
        "WITH DISTINCT op, ev\n"
        "RETURN count(*)",
        params=params,
    )
    rows1 = _rows(
        proj,
        f"MATCH (op:Point {{is_operator: true}})-[:IMPL|NAND]->(ev:Point)\n"
        f"WHERE {_op_in_kinds('op', 'ev')} AND ev.sourceKind IS NULL "
        "AND ev.is_operator = false\n"
        f"{no_provenance_w}"
        f"RETURN DISTINCT op.id, ev.id, ev.content LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("missing_sourceKind", [
        AuditIssue(
            issue_type="missing_sourceKind",
            severity="low",
            node_id=str(ev_id),
            detail=(f"Legacy evidence '{ev_content}' (from operator {op_id}) has "
                    "no point-level sourceKind and no Source provenance "
                    "(extractedFrom)"),
            # Point-level sourceKind is legacy — tier the SOURCE node instead
            # (#398); see the missing_sourceKind_source check (7).
            fix=("wire provenance + tier the source: create_source(url, kind, "
                 "tier=...) then MATCH (ev:Point {id:" + _quoted(ev_id) + "}), "
                 "(s:Source {url:'...'}) CREATE (ev)-[:extractedFrom]->(s)"),
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
            fix=f"MATCH (n:Point {{id:{_quoted(ev_id)}}}) "
                "SET n.sourceDate = '2026-01-01'",
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
            fix=(f"Link the replacement: tortoise_supersede({_quoted(nid)}, '<new_id>') "
                 f"or MATCH (old:Point {{id:{_quoted(nid)}}}), "
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
        # #6976: load-bearing. FalkorDB 6.0.0 drops the whole WHERE predicate at
        # the re-binding MATCH below unless a `WITH` separates them, so this check
        # returned the UNFILTERED count (measured 11508 vs the true 47).
        "WITH sup\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active) "
        # #7927: ``active`` is a TRAVERSAL position — the live node still
        # wiring into a superseded claim may be a :Subject/:Object/:Event.
        f"WHERE {epistemic_disjunction('active')} "
        "AND (active.status IS NULL OR active.status = 'live')\n"
        "RETURN count(DISTINCT r)",
        params=params,
    )
    rows4 = _rows(
        proj,
        f"MATCH (sup:Point) WHERE {sup4_w}\n"
        # #6976: load-bearing `WITH sup` — see the count4 note above.
        "WITH sup\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active) "
        f"WHERE {epistemic_disjunction('active')} "
        "AND (active.status IS NULL OR active.status = 'live')\n"
        # #7927: the widened endpoint's identity, coalesced — it is rendered
        # into the message and the guidance ("mark X as superseded too"), never
        # re-anchored as an ``id``, so this is the place ``epistemic_identity``
        # IS right (unlike an id-keyed frontier, #8538).
        "RETURN DISTINCT sup.id, sup.content, type(r), "
        f"{epistemic_identity('active')}, active.id "
        f"LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("superseded_active_edges", [
        AuditIssue(
            issue_type="superseded_active_edges",
            severity="medium",
            node_id=str(sup_id),
            detail=(f"Superseded '{sup_content}' has active {edge_type} edge "
                    f"from {active_id if active_id is not None else '<no id or eventId>'}"
                    + ("" if active_id is not None else
                       " — that endpoint carries no identity, so it cannot be "
                       "named by the repair; remove the edges by hand")),
            # ``_quoted(None)`` renders the literal ``'None'`` — a phantom
            # identity that can even address a real node whose id is "None", so
            # an unaddressable endpoint contributes NO clause rather than one.
            # ``tortoise_supersede`` resolves BY ``id``, so naming the
            # coalesced value would hand it a value that is not an id (failing)
            # or its TWIN (colliding). The clause is therefore display-only when
            # the identity is not the node's own ``id``.
            fix=(f"Remove edges into {_quoted(sup_id)}" + (
                f" or mark {_quoted(active_id)} as superseded too"
                if (active_id is not None and active_id == active_nid_value)
                else (f" then supersede the node displayed as {_quoted(active_id)}"
                      if active_id is not None else ""))),
        )
        for sup_id, sup_content, edge_type, active_id, active_nid_value in rows4
    ], count4)

    # ── 5. impl_instead_of_nand (low, advisory heuristic) ──────────
    # Substring keyword matching produced false positives ("no " in
    # "tornado") at HIGH severity and incentivized false IMPL→NAND fixes
    # (NAND_BASE_WEIGHT=8.0 rewires belief propagation). Demoted to a LOW
    # advisory heuristic: word-boundary matched (Python-side — the embedded
    # Cypher engine has no regex), advisory-only fix string. Unit = distinct
    # (src, tgt) pairs.
    # #7927: the text may live on ``content`` (Point) or ``name``/``title``
    # (entity), so the keyword match reads the coalesced text — a
    # ``content``-only read made every entity target invisible here.
    kw_clauses = " OR ".join(
        f"toLower(coalesce(tgt.content, tgt.name, tgt.title, '')) CONTAINS '{kw}'"
        for kw in CONTRADICTION_KEYWORDS
    )
    rows5 = _rows(
        proj,
        # #7927: both endpoints are TRAVERSAL positions (an IMPL claim and its
        # target may each be a :Subject/:Object/:Event, #7902), and the
        # non-operator predicate is the CANONICAL one — a bare
        # ``tgt.is_operator = false`` drops an entity by NULL.
        "MATCH (src)-[e:IMPL]->(tgt)\n"
        f"WHERE {epistemic_disjunction('src')} "
        f"AND {epistemic_disjunction('tgt')} "
        f"AND {_op_in_kinds('src', 'tgt')} AND ({kw_clauses}) "
        "AND (tgt.is_operator IS NULL OR tgt.is_operator = false) "
        "AND tgt.op_type IS NULL\n"
        # #7927: the emitted FIX interpolates these ids, so the identity must be
        # the node's real key. ``_upsert_event`` sets ``id == eventId``, so an
        # in-tree Event is fine — but a legacy / out-of-tree Event may be keyed
        # on ``eventId`` only (the #7936 residual ``consistency.py`` describes),
        # and ``id:'None'`` in a repair string addresses nothing. Same identity
        # ``ep.get_contested_claims`` coalesces.
        # The emitted repair is an SDK CALL (``create_operator`` resolves its
        # endpoints by ``id``), so this leg returns the ``id``s — and the
        # internal node id, which is UNIQUE, for the dedupe below. A coalesced
        # identity is neither: ``id`` is not unique (``_event_guarded_merge``'s
        # suffix branch leaves ``id`` at the candidate while ``eventId`` takes
        # the suffix) and an ``eventId`` is not an ``id`` the call can resolve.
        "RETURN src.id, tgt.id, "
        "coalesce(tgt.content, tgt.name, tgt.title, ''), "
        "id(src), id(tgt) "
        f"LIMIT {CHECK5_FETCH_LIMIT}",
        params=params,
    )
    patterns5 = [re.compile(rf"\b{re.escape(kw)}\b") for kw in CONTRADICTION_KEYWORDS]
    issues5: list[AuditIssue] = []
    seen5: set[tuple[int, int]] = set()
    count5 = 0
    for src_id, tgt_id, tgt_content, src_nid, tgt_nid in rows5:
        low = (tgt_content or "").lower()
        hit = next(
            (kw for kw, pat in zip(CONTRADICTION_KEYWORDS, patterns5) if pat.search(low)),  # noqa: B905
            None,
        )
        if hit is None:
            continue
        # Dedupe on the INTERNAL node id: a shared ``id`` value is a tested
        # shape (see ``_node_pattern``), and keying on it would drop a genuine
        # second edge as a duplicate of the first.
        key = (src_nid, tgt_nid)
        if key in seen5:
            continue
        seen5.add(key)
        count5 += 1
        if len(issues5) < SAMPLE_LIMIT:
            # ``create_operator`` resolves endpoints by ``id`` — an endpoint
            # keyed on ``eventId`` alone cannot be named by the repair, so it
            # gets NO statement rather than one that raises
            # (``Points [...] do not exist``) or, worse, names its TWIN.
            if src_id is None or tgt_id is None:
                fix, suffix = "", (
                    " — an endpoint has no `id`, and the repair is id-keyed, so "
                    "it cannot name that endpoint; convert by hand")
            else:
                fix, suffix = (
                    "Verify semantic contradiction BEFORE converting — if "
                    f"confirmed: tortoise_create_operator('NAND', {_quoted(src_id)}, "
                    f"[{_quoted(tgt_id)}])"), ""
            issues5.append(AuditIssue(
                issue_type="impl_instead_of_nand",
                severity="low",
                # A None id must not be rendered as the literal "None": that is
                # a phantom identity that can address a real node whose id is
                # "None" if a consumer feeds ``node_id`` back into a lookup.
                node_id=(str(src_id) if src_id is not None else "<no id>"),
                detail=(f"IMPL edge {src_id if src_id is not None else '<no id>'} "
                        f"→ '{str(tgt_content)[:80]}' "
                        f"(keyword: '{hit}') — verify whether NAND applies{suffix}"),
                fix=fix,
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
        # #7927: ``tgt`` is a TRAVERSAL position — a mitigated operator's target
        # may be a :Subject/:Object/:Event (#7902), and a Point-only read
        # reported a false CLEAN for it.
        "MATCH (op:Point {is_operator: true})-[:IMPL|NAND]->(tgt)\n"
        f"WHERE {epistemic_disjunction('tgt')} "
        f"AND {_op_in_kinds('op', 'tgt')} AND {conf_w}\n"
        "OPTIONAL MATCH (op)-[mit:mitigated_by]->(:Point)\n"
        "WITH DISTINCT op, tgt, mit WHERE mit IS NULL\n"
        "RETURN count(*)",
        params=params,
    )
    rows6 = _rows(
        proj,
        # #7927: ``tgt`` is a TRAVERSAL position — a mitigated operator's target
        # may be a :Subject/:Object/:Event (#7902), and a Point-only read
        # reported a false CLEAN for it.
        "MATCH (op:Point {is_operator: true})-[:IMPL|NAND]->(tgt)\n"
        f"WHERE {epistemic_disjunction('tgt')} "
        f"AND {_op_in_kinds('op', 'tgt')} AND {conf_w}\n"
        "OPTIONAL MATCH (op)-[mit:mitigated_by]->(:Point)\n"
        "WITH DISTINCT op, tgt, mit WHERE mit IS NULL\n"
        "RETURN op.id, op.confidence, "
        "coalesce(tgt.content, tgt.name, tgt.title, '') "
        f"LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("mitigation_recommended", [
        AuditIssue(
            issue_type="mitigation_recommended",
            severity="medium",
            node_id=str(op_id),
            detail=(f"Low-confidence operator {op_id} (conf={conf}) → "
                    f"'{tgt_content}' has no mitigation"),
            # strength= is the SDK/MCP kwarg — sanctioned band [0.10, 0.50],
            # 0.50 = strongest dampening (single source: tortoise/weights.py
            # module docstring, #2315); the skill's relevance-attack range.
            fix=(f"tortoise_mitigate_operator({_quoted(op_id)}, 'Relevant because...', "
                 "strength=0.3)"),
        )
        for op_id, conf, tgt_content in rows6
    ], count6)

    # ── 6b. legacy_mitigates_edge (low, LEGACY) ──────────────────
    # graph-scripts-era edges used `mitigates`; the only current write path is
    # `mitigated_by`. Surfaced for migration, never treated as coverage.
    count6b = _count(
        proj,
        "MATCH (op:Point {is_operator: true})-[mit:mitigates]->(m)\n"
        f"WHERE {epistemic_disjunction('m')} AND {_op_in_kinds('op', 'm')}\n"
        "RETURN count(DISTINCT mit)",
        params=params,
    )
    rows6b = _rows(
        proj,
        "MATCH (op:Point {is_operator: true})-[mit:mitigates]->(m)\n"
        f"WHERE {epistemic_disjunction('m')} AND {_op_in_kinds('op', 'm')}\n"
        # #7927: ``m`` may be an :Event keyed on ``eventId`` with no ``id``
        # (legacy / out-of-tree — ``_upsert_event`` sets both), so BOTH keys are
        # returned and the repair is rendered against whichever one the node
        # actually carries. Coalescing the value and matching it key-agnostic
        # was not enough: ``coalesce(m.id, m.eventId) = 'X'`` equates two
        # DIFFERENT properties, so the repair could also address an unrelated
        # node that happens to carry ``X`` under the other key (a reachable
        # shape — ``consistency._graph_view`` models ``:Event {eventId:'X'}``
        # alongside ``:Event {id:'X'}``, #8879).
        #
        # The INTERNAL node ids come along for the verification below: they are
        # the only thing that says "this is the row's node" (a property value
        # cannot — two nodes may share every key value, and an int id and a
        # string id render identically through ``_quoted``).
        "RETURN DISTINCT op.id, m.id, m.eventId, "
        "coalesce(m.content, m.name, m.title, ''), id(op), id(m) "
        f"LIMIT {SAMPLE_LIMIT}",
        params=params,
    )

    def _legacy_repair(op_id, m_id, m_eid, op_nid, m_nid) -> tuple[str, str]:
        """``(fix, detail_suffix)`` addressing EXACTLY the edge the issue names.

        A repair is EMITTED ONLY IF it can be verified to touch one edge, and
        it is verified by its own edge pattern — not by counting nodes: a
        property pattern matches a SUPERSET when a node carries fewer keys than
        its twin (every ``:Point`` is id-only), two nodes may share every key
        value, and an int id renders byte-identically to the string id beside
        it. Measured failures of the shape-only check: one issue's statement
        repairing a different edge (twin ops, int/string twins, id-only vs
        two-key), and exact repairs REFUSED because an unrelated node of another
        label shared the id.

        So the read-back asks the only question that matters — does this
        statement resolve to the row's ONE edge, between the row's two nodes —
        and the answer must be ``count(r) == 1`` with both endpoint internal ids
        equal to the row's. Internal ids are used for the CHECK, never written
        into the repair: they do not survive ``GRAPH.COPY``/rebuild, so an
        emitted one could stop matching later.

        ``MERGE``, not ``CREATE``: this check reports a state a PARTIALLY
        applied migration leaves behind (both edges present on the same pair),
        and two parallel direct edges between one pair share a single message
        slot (``ep._affected_factors``) — the repair must not mint the second.
        """
        if op_id is None:
            return "", (" — the operator carries no `id`, so the edge cannot be "
                        "named; repair by hand")
        target = _node_pattern("m", {"id": m_id, "eventId": m_eid})
        if target is None:
            return "", (" — the endpoint carries neither `id` nor `eventId`, so "
                        "no statement can address it; repair by hand")
        statement = (f"MATCH (op:Point {{id:{_quoted(op_id)}}})-[r:mitigates]->"
                     f"{target}")
        try:
            rows = proj.g.query(
                f"{statement} RETURN count(r), collect(id(op)), collect(id(m))"
            ).result_set
        except Exception as exc:  # an unverifiable repair is a refused one
            _log.warning(
                "legacy_mitigates_edge: repair verification failed for %r: %s",
                statement, exc)
            return "", (" — the repair could not be verified against the graph; "
                        "repair by hand")
        if not rows or rows[0][0] == 0:
            # Zero is its OWN cause, not a narrower verdict. Two of them:
            # ``_quoted`` renders every id as a string literal, so a non-string
            # id can never be re-addressed by the emitted pattern (the RENDERER
            # cannot see it — measured, and the reason a numeric id needs this
            # branch); or the graph changed between the row read and this check,
            # since ``audit_graph`` takes no snapshot. The diagnosis is computed
            # from the values in hand, not asserted — naming a cause the run did
            # not measure is the defect this branch exists to fix.
            non_string = any(
                value is not None and not isinstance(value, str)
                for value in (op_id, m_id, m_eid))
            if non_string:
                reason = ("the row's id is not a string, so it cannot be "
                          "re-rendered as a literal")
            else:
                reason = ("the repair matches no edge — the graph may have "
                          "changed since this issue was read")
            return "", f" — the repair addresses no edge: {reason}; repair by hand"
        if rows[0][0] != 1:
            return "", (" — the repair matches more than the one edge this "
                        "issue describes; repair by hand")
        op_ids, m_ids = rows[0][1], rows[0][2]
        if op_ids != [op_nid] or m_ids != [m_nid]:
            return "", (" — the repair resolves to a DIFFERENT node pair than "
                        "this edge (a key value is shared with another node); "
                        "repair by hand")
        return f"{statement} DELETE r MERGE (op)-[:mitigated_by]->(m)", ""

    def _legacy_issue(op_id, m_id, m_eid, m_content, op_nid, m_nid) -> AuditIssue:
        fix, suffix = _legacy_repair(op_id, m_id, m_eid, op_nid, m_nid)
        shown_op = op_id if op_id is not None else "<no id>"
        return AuditIssue(
            issue_type="legacy_mitigates_edge",
            severity="low",
            # A None id must never be rendered as the literal "None": that is a
            # phantom identity that can address a real node whose id is "None".
            node_id=str(shown_op),
            detail=(f"Legacy mitigates edge {shown_op} -[:mitigates]-> "
                    f"'{m_content}' — migrate to mitigated_by{suffix}"),
            fix=fix,
            legacy=True,
        )

    _record("legacy_mitigates_edge", [
        _legacy_issue(op_id, m_id, m_eid, m_content, op_nid, m_nid)
        for op_id, m_id, m_eid, m_content, op_nid, m_nid in rows6b
    ], count6b, legacy=True)

    # ── 7. missing_sourceKind_source (medium, #1158) ──────────────
    # Canonical source-tiering check, keyed on resolve_tier OUTCOME (not the
    # raw field): a Source is untiered iff its effective tier is neutral —
    # credibilityTier NOT a T0-T4 form AND sourceKind NOT a T0-T4 form AND
    # sourceKind NOT a registry-tiered kind (runtime registrations honored).
    # Explicitly-neutral legacy kinds (registry → None) and unregistered kinds
    # resolve neutral → flagged (the #334 remediation population). A Source
    # with credibilityTier only is valid (resolve_tier reads it).
    source_untiered_w = (
        f"NOT ((src.credibilityTier IS NOT NULL AND "
        f"src.credibilityTier IN {_TIER_LIT})"
        f" OR (src.sourceKind IS NOT NULL AND src.sourceKind IN {_TIER_LIT})"
        f" OR (src.sourceKind IS NOT NULL AND "
        f"src.sourceKind IN {_registry_tiered_kinds_lit()}))"
    )
    count7 = _count(
        proj,
        # #7927: ``ev`` is a TRAVERSAL position (the evidence claim may be a
        # :Subject/:Object/:Event). ``src`` stays ``:Source`` — the tiering
        # predicate below is about the SOURCE, not the claim.
        "MATCH (op:Point {is_operator: true})-[:IMPL|NAND]->(ev)"
        "-[:extractedFrom]->(src:Source)\n"
        f"WHERE {epistemic_disjunction('ev')} "
        f"AND {_op_in_kinds('op', 'ev')} AND {source_untiered_w}\n"
        "WITH DISTINCT src, ev\n"
        "RETURN count(*)",
        params=params,
    )
    rows7 = _rows(
        proj,
        "MATCH (op:Point {is_operator: true})-[:IMPL|NAND]->(ev)"
        "-[:extractedFrom]->(src:Source)\n"
        f"WHERE {epistemic_disjunction('ev')} "
        f"AND {_op_in_kinds('op', 'ev')} AND {source_untiered_w}\n"
        "RETURN DISTINCT src.url, "
        "coalesce(ev.content, ev.name, ev.title, '') "
        f"LIMIT {SAMPLE_LIMIT}",
        params=params,
    )
    _record("missing_sourceKind_source", [
        AuditIssue(
            issue_type="missing_sourceKind_source",
            severity="medium",
            node_id=str(src_url),
            detail=(f"Source '{src_url}' (backing evidence '{ev_content}') "
                    "resolves to a NEUTRAL tier (no explicit credibilityTier / "
                    "tier-form or registry-tiered sourceKind)"),
            fix=(f"tier the source: tortoise_set_source_tier({_quoted(src_url)}, "
                 f"'T0'..'T4') or create_source({_quoted(src_url)}, kind, "
                 f"tier=...)"),
        )
        for src_url, ev_content in rows7
    ], count7)

    # ── 8. inverted_validity_window (high, #5361) ──────────────────
    # A Point whose persisted window END precedes its START (validTo <
    # validFrom) is unreadable to temporal queries: get_historical_knowledge's
    # `_covers` requires validFrom <= t <= validTo, and no instant satisfies
    # both, so the point silently disappears — indistinguishable from honest
    # absence. HIGH because this is a real integrity failure, not a style
    # nit: a live point is invisible to the temporal read path while that path
    # reports it as merely absent.
    #
    # The measure is the READ PATH's own, never a string compare:
    # `_created_sort_key` (the exact key `_covers` orders with — mixed-format
    # ISO/epoch safe) plus `is not None` PRESENCE (never truthiness; a
    # falsey-but-present bound is #3985). A naive `validTo < validFrom`
    # string compare misreports open-ended and unparseable values (and raises
    # on a ``None``), which is the bug class this guard must not have. ABSENT
    # (None) bounds are open intervals — legal, not reported. An UNPARSEABLE
    # bound buckets as (1, <text>) and is a DIFFERENT concern (#5360) — not
    # reported here.
    #
    # The count is exact, and the scan is PAGED. The ordering is defined by a
    # Python primitive, so it cannot be counted in Cypher; but this tool is
    # reachable over MCP with no `point_kinds`, and materializing every bounded
    # Point in one result set is an unbounded cost no sibling check pays. Pages
    # keep client memory at O(page) while the count stays exact, so
    # `check_capped` remains False — this is a real total, not check 5's
    # deliberately upper-bounded fetch (CHECK5_FETCH_LIMIT).
    from tortoise.search_engine import _created_sort_key
    issues8: list[AuditIssue] = []
    count8 = 0
    offset = 0
    while True:
        window_rows = _rows(
            proj,
            f"MATCH (n:Point) WHERE {_kinds_w('n')} "
            "AND n.validFrom IS NOT NULL AND n.validTo IS NOT NULL "
            "RETURN n.id, n.validFrom, n.validTo "
            # `n.id` is NOT unique (colliding ids are a tested shape), so
            # ordering by it alone is not a total order. This scan pages with
            # SKIP/LIMIT, and each page is a SEPARATE query, so a tie group
            # straddling the boundary can be reordered between the two
            # executions: rows are then DROPPED and DUPLICATED, and an inverted
            # window can be missed entirely (a false clean). Measured on this
            # head at CHECK8_PAGE=5000: 5100 tied rows lost 82 and repeated 82.
            # `id(n)` (the internal node id) is unique and makes the order
            # total, which is what paging requires.
            "ORDER BY n.id, id(n) "
            f"SKIP {offset} LIMIT {CHECK8_PAGE}",
            params=params,
        )
        for wid, w_from, w_to in window_rows:
            k_from = _created_sort_key(w_from)
            k_to = _created_sort_key(w_to)
            if k_from[0] != 0 or k_to[0] != 0:
                continue  # unparseable bound — owned by #5360, not this check
            if k_to < k_from:
                count8 += 1
                if len(issues8) < SAMPLE_LIMIT:
                    issues8.append(AuditIssue(
                        issue_type="inverted_validity_window",
                        severity="high",
                        node_id=str(wid),
                        detail=(f"Point {wid} has an inverted validity window "
                                f"(validFrom {w_from!r} > validTo {w_to!r}) — no "
                                "instant is covered, so temporal queries cannot "
                                "see it"),
                        fix=(f"Repair the window: tortoise_update_point({_quoted(wid)}, "
                             "{'validTo': '<at-or-after validFrom>'})"),
                    ))
        if len(window_rows) < CHECK8_PAGE:
            break
        offset += CHECK8_PAGE
    _record("inverted_validity_window", issues8, count8)

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
