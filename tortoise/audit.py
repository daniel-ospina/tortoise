"""Graph audit — check Tortoise graph wiring quality.

Identifies: missing sourceKind (point-level legacy + Source-level canonical),
missing sourceDate, superseded gaps (missing CORRECTS edge), live IMPL/NAND
edges into superseded points, live IMPL/NAND edges into superseded OBJECTS
(#11826 — the Object lane moves no edges, so there the shape is lost belief,
not leftover wiring), a superseded Object whose ``supersededBy`` name resolves
to no node at all (#11826), naive-IMPL heuristics, missing mitigations,
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
    DRAFT_STATUS,  # #780: EP strips a DRAFT input exactly as it strips a
    # terminal one, so a draft successor is a repair that does not repair.
    direction_flows,  # the ONE read-side direction normalizer — the repair's
    # idx-0-source arm has to ask the SAME question ep.py asks.
    epistemic_disjunction,
    epistemic_identity,
    is_terminal_status,  # #7819: the shared terminal predicate (Python mirror)
)

# resolve_tier precedence: explicit credibilityTier (T0-T4) > sourceKind
# tier-form (T0-T4) > registry default > None (neutral). The Source-level check
# (7) keys on this OUTCOME, not the raw fields (#1158): a Source whose
# sourceKind is unregistered (registry default None) or whose credibilityTier is
# malformed ('T9') resolves neutral and is flagged. Registry-tiered kinds are
# computed dynamically so runtime register_source_kind_default() mappings are
# honored (SOURCE_KIND_DEFAULTS non-None entries are exactly T0-T4 by default).
from tortoise.search_engine import belief_mirror_cypher  # #11702: the banded mirror
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


def _status_or_flag(status, outdated: bool) -> str:
    """A node's EP-lifecycle state, spelling the ``outdated`` flag when it is the cause.

    ``is_terminal_status(None, True)`` is True, so a legacy-flagged node reaches the
    terminal arms with NO status at all and ``_quoted(None)`` rendered the literal
    ``'None'`` — a status no node carries, and the one fact the repair must act on
    (the flag) was never stated.
    """
    if outdated:
        return f"{status or 'live'}(outdated)"
    return status or "live"


def _named(value: object) -> str:
    """A node NAME for display — a missing one is ``<unnamed>``, never ``None``.

    ``update_entity(obj, name=None)`` is accepted (``o.name = NULL``), and an
    f-string then interpolated Python's ``None`` into a finding, naming a node that
    exists nowhere in the graph (measured: "Superseded Object 'None' ...").
    """
    return _quoted(value) if value else "<unnamed>"


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

    # ── 4b. superseded_object_active_edges (high) ────────────────
    # #11826: the OBJECT supersession lane moves NO edges (the fold writes
    # ``status``/``supersededBy``/``supersededAt`` only —
    # ``projection/entities.py::_fold_object_superseded``), so the shape check 4
    # reports as LEFTOVER WIRING on a :Point is LOST BELIEF on an :Object: the
    # operator's input stays anchored to the node the supersession just made
    # terminal for EP, EP strips that input (``1/2 inputs stripped — factor
    # skipped (degenerate, #780/#2422)``), and the factor stops funding the
    # successor that should carry it — silently, because nothing reports it.
    #
    # HIGH, and deliberately not check 4's ``medium``: on a :Point the transfer
    # is the lane's own job and a surviving edge is an anomaly; on an :Object the
    # successor inherits nothing BY CONSTRUCTION, so every finding here is the
    # measured defect rather than a stray survivor.
    #
    # INCOMING only — check 4's orientation, kept so the two checks answer ONE
    # question in two vocabularies.
    #
    # ⛔ THE OUTGOING ORIENTATION IS A DECLARED FALSE-CLEAN, and the reason first
    # given here was FALSE — corrected rather than quietly deleted. An outgoing
    # IMPL/NAND from a superseded :Object does NOT require the Object to act as an
    # operator: ``create_direct_edge("IMPL", <object>, <point>)`` is a public SDK
    # door that writes the operator-less edge ``Object-[:IMPL]->Point`` (its own
    # docstring admits any of the four epistemic labels, #7902), and EP DOES build a
    # factor from it with the Object as the factor's source (measured: two
    # confidences before the supersession, NONE after — ``confidences: {}``,
    # ``skipped_factors: None``, every supersession count 0). So the same lost belief
    # exists in the other direction and no check in this module reports it. The arm
    # is a follow-up (it needs its own consequence text: a direct edge carries no
    # ``idx`` and produces no skipped-factor record), not this change's.
    #
    # ANCHORED on ``status = 'superseded'`` and nothing wider. NOT the legacy
    # ``outdated`` status/flag: an ``outdated`` :Object has no successor to
    # inherit anything, so the finding this check exists for cannot be phrased
    # against it (it is a different defect — the unguarded Object status door,
    # #8166), and NOT ``retracted``/``archived``/``deprecated`` either, for
    # ``_superseded_w``'s recorded reason: those are terminal without being
    # produced by a supersession write.
    #
    # NOT gated on ``pointKind``: that is a POINT-only property, so scoping an
    # entity-anchored check by it would silently drop every Object whose
    # operator carries no kind value — a false CLEAN, the one result a read
    # surface may not produce (#6146).
    #
    # SCOPE, declared rather than implied (the rule check 4c states for itself):
    # this check is `:Object`-anchored, and a superseded `:Subject` that is an
    # operator input has the SAME shape — the Subject lane moves no edges either
    # (#1423), and EP's participation predicate is label-agnostic
    # (``epistemic_disjunction`` admits ``:Subject``; the strip is
    # ``status in TERMINAL_EXCLUDED_STATUSES``), so the input is stripped and the
    # belief is lost exactly as measured in #11826. Measured: a ``:Subject``
    # superseded with ``apply_supersessions`` carries ``status='superseded'``,
    # holds no relationship of any type to its successor, and NO check in this
    # module reports it. That is a KNOWN false-clean for that label, recorded
    # rather than left as an accident of the anchor — the Subject arm is a
    # follow-up (it needs the same "is there a successor / was it rewired"
    # decision below), not this change's.
    #
    # ``:Point:Object`` — a TEST-FIXTURE shape no production writer mints
    # (``projection/__init__.py``'s fixture lane says so). If one is ever
    # superseded, checks 4 and 4b both report it (``medium`` and ``high``, one
    # node, two vocabularies): the two checks answer one question per label, and
    # an overlap is the price of that, not a separate defect.
    #
    # ⛔ THE REPAIR AND THE CONSEQUENCE DEPEND ON TWO FACTS THE ANCHOR DOES NOT
    # IMPLY, so both are read below rather than assumed:
    #
    #   * ``supersededBy`` — the anchor is ``status = 'superseded'``, and the
    #     generic ``update_entity(status='superseded')`` door reaches that status
    #     with NO successor at all (#11824's AX table: that mirror shape is out of
    #     the guard on purpose — refusing it would over-block). Telling a reader
    #     to re-point "onto the successor named by its supersededBy" then names a
    #     node that does not exist, so the repair cannot be performed — the
    #     #7819 rule this module already applies to a remedy that always raises.
    #     And it is NOT "is the field set": a pointer naming a RENAMED successor,
    #     or the node itself, is set and resolves to nothing to re-point onto, so
    #     the arm is selected on whether the pointer RESOLVES (``carrier_count``
    #     in the row query) rather than on its presence.
    #
    #     ⛔ ``archived`` IS NOT THE REPAIR, and offering it as one would have been
    #     a self-inflicted false NEGATIVE: ``archived`` is in
    #     ``TERMINAL_EXCLUDED_STATUSES``, so EP strips the input exactly as before
    #     (measured: ``reason: degenerate`` with the input shown as ``archived``),
    #     while the node leaves THIS anchor (``status = 'superseded'``) and no other
    #     check in this module covers a live IMPL/NAND edge into an ``archived``
    #     Object. An agent that ran that call and re-audited would see a clean graph
    #     over a still-degenerate factor. The successorless arm therefore names
    #     ``archived`` only to say what it does NOT clear.
    #   * whether the successor was ALREADY rewired — a successor can carry an
    #     equivalent input while the stale edge survives (that is step 1 of the
    #     emitted repair without step 2; measured: the factor then RUNS, 3 inputs
    #     with 1 stripped, and the successor does inherit the belief). The
    #     survivor is then leftover wiring, not lost belief, so a consequence
    #     stated unconditionally would misreport it — the same overstatement the
    #     two-vs-wider clause below exists to avoid. The hedge is scoped to "a slot
    #     EP reads" because rewiring does NOT restore participation when the
    #     stripped input was a DIRECTIONAL operator's idx-0 source: EP takes the
    #     ``source_stripped`` branch before the degenerate test (measured:
    #     ``direction='->'``, rewrite the successor, ``skipped: ['source_stripped']``).
    count4b = _count(
        proj,
        "MATCH (sup:Object) WHERE sup.status = 'superseded'\n"
        # #6976: load-bearing `WITH sup` — without it FalkorDB drops the whole
        # WHERE predicate at the re-binding MATCH below (see the count4 note).
        "WITH sup\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active) "
        # #7927: ``active`` is a TRAVERSAL position, so the live endpoint may be
        # a :Point/:Subject/:Event as readily as another :Object.
        f"WHERE {epistemic_disjunction('active')} "
        "AND (active.status IS NULL OR active.status = 'live')\n"
        # EP's own liveness for the source is ``_live_only('o.status')``, which also
        # requires the legacy flag to be false — so an ``outdated`` source is EP-dead
        # and the row would describe it as "active" over a factor EP never builds
        # (``invalidate_point`` is a door into that state).
        "AND coalesce(active.outdated, false) = false\n"
        "RETURN count(DISTINCT r)",
    )
    rows4b = _rows(
        proj,
        "MATCH (sup:Object) WHERE sup.status = 'superseded'\n"
        # #6976: load-bearing `WITH sup` — see the count4b note above.
        "WITH sup\n"
        "MATCH (sup)<-[r:IMPL|NAND]-(active) "
        f"WHERE {epistemic_disjunction('active')} "
        "AND (active.status IS NULL OR active.status = 'live')\n"
        # EP's own liveness for the source — see the count4b note. ⛔ Check 4 (the
        # ``:Point`` orientation) keeps the older filter; that divergence is a
        # recorded follow-up rather than half-fixed here.
        "AND coalesce(active.outdated, false) = false\n"
        # ⛔ CARRIER COUNT, in the same statement, because the repair's arm turns on
        # whether the pointer RESOLVES — not on whether it is merely SET. A
        # `supersededBy` naming a renamed successor, or the node itself, is present
        # and resolves to nothing this check can re-point onto (measured: rename the
        # successor and the pointer is still a string, `carrier_count` is 0). The
        # self-exclusion is what makes "resolvable" mean a DIFFERENT node: a
        # self-pointer is not a successor, and an in-place `supersededBy` that names
        # a same-named OTHER Object still counts (1 carrier, silent).
        # ⛔ ``id(carrier) <> id(sup)``, NOT ``carrier.id <> sup.id``: a NULL id on
        # either side makes the comparison NULL, the OPTIONAL MATCH binds NOTHING,
        # and both checks then state the opposite of the graph (measured: an
        # id-less superseded Object with a resolvable successor emitted "NO Object
        # carries that name" and "there is nothing to re-point this input onto").
        # Internal node identity is always present, so the exclusion is null-safe.
        "OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy "
        "AND id(carrier) <> id(sup)\n"
        "WITH sup, r, active, count(carrier) AS carrier_count, "
        "     head(collect(id(carrier))) AS carrier_id, "
        "     head(collect(carrier.status)) AS carrier_status, "
        "     head(collect(carrier.supersededBy)) AS carrier_sb, "
        # EP's terminal test is ``status IN <vocab> OR outdated = true``
        # (``live.is_terminal_status``), so a legacy-flagged successor is EP-dead
        # with NO status at all — reading ``status`` alone put it on the ordinary
        # arm, where the prescribed re-point leaves the factor degenerate and a
        # re-audit reads CLEAN.
        "     head(collect(coalesce(carrier.outdated, false))) AS carrier_outdated, "
        # ⛔ The carrier's OPERATOR-SHAPED flag, from the SAME group (no fourth
        # OPTIONAL MATCH, so ``count4b`` and SAMPLE_LIMIT are untouched). EP does
        # not admit an operator-shaped node as a factor input (#9269,
        # ``_non_operator``) — the same strip a draft gets — so re-pointing onto one
        # leaves the factor degenerate and a re-audit would read CLEAN (measured).
        # It keys on the RAW FLAG, not the flag-OR-``op_type`` predicate ``active``
        # uses: an ``op_type``-only Object IS admitted (measured, ``skipped: []``).
        "     head(collect(coalesce(carrier.is_operator, false))) "
        "AS carrier_is_operator\n"
        # ⛔ ONE HOP OF LOOKAHEAD, because "the successor is itself terminal" is NOT
        # the same fact as "there is a live head to re-point onto": the Object lane can
        # terminalize without a supersession (so the successor names no successor at
        # all — no chain exists), and a chain's next name can dangle. Both states
        # would otherwise be told to "target the LIVE head of that chain" — a node
        # that does not exist, the #7819 rule this module refuses to emit.
        "OPTIONAL MATCH (nxt:Object) WHERE nxt.name = carrier_sb "
        # The hop must exclude the CARRIER as well as the subject: a successor
        # whose own ``supersededBy`` is its own name would otherwise find
        # ITSELF and the string would tell the reader to walk a chain with no
        # live head.
        "AND id(nxt) <> id(sup) AND id(nxt) <> carrier_id\n"
        "WITH sup, r, active, carrier_count, carrier_id, carrier_status, "
        "     carrier_sb, carrier_is_operator, carrier_outdated, "
        "     count(nxt) AS next_count, "
        "     head(collect(nxt.status)) AS next_status, "
        "     head(collect(coalesce(nxt.outdated, false))) AS next_outdated, "
        # ⛔ The next hop's NON-PARTICIPATION, for the same reason hop 1 carries it:
        # ``draft`` is NOT terminal (so ``next_terminal`` is False) and an
        # operator-shaped node is excluded from the input selection entirely, yet
        # both were announced as "which is LIVE — target THAT". Measured: the
        # prescribed re-point left the factor producing NO belief (confidences
        # empty) while a re-audit read CLEAN. Reachable through the sanctioned fold
        # lane — ``apply_supersessions`` admits a DRAFT successor.
        "     head(collect(coalesce(nxt.is_operator, false))) AS next_is_operator\n"
        # ``r.idx`` and the OPERATOR's ``direction`` are projected because the repair
        # differs in the idx-0 source slot (ep.py's ``source_stripped`` hazard),
        # ``op_type`` because EP's operator predicate is the flag OR ``op_type``
        # (see ``_4b_state``), and the two lookahead columns because a terminal
        # successor is not necessarily a chain.
        # ⛔ The operator's ARITY is deliberately NOT projected: a fourth
        # ``OPTIONAL MATCH`` would multiply each row by the operator's input count
        # and SAMPLE_LIMIT would then truncate a different set of rows than
        # ``count4b`` counts. The strings therefore do not assert an arity — the
        # detail states the consequence as a ladder over whatever inputs remain
        # (including none, where EP builds no factor at all).
        "RETURN DISTINCT sup.id, sup.name, sup.supersededBy, type(r), r.idx, "
        "coalesce(active.is_operator, false), active.op_type, active.direction, "
        # ⛔ EP's operator arm ALSO requires the label (``(o:Point)-[:IMPL|NAND]->``),
        # so a non-:Point node carrying ``is_operator=true`` is in NEITHER arm — the
        # direct-edge arm demands a non-operator on both endpoints — and no factor
        # exists for it. Without this column the row took the operator arm and
        # prescribed an ``idx`` no factor would ever read (door-reachable:
        # ``create_direct_edge('IMPL', actor, obj)`` then
        # ``update_entity(actor, is_operator=True)``).
        "labels(active), "
        f"{epistemic_identity('active')}, active.id, carrier_count, "
        "carrier_id, carrier_status, carrier_sb, carrier_is_operator, "
        "carrier_outdated, next_count, next_status, next_outdated, "
        "next_is_operator "
        f"LIMIT {SAMPLE_LIMIT}",
    )

    def _4b_state(row) -> dict:
        """One ``rows4b`` row, as the facts the two emitted strings are built from.

        ⛔ ``is_operator`` is EP's OWN operator predicate, not the bare flag: EP
        treats a Point as an operator when ``is_operator = true`` **or** ``op_type
        IS NOT NULL`` (the complement of its non-operator clause, ``ep.py``), and
        the hybrid ``create_point(op_type=...)`` shape is exactly the second half
        (measured: ``is_operator=False, op_type='IMPL'``). Reading the flag alone
        sent that row down the DIRECT-edge arm — "EP builds NO factor … there is no
        skipped-factor record" (false: EP builds the factor and records the strip)
        and a repair with no ``idx`` for an edge that is an operator input.
        """
        (sup_id, sup_name, sup_sb, edge_type, idx, is_op_flag, op_type,
         direction, active_labels, active_id, _active_nid, carrier_count,
         _carrier_id, carrier_status, carrier_sb, carrier_is_operator,
         carrier_outdated, next_count, next_status, next_outdated,
         next_is_operator) = row
        return {
            "sup_id": sup_id, "sup_name": sup_name, "sup_sb": sup_sb,
            "edge_type": edge_type, "idx": idx,
            # ⛔ EP's predicate is the flag OR ``op_type`` AND the ``:Point`` label
            # (its arms are ``(o:Point)-[:IMPL|NAND]->(c)`` and a non-operator check
            # on both endpoints of a direct edge), so the label belongs here.
            # ⛔ SHAPE and PREDICATE are different questions and the third mechanism
            # lives between them: EP's operator arm requires the ``:Point`` label AND
            # the flag-or-``op_type``, while its direct-edge arm refuses an
            # operator-SHAPED endpoint on either side (``is_operator IS NULL|false
            # AND op_type IS NULL``). So a shaped non-``:Point`` endpoint is in
            # NEITHER arm — no factor, no skipped-factor record — and the direct-edge
            # sentence ("write the equivalent edge") would prescribe a repair that
            # changes nothing (measured: confidences empty after it).
            "shape": bool(is_op_flag) or op_type is not None,
            "is_operator": ("Point" in (active_labels or [])
                            and (bool(is_op_flag) or op_type is not None)),
            "direction": direction, "active_id": active_id,
            "carrier_count": carrier_count, "carrier_status": carrier_status,
            "carrier_sb": carrier_sb,
            "carrier_is_operator": bool(carrier_is_operator),
            # ⛔ DERIVED through the shared predicate, not read off ``status``: EP's
            # terminal test is the status vocabulary OR the legacy ``outdated`` flag.
            "carrier_outdated": bool(carrier_outdated),
            "carrier_terminal": is_terminal_status(carrier_status,
                                                   bool(carrier_outdated)),
            "next_count": next_count, "next_status": next_status,
            "next_outdated": bool(next_outdated),
            "next_terminal": is_terminal_status(next_status, bool(next_outdated)),
            "next_is_operator": bool(next_is_operator),
        }

    def _successor_note(s: dict) -> str:
        """The pointer's state, as the trailing clause of a ``detail``.

        Four facts, not one: nothing resolvable; a resolvable successor; a
        resolvable successor that is ITSELF terminal (a supersession CHAIN —
        re-pointing onto it clears this finding and leaves the input anchored to
        another EP-dead node); or an AMBIGUOUS name (N≥2 carriers, #3573), which
        this check declines to resolve and therefore must not describe by picking
        one carrier's status.
        """
        if not s["carrier_count"]:
            named = f"{_quoted(s['sup_sb'])}" if bool(s["sup_sb"]) else "none"
            return ("; and no OTHER Object answers its supersededBy "
                    f"({named}) — if a Subject or Point carries that name, re-point "
                    "onto that node; otherwise name the successor")
        if s["carrier_count"] > 1:
            return (f"; and its supersededBy ({_quoted(s['sup_sb'])}) is AMBIGUOUS "
                    f"({s['carrier_count']} Objects carry it, #3573), so the repair "
                    "must name the Object meant as the successor")
        if s["carrier_is_operator"]:
            # ⛔ EP does not admit an OPERATOR-SHAPED node as a factor input (#9269,
            # ``_non_operator``) — the same strip a draft gets — so re-pointing onto
            # one leaves the factor degenerate while a re-audit reads CLEAN. Measured:
            # ``create_entity("object", n, is_operator=True)`` is door-permitted, the
            # prescribed re-point leaves ``skipped_factors: [degenerate]`` and 4b
            # reports 0 issues afterwards.
            return ("; the successor it names is itself OPERATOR-SHAPED "
                    "(is_operator=true), which EP excludes from a factor's inputs "
                    "(#9269) — so re-pointing onto it drops that input entirely and "
                    "the operator is left with NO factor (measured: confidences "
                    "empty, successor absent), the claim's support silently gone "
                    "while a re-audit reads CLEAN. Target a CLAIM successor")
        if (s["carrier_status"] or "") == DRAFT_STATUS:
            # ⛔ A DRAFT successor is not terminal, so without this arm it takes the
            # ordinary one — but EP strips a draft input exactly as it strips a
            # terminal input (#780). The re-point would then read CLEAN on a
            # re-audit with the factor still degenerate: the same self-inflicted
            # false NEGATIVE the successorless arm refuses for ``archived``, and
            # reachable through the sanctioned fold lane (measured:
            # ``apply_supersessions`` folds a draft successor with NO warning).
            return (f"; the successor it names is '{DRAFT_STATUS}', which EP "
                    "strips like a terminal input (#780) — it needs promoting to "
                    "live as well, or the re-point leaves the factor degenerate")
        if s["carrier_terminal"]:
            return ("; the successor it names is ITSELF "
                    f"{_quoted(_status_or_flag(s['carrier_status'], s['carrier_outdated']))}"
                    f", and it {_head_fact(s)}")
        return ""

    def _head_fact(s: dict) -> str:
        """What the row PROVES about the head of a terminal successor's chain.

        ⛔ ONE source for BOTH emitted strings. The ``detail`` and the ``fix``
        describe the same row, so a "target the LIVE head of that chain" in one and
        "there is no chain to follow" in the other is not a wording inconsistency —
        one of them is FALSE, and the reader cannot tell which (measured: an
        ``archived`` successor with no ``supersededBy`` emitted both, from one
        finding). "The LIVE head of that chain" is only emitted when the next hop
        resolves to exactly one NON-terminal Object; every other arm names what it
        actually found, and never asserts a live head it has not seen — the #7819
        rule this module refuses to emit.
        """
        if not s["carrier_sb"]:
            return ("names NO successor, so there is no chain to follow — re-point "
                    "onto whatever now carries the claim, or delete the edge")
        if s["carrier_sb"] == s["sup_sb"]:
            return ("names ITSELF as its own successor, so that chain loops with no "
                    "live head — re-point onto whatever now carries the claim, or "
                    "delete the edge")
        if not s["next_count"]:
            return (f"names, in its own supersededBy, {_quoted(s['carrier_sb'])} — "
                    "which answers no OTHER Object either; if a Subject or Point "
                    "carries that name, re-point onto that node — otherwise the "
                    "chain has no resolvable head, so re-point onto whatever now "
                    "carries the claim or delete the edge")
        if s["next_count"] > 1:
            return (f"names {s['next_count']} Objects as its own successor — the "
                    "next hop is exactly the ambiguity this check refuses to "
                    "resolve, so name the LIVE head by ID")
        if s["next_terminal"]:
            return (f"names the next link ({_quoted(s['carrier_sb'])}), which is "
                    f"{_quoted(_status_or_flag(s['next_status'], s['next_outdated']))}"
                    " too, so it is not a head either — "
                    "resolve that chain to a live Object before writing, and if it "
                    "ends with none, re-point onto whatever now carries the claim or "
                    "delete the edge")
        # ⛔ The SAME two non-participating shapes hop 1 knows about, at hop 2: a
        # ``draft`` is not terminal (``is_terminal_status`` is False) and an
        # operator-shaped node is excluded from the input selection, so neither is a
        # head — and both were announced as "which is LIVE — target THAT" (measured:
        # the prescribed re-point leaves the factor producing NO belief, and a
        # re-audit reads CLEAN).
        if s["next_is_operator"]:
            return (f"names the next link ({_quoted(s['carrier_sb'])}), which is "
                    "OPERATOR-SHAPED (is_operator=true) — EP excludes it from a "
                    "factor's inputs (#9269), so targeting it leaves the operator "
                    "with NO factor and a re-audit reading CLEAN: target a CLAIM "
                    "successor")
        if (s["next_status"] or "") == DRAFT_STATUS:
            return (f"names the next link ({_quoted(s['carrier_sb'])}), which is "
                    f"'{DRAFT_STATUS}' — EP strips a draft input like a terminal one "
                    "(#780), so targeting it leaves the factor degenerate: it needs "
                    "promoting to live as well")
        return (f"names the next link ({_quoted(s['carrier_sb'])}), which is LIVE — "
                "target THAT, not the immediate successor")

    def _active_edge_detail(s: dict) -> str:
        """The consequence, at its measured scope, for THIS row's mechanism.

        Two mechanisms live behind one pattern: an operator INPUT (EP builds the
        factor and then strips it) and a DIRECT edge (EP builds no factor at all,
        so the claim loses the support silently and there is no skipped-factor
        record to find). Stating the first for the second would send a reader to a
        diagnostic that never exists — which is why ``is_operator`` is EP's
        predicate and not the raw flag.
        """
        source = (s["active_id"] if s["active_id"] is not None
                  else "<no id or eventId>")
        detail = (f"Superseded Object {_named(s['sup_name'])} still carries an "
                  f"active {s['edge_type']} edge from {source}")
        if s["shape"] and not s["is_operator"]:
            # NEITHER arm — see the ``shape`` note in ``_4b_state``.
            detail += (
                " — the Object lane moved no edges on supersession, and EP builds NO "
                "factor for this endpoint in EITHER arm: the operator arm requires "
                "the :Point label, and the direct-edge arm refuses an "
                "operator-shaped endpoint. So the claim never counted this support "
                "and no skipped-factor record exists (#9269/#11826)")
        elif s["is_operator"]:
            fwd, bwd = direction_flows(s["direction"])
            if not (fwd or bwd):
                # ⛔ A NEUTRAL operator (``direction='-'``, e.g.
                # ``create_operator(..., semantics='related')``) transfers NO
                # confidence **by design**, so no belief is lost when its input goes
                # terminal — and the belief-loss ladder below would report a HIGH
                # finding whose rationale is void (measured: posteriors identical
                # before and after the supersession, successor rewired or not).
                detail += (
                    " — the Object lane moved no edges on supersession, but this "
                    "operator is '-' (neutral): the connection transfers NO "
                    "confidence by design, so no belief is lost here and rewiring "
                    "the successor restores nothing that was ever carried. The "
                    "stale edge is leftover wiring, safe to delete (#11826)")
            else:
                detail += (
                    " — the Object lane moved no edges on supersession, so EP "
                    "strips that terminal input: the factor over its REMAINING "
                    "inputs is then re-scored without it — ONE left is skipped as "
                    "degenerate (#780/#2422: ep.py's ``len(input_ids) < 2``), two "
                    "or more run without it, and an operator whose ONLY input this "
                    "is has no factor to begin with, so nothing is lost. A "
                    "DIRECTIONAL operator is skipped outright "
                    "when the stripped input is its idx-0 source (source_stripped). "
                    "The successor inherits no belief from this edge unless it was "
                    "separately rewired into a slot EP reads (#11826)")
        else:
            detail += (
                " — the Object lane moved no edges on supersession, and EP builds "
                "NO factor for a direct edge: with a terminal endpoint the row is "
                "excluded before evaluation, so there is no skipped-factor record "
                "for it and the claim silently loses that support (#11826)")
        if (not s["carrier_count"] and s["sup_id"] is not None
                and s["sup_sb"] and s["sup_name"] == s["sup_sb"]):
            detail += ("; and its supersededBy names the Object ITSELF, so no OTHER "
                       "Object answers it — name the real successor")
        else:
            detail += _successor_note(s)
        if s["active_id"] is None:
            detail += ("; that endpoint carries no identity, so it cannot be named "
                       "by the repair")
        return detail

    def _chain_tail(s: dict) -> str:
        """What to do when the named successor is not a live target.

        Emitted only for facts the row carries. A terminal successor with no name
        to follow has NO chain (the Object lane can terminalize without a
        supersession), and one whose next name answers no OTHER Object has no
        resolvable head — telling the reader to "target the LIVE head" in either
        state would name a node that does not exist.
        """
        if not s["carrier_count"] or s["carrier_count"] > 1:
            return ""
        status = s["carrier_status"]
        succ = f"'{s['sup_sb']}'" if bool(s["sup_sb"]) else "the successor"
        if s["carrier_is_operator"]:
            return (f" ⛔ {succ} is OPERATOR-SHAPED (is_operator=true), which EP "
                    "excludes from a factor's inputs (#9269): re-pointing onto it "
                    "drops the input entirely and leaves the operator with NO factor "
                    "(measured: confidences empty, successor absent) — target a CLAIM "
                    "successor")
        if (status or "") == DRAFT_STATUS:
            return (f" ⛔ {succ} is '{DRAFT_STATUS}', which EP strips the same way "
                    "(#780): promote it to live as well, or the re-point leaves "
                    "the factor degenerate")
        if not s["carrier_terminal"]:
            return ""
        return (f" ⛔ {succ} is ITSELF "
                f"{_quoted(_status_or_flag(status, s['carrier_outdated']))} "
                f"and it {_head_fact(s)}")

    def _active_edge_fix(s: dict) -> str:
        """The repair for this row, chosen by the state the row carries.

        Five states the anchor does not rule out, each with its own repair:

          * NO RESOLVABLE SUCCESSOR — the transfer has no destination, so naming one
            is step one. ``archived`` is offered only to say what it does NOT clear
            (#11824's two-arm rule is a write-door rule; archiving here keeps the
            input stripped AND drops the node out of this check's anchor, so a
            re-audit would read clean over a dead factor).
          * AN AMBIGUOUS NAME (N≥2 carriers, #3573) — the check cannot pick one, so
            the first step is naming the Object meant.
          * A DIRECT edge — no factor is built and no ``idx`` exists, so the repair
            writes the equivalent edge onto the successor.
          * The operator's idx-0 SOURCE of a non-bidirectional operator — EP drops
            the whole factor while that slot is terminal, and DELETING the edge
            alone renumbers a live target into idx 0 and inverts the factor's
            direction (``ep.py``'s ``source_stripped`` note).
          * Any other operator input — re-point, preserving ``idx`` where the edge
            carries one; a successor that already carries an equivalent input needs
            only the delete.
        """
        q = _quoted(s["sup_id"]) if s["sup_id"] is not None else _quoted(s["sup_name"])
        # The endpoint that CARRIES the operator shape — never the superseded anchor.
        _shaped = (_quoted(s["active_id"]) if s["active_id"] is not None
                   else _quoted(s["sup_name"]))
        if s["sup_id"] is None:
            # ⛔ ``tortoise_update_entity`` keys on the ID, so embedding a NAME in its
            # first argument addresses a key that does not exist. Only a raw
            # ``REMOVE o.id`` reaches this state (no SDK writer mints an id-less
            # Object), but the string must not hand out a call that cannot work —
            # 4c already answers this state in prose, and the two checks must not
            # disagree about it.
            return (
                f"No id-keyed repair is possible for the superseded Object named "
                f"{_named(s['sup_name'])} (it carries no id, so "
                "tortoise_update_entity cannot address it): give it one, then name "
                f"the successor on it and re-point this {s['edge_type']} input onto "
                "that successor, deleting the edge into the old endpoint. Archiving "
                "it instead does NOT clear this: EP strips a terminal input either "
                "way, so the edge must still be deleted or re-pointed onto whatever "
                "now carries the claim")
        if not s["carrier_count"]:
            # ⛔ "Nothing to re-point onto" would be FALSE whenever a :Subject or
            # :Point carries the name: both are nodes EP accepts as an operator
            # input, and this check's carrier probe is ``:Object``-scoped. State the
            # fact it measured and name that escape, rather than sending the reader
            # to mint a second successor.
            fix = (
                f"No :Object answers the successor name this one carries, so there "
                f"is no Object to re-point this {s['edge_type']} input onto: name "
                f"the successor — tortoise_update_entity({q}, {{'supersededBy': "
                f"'<the successor name>'}}) — then re-point the input onto it and "
                f"delete the edge into {q}. If a Subject or Point carries that "
                f"name, re-point onto THAT node instead and delete this edge. "
                # ⛔ THREE mechanisms, so the "archiving does not clear this" clause
                # has to be three-way: a shaped non-:Point endpoint gets NEITHER arm
                # (no factor at all), a genuine direct edge is excluded BEFORE
                # evaluation (no factor, no record), and an OPERATOR still builds the
                # factor and records the strip. One sentence for all three left the
                # operator arm asserting a mechanism it does not have — measured:
                # skipped_factors reason 'degenerate' appears in exactly that arm.
                + (("Archiving it instead does NOT clear this: EP builds no factor "
                    "for that endpoint in EITHER arm, so archiving only drops it out "
                    "of this anchor — the claim's support has to be wired through a "
                    ":Point operator (or the shape cleared) regardless")
                   if (s["shape"] and not s["is_operator"]) else
                   (f"Archiving it instead — tortoise_update_entity({q}, {{'status': "
                    f"'archived'}}) — does NOT clear this: EP still builds the factor "
                    f"and strips that terminal input, recording the skip (reason "
                    f"'degenerate', or 'source_stripped' when this edge is its idx-0 "
                    f"source), so the edge must still be deleted or re-pointed onto "
                    f"whatever now carries the claim")
                   if s["is_operator"] else
                   (f"Archiving it instead — tortoise_update_entity({q}, {{'status': "
                    f"'archived'}}) — does NOT clear this: the direct-edge row is "
                    f"excluded BEFORE evaluation while the endpoint is terminal, so "
                    f"no factor is built and no skipped-factor record appears either "
                    f"way. The edge must still be deleted or re-pointed onto "
                    f"whatever now carries the claim")))
            if s["shape"] and not s["is_operator"]:
                # ⛔ The SAME neither-arm fact the resolvable path states, in the
                # successorless one: a shaped non-``:Point`` endpoint is in neither
                # EP arm, so "write the equivalent edge" (the direct-edge sentence)
                # changes nothing and a re-audit reads CLEAN over a claim whose
                # support was never there.
                fix += ("; and no edge rewrite repairs this — EP builds NO factor "
                        "in EITHER arm (the operator arm needs the :Point label, the "
                        "direct-edge arm refuses an operator-shaped endpoint): "
                        f"clearing the shape — tortoise_update_entity({_shaped}, "
                        "{'is_operator': False}) — or wiring this claim through a "
                        ":Point operator is what restores the support")
            elif not s["is_operator"]:
                fix += ("; and this is a DIRECT edge, so there is no factor to "
                        "re-point — write the equivalent edge onto that successor "
                        "and delete this one")
            return fix
        prefix = ""
        if s["carrier_count"] > 1:
            prefix = (f"FIRST name the successor meant: {_quoted(s['sup_sb'])} is "
                      f"carried by {s['carrier_count']} Objects (#3573) and this "
                      "check cannot pick one, so re-point onto the intended one "
                      "by ID. ")
        successor = f"'{s['sup_sb']}'" if bool(s["sup_sb"]) else "its successor"
        tail = _chain_tail(s)
        if s["shape"] and not s["is_operator"]:
            # ⛔ NEITHER arm: "write the equivalent edge" would change nothing, so the
            # repair is to clear the SHAPE or route the claim through a :Point
            # operator — measured: re-pointing leaves EP building no factor at all.
            # ⛔ The shape belongs to the ACTIVE ENDPOINT, never to the superseded
            # anchor ``q`` (``active_id`` differs by construction: the anchor is
            # ``status='superseded'`` while ``active`` must be live), so a call on
            # ``q`` would set the flag on a node that never carried it.
            return prefix + (
                "No edge rewrite repairs this: EP builds NO factor in either arm "
                "(the operator arm needs the :Point label, the direct-edge arm "
                "refuses an operator-shaped endpoint), so clearing the shape — "
                f"tortoise_update_entity({_shaped}, {{'is_operator': False}}) — or "
                "wiring this claim through a :Point operator is what restores the "
                "support" + tail)
        if not s["is_operator"]:
            return prefix + (
                f"Re-point this {s['edge_type']} edge off {q} onto the successor "
                f"({successor}): it is a DIRECT edge, so there is no factor to "
                f"re-point and no edge idx to preserve — write the equivalent "
                f"edge from the same source onto the successor and delete this "
                f"one." + tail)
        fwd, bwd = direction_flows(s["direction"])
        if not (fwd or bwd):
            # ⛔ A NEUTRAL operator transfers NO confidence **by design**, so
            # nothing is lost here and the belief-loss arms below (the SOURCE-slot
            # warning in particular) would assert a mechanism EP never runs:
            # ``_update_nary_factor`` returns at ``if not (fwd or bwd)`` before any
            # renumbering (measured: posteriors identical before and after, with
            # the successor rewired and with the stale edge deleted).
            return prefix + (
                f"Re-point this {s['edge_type']} input from {q} onto the "
                f"successor ({successor}) if the claim should still be wired "
                f"there, then delete the edge into {q}. ⛔ Nothing was LOST by "
                f"leaving it: this operator is '-' (neutral), so no confidence "
                f"transferred through this edge either way (measured: posteriors "
                f"identical before and after the supersession)" + tail)
        if s["idx"] == 0 and fwd != bwd:
            return prefix + (
                f"Write the successor ({successor}) into the SOURCE slot: this "
                f"{s['edge_type']} edge is the operator's idx-0 source and the "
                f"operator is {_quoted(s['direction'])}-directional, so EP drops "
                f"the whole factor while that slot is terminal — and DELETING the "
                f"edge alone is not the repair: any SURVIVING input is renumbered "
                f"into idx 0 and the factor's direction inverts (ep.py's "
                f"source_stripped note). Re-point idx 0 onto the successor, then "
                f"delete the edge into {q}." + tail)
        if s["idx"] is None:
            return prefix + (
                f"Re-point this {s['edge_type']} input from {q} onto the successor "
                f"({successor}): an explicit edge write against the successor's "
                f"ID, then delete the edge into {q}. ⛔ This input edge carries NO "
                f"idx (a legacy or operator-less write), so stamp one — idx 0 is "
                f"the source slot EP reads — or the source cannot be told from the "
                f"targets. No SDK door rewires an operator's inputs" + tail)
        return prefix + (
            f"Re-point this {s['edge_type']} input from {q} to the successor "
            f"({successor}): an explicit edge write against the successor's ID "
            f"that PRESERVES the edge idx (the slot EP reads), then delete the "
            f"edge into {q}. A successor that ALREADY carries an equivalent input "
            f"needs only that delete. No SDK door rewires an operator's inputs"
            + tail)
    _record("superseded_object_active_edges", [
        AuditIssue(
            issue_type="superseded_object_active_edges",
            severity="high",
            node_id=str(s["sup_id"]) if s["sup_id"] is not None else str(s["sup_name"]),
            detail=_active_edge_detail(s),
            fix=_active_edge_fix(s),
        )
        for s in map(_4b_state, rows4b)
    ], count4b)

    # ── 4c. superseded_by_unresolvable (medium) ─────────────────
    # #11826's second measured defect, and a regression against a recorded
    # ruling rather than a design gap: ``supersededBy`` is a NAME stored in a
    # column, while #3590 made the Object name MUTABLE and the id the identity.
    # Rename the successor and the pointer dangles — measured: supersede
    # ``PolicyV1`` → ``PolicyV2``, rename to ``PolicyV2b``, and
    # ``supersededBy='PolicyV2'`` resolves to NO node. The superseded Object then
    # holds no relationship of ANY type to its successor, nothing repairs it, and
    # before this check nothing warned.
    #
    # SCOPE, declared rather than implied: this check is `:Object`-anchored, and a
    # `:Subject` carries the same name-keyed `supersededBy` (#1423's lane), so a
    # Subject's dangling pointer stays silent here — a KNOWN false-clean for that
    # label, not an oversight. Widening it means deciding whether the two families
    # answer one question (the #11824 target model), which is not this change's.
    #
    # "UNRESOLVABLE" IS ZERO CARRIERS, and the distinction is load-bearing: N≥2
    # Objects sharing one name is a DIFFERENT fact (same-name coexistence,
    # #3573) and is NOT reported here — folding the two would flag an AMBIGUOUS
    # pointer as a DEAD one, and the two need different repairs (disambiguation
    # vs. re-pointing). N≥1 means the name still addresses something, so this
    # check stays silent even where a read path would refuse to pick one.
    #
    # ⛔ A CARRIER IS ANOTHER NODE (`id(carrier) <> id(sup)` — internal identity,
    # because a NULL `id` property would make the comparison NULL and bind
    # nothing), and that exclusion is
    # the whole reason a SELF-pointer is visible at all: `supersededBy` naming the
    # superseded Object itself satisfies a plain name match (the node carries that
    # name by definition), so without it the pointer resolves to itself and NOTHING
    # in this module reported it — measured: `update_entity(obj,
    # supersededBy='<its own name>')` is accepted, and the node "has a successor"
    # that is itself. With the exclusion, a self-pointer reads as zero OTHER
    # carriers — the same repairable fact as a dangling name (name the real
    # successor) — while a supersession by a DIFFERENT node that happens to share
    # the name still resolves (1 carrier, silent).
    count4c = _count(
        proj,
        "MATCH (sup:Object) WHERE sup.status = 'superseded' "
        "AND sup.supersededBy IS NOT NULL "
        "AND sup.supersededBy <> ''\n"
        # #6976: load-bearing `WITH sup` — see the count4b note above.
        "WITH sup\n"
        # An OPTIONAL MATCH that binds nothing yields ONE row holding NULL; two
        # carriers yield two non-NULL rows that the WHERE then drops. So the
        # filter is `carrier IS NULL` (zero carriers), never "fewer than two".
        "OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy "
        "AND id(carrier) <> id(sup)\n"
        "WITH sup, carrier WHERE carrier IS NULL\n"
        "RETURN count(DISTINCT sup)",
    )
    rows4c = _rows(
        proj,
        "MATCH (sup:Object) WHERE sup.status = 'superseded' "
        "AND sup.supersededBy IS NOT NULL "
        "AND sup.supersededBy <> ''\n"
        "WITH sup\n"
        "OPTIONAL MATCH (carrier:Object) WHERE carrier.name = sup.supersededBy "
        "AND id(carrier) <> id(sup)\n"
        "WITH sup, carrier WHERE carrier IS NULL\n"
        "RETURN DISTINCT sup.id, sup.name, sup.supersededBy "
        f"LIMIT {SAMPLE_LIMIT}",
    )
    _record("superseded_by_unresolvable", [
        AuditIssue(
            issue_type="superseded_by_unresolvable",
            severity="medium",
            node_id=str(sup_id) if sup_id is not None else str(sup_name),
            # ⛔ ZERO CARRIERS is the fact; the CAUSE is not established by it, so
            # the string names the candidate causes instead of asserting one — AND a
            # SELF-pointer is its own state, spelled out, because the general clause
            # would then be false ("NO Object carries that name" is untrue: the node
            # carries it by definition; what is missing is a DIFFERENT node). Measured:
            # `update_entity(obj, supersededBy='<its own name>')` is accepted.
            detail=((f"Superseded Object {_named(sup_name)} names ITSELF as its "
                     "successor "
                     "(supersededBy is its own name, which no OTHER Object answers), "
                     "so the name-keyed pointer names no successor at all — name the "
                     "real successor (#3590/#11826)")
                    if (bool(sb_name) and sb_name == sup_name) else
                    (f"Superseded Object {_named(sup_name)} names successor "
                     f"{_named(sb_name)}, but NO Object carries that name "
                     "(a renamed successor, one that was deleted or never "
                     "created, or a Subject/Point that this :Object anchor does "
                     "not resolve — the name-keyed pointer dangles, "
                     "#3590/#11826)")),
            # ⛔ The PROPS form is the door's own signature — ``tortoise_update_entity(
            # id, props)`` (``mcp_server.py``) takes the properties as ONE dict, so a
            # keyword spelling (``status='superseded'``) is a ``TypeError`` at the
            # caller: a repair that always raises is worse than no repair, which is
            # the rule the #7819 note in this module already records. Same shape as
            # the other `fix` strings here (``tortoise_update_point(id, {...})``).
            # ⛔ A Subject/Point carrier makes the pointer NOT dangling and the
            # repair below a no-op, so the string names that state rather than
            # sending the reader to re-write the name it already holds (4b says the
            # same thing about the same row in the same run).
            fix=((f"Re-point the successor link — UNLESS a Subject or Point carries "
                  "that name, in which case the pointer is NOT dangling and the "
                  "input should be re-pointed onto that node instead. Otherwise: "
                  f"tortoise_update_entity({_quoted(sup_id)}, "
                  "{'status': 'superseded', 'supersededBy': "
                  "'<the successor CURRENT name>'})")
                 if sup_id is not None else
                 (f"Set the successor name on the Object named {_named(sup_name)} "
                  f"to the CURRENT name of {_named(sb_name)} — UNLESS a Subject or "
                  "Point carries that name, in which case the pointer is NOT "
                  "dangling and the input should be re-pointed onto that node "
                  "instead")),
        )
        for sup_id, sup_name, sb_name in rows4c
    ], count4c)

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
    # #11702 cycle 24: the operator's mirror is read through the BAND, like every
    # other belief read — an out-of-band value (`-0.5`, a legacy `"0x1p4"`) is not
    # a confidence, and unbanded it was served in the issue detail ("conf=-0.5").
    _op_conf = belief_mirror_cypher("op.confidence")
    conf_w = (f"{_op_conf} IS NOT NULL AND {_op_conf} <= "
              f"{MITIGATION_CONF_THRESHOLD}")
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
        "RETURN op.id, " + _op_conf + ", "
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
    # ⚠ #11646 made the ingest producer's shape (``2023/05/20 (Sat) 02:21``)
    # ORDERABLE, and a zone-less date-TIME is read in the READER's zone (#3982),
    # so this check's VERDICT can differ across hosts for a MIXED PAIR. What
    # decides the disagreement is where the pair's separation falls against the
    # two hosts' OFFSETS — the distance alone decides nothing: a naive bound's
    # epoch is ``wall_clock - reader_offset``, so two hosts disagree only when
    # the separation falls between their offsets, the closed end being the
    # ACCEPTING host's offset (higher for a producer-as-START, lower for a
    # producer-as-END — see ``commit_schema.validate_validity_window``). When
    # BOTH bounds are naive ONE host reads both, so the reader's offset CANCELS
    # — except where the pair straddles an OFFSET TRANSITION on that host (a DST
    # change OR a permanent standard-offset change) that leaves ONE bound in a
    # nonexistent hour AND reads the OTHER at the POST-transition offset — which
    # is a WALL-INVERTED pair whose END is in the gap, or a non-inverted pair
    # whose START is in it and whose END is after it. A gap bound is read at the
    # PRE-transition offset, so a non-inverted END pair and a WALL-INVERTED START
    # pair read BOTH bounds at that same offset and cannot move. Given different
    # offsets the verdict moves per orientation, because only the NONEXISTENT
    # bound is shifted — as the END, a WALL-INVERTED pair flips at
    # ``separation <= shift`` (the shift un-inverts it into a well-formed window
    # of length ``shift - separation``, zero-length exactly at equality; measured
    # at a 60 min shift: 20/30/60 min flip, 61 does not), while a NON-inverted END
    # pair never flips: BOTH bounds land on the SAME pre-transition offset, so
    # the gap host reads the same separation-long window as any other host. As
    # the START, with the other bound AFTER the transition, the flip needs
    # ``separation < shift``, because at exactly the shift the GAP host reads a
    # zero-length window while a transition-free host reads a shift-long one, and
    # both accept (measured: 20 and 59 min flip, 60 min agrees); a WALL-INVERTED
    # START pair never flips, for the same offset reason. Measured: the US
    # spring-forward pair flips between UTC and America/Los_Angeles, and
    # Asia/Pyongyang's PERMANENT +08:30 → +09:00 change in 2018 does the same with
    # no DST at all. This check reports rather
    # than refuses, but the verdict it reports is that measure under a
    # host-dependent key: not a new asymmetry, but a newly reachable one (the
    # shape used to bucket as unparseable, so the pair was skipped everywhere).
    # It is RECORDED and PINNED rather than changed: flipping the reading is a
    # reopen of #3982, and anchoring the producer's shape on its own is the
    # SEPARATE decision recorded on #11646 — either way a decision, not a
    # cleanup here. The pin is
    # ``tests/test_11646_producer_date_shape.py``::
    # test_a_mixed_producer_pair_is_host_dependent_and_that_is_recorded,
    # which asserts ``validate_validity_window``'s verdict at FOUR zones (UTC,
    # Asia/Kolkata, America/Los_Angeles, Africa/Johannesburg) plus a fifth child
    # at Asia/Pyongyang for the permanent-shift case.
    #
    # ⛔ The `fix` is LIFECYCLE-AWARE (#7819), and the scan deliberately keeps
    # terminal rows. It must not drop them: the population this check exists
    # for is the #4021 legacy supersession inversion, whose damaged row is the
    # PREDECESSOR — which `supersede_point` terminalizes — and
    # `get_historical_knowledge` walks exactly those predecessors, so an
    # inverted window on a dead claim is still a live §5 defect, not a moot
    # one. What did change is that `update_point` now REFUSES a temporal write
    # on a terminal claim, so the repair this check published
    # (`tortoise_update_point(id, {'validTo': …})`) is unexecutable for them.
    # A remedy that always raises is worse than no remedy: the fix string
    # branches, and the terminal branch states the truth instead of naming a
    # call the SDK refuses (`tests/test_5361_inverted_window_audit.py` pins
    # both branches).
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
            "RETURN n.id, n.validFrom, n.validTo, n.status, "
            "coalesce(n.outdated, false) "
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
        for wid, w_from, w_to, w_status, w_outdated in window_rows:
            k_from = _created_sort_key(w_from)
            k_to = _created_sort_key(w_to)
            if k_from[0] != 0 or k_to[0] != 0:
                continue  # unparseable bound — owned by #5360, not this check
            if k_to < k_from:
                count8 += 1
                if len(issues8) < SAMPLE_LIMIT:
                    # #7819: the repair differs by LIFECYCLE. A terminal claim's
                    # temporal record is frozen by the shared guard, so naming
                    # `tortoise_update_point` there would prescribe a call that
                    # always raises; say what the state IS instead.
                    #
                    # ⛔ The refusal is the DOOR's, not the SDK's: name
                    # `tortoise_update_point` and never "through the SDK".
                    # `update_entity`'s Point arms write caller props with no
                    # lifecycle guard and no window validator (#9649), so a
                    # window re-stamp is still reachable there on a dead claim —
                    # claiming otherwise in a HIGH finding would hand the
                    # operator a second unexecutable statement.
                    if is_terminal_status(w_status, bool(w_outdated)):
                        _fix = (
                            f"Not repairable through tortoise_update_point: "
                            f"point {wid} is a terminal claim, whose temporal "
                            "record that verb refuses (#7819) — the window "
                            "stays as stored (a retroactive correction is "
                            "#5362, not shipped; the unguarded update_entity "
                            "door is #9649)"
                        )
                    else:
                        _fix = (f"Repair the window: tortoise_update_point("
                                f"{_quoted(wid)}, {{'validTo': "
                                "'<at-or-after validFrom>'})")
                    issues8.append(AuditIssue(
                        issue_type="inverted_validity_window",
                        severity="high",
                        node_id=str(wid),
                        detail=(f"Point {wid} has an inverted validity window "
                                f"(validFrom {w_from!r} > validTo {w_to!r}) — no "
                                "instant is covered, so temporal queries cannot "
                                "see it"),
                        fix=_fix,
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
