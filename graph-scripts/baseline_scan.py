#!/usr/bin/env python3
"""#334 Phase 1 — baseline scan for the Work Graph Wiring Remediation epic.

READ-ONLY scan that detects the wiring-anomaly classes the epic targets
(stub Points, degenerate operators, NULL-status Points, missing sourceKind,
neutral-tier Sources, missing extractedFrom backing, Subject stubs) and emits
a JSON report of counts + sample IDs. Writes NOTHING to the graph — all
queries are MATCH/RETURN; the only artifact is the report itself (stdout or
--output file).

Per the scoping doc (Phase 1, "Pre-migration (no graph writes)" + Phase 2
"audit variant home = graph-scripts/"), this is the #334-owned pre-migration
baseline instrument. The Source-level audit variant (Phase 2) builds on it;
#348 ships the ongoing-maintenance CLI product. 2026-08-15 owner decision:
legacy/local graphs are irrelevant — tooling is validated on fresh graphs
now, and the real baseline scan runs on the production URI at launch.

Report sections (counts + samples, all read-only):
  - graph_stats / status_distribution   (draft/live distribution — EP default
    excludes 'draft', scoping clarification #4)
  - stub_points                         content='[missing]' (canonical stub from
    _create_edges, edges.py ~L45-95) + non-operator Points with missing content
  - stub_edges                          IMPL/NAND/INPUT edges with stub endpoints
    (the dead-edge class EP propagates through)
  - degenerate_operators                operators with <2 IMPL/NAND inputs
    (the engine's own exclusion threshold, projection/__init__.py ~L1317)
  - null_status_points                  status IS NULL → treated as LIVE by
    _live_only (tortoise/live.py; analyze.py ~L548)
  - missing_source_kind                 Source-level (primary, exit criterion 3)
    + legacy Point-level
  - source_tier_neutral                 Sources resolving to no tier (credibilityTier /
    tier-form sourceKind / registry default; source_credibility.resolve_tier)
  - extracted_from                      Points with Source backing (edge or legacy
    property) vs evidence without any Source backing (exit criterion 4)
  - subject_stubs                       Subject nodes auto-created by the stub path
    (id = name, subjectKind='other' — edges.py _create_about_edges; exit
    criterion 8)
  - cap_skip                            UNVERIFIABLE from graph state — the edge is
    silently skipped (no trace); source of truth = event log > content-string,
    neither available → class UNVERIFIABLE (scoping exit criterion 2). Decision
    owner + default documented (event-log normalization decision, Phase 1).

Bounded-materialization contract: large legacy populations (NULL-status
points, sourceKind-less Points, evidence without Source backing, tier-neutral
Sources, degenerate operators) are NEVER fully materialized into Python —
every section reports an exact Cypher COUNT plus a bounded sample (LIMIT in
the query, SAMPLE_LIMIT ids).

Known-issue note (NOT fixed here): tortoise/ep.py:1060 `random.shuffle(factors)`
is unseeded — Phase 5's EP determinism protocol must pin random.seed (+
PYTHONHASHSEED if cross-process). Phase 0/1 is pre-migration read-only
tooling; the EP snapshot/restore script is a later-phase prerequisite.

Usage:
  TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise \
    python3 graph-scripts/baseline_scan.py
  python3 graph-scripts/baseline_scan.py --path /tmp/fresh-graph.db
  python3 graph-scripts/baseline_scan.py --output baseline-report.json

Exit codes: 0 = scan completed (report may contain findings — scan never
fails on findings; it fails only on unreachable/misconfigured target, exit 1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Allow running from any directory (repo-root import).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Sibling graph-scripts/ module (script dir is sys.path[0] when run directly;
# tests insert graph-scripts/ explicitly).
from connectivity_gate import redact_uri

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"
SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")
SAMPLE_LIMIT = 10  # sample IDs per section (counts are always exact)


# ── Connection resolution (mirrors connectivity_gate.py) ─────────────────

def classify_target(uri: str | None, path: str | None) -> dict:
    """Classify a connection target (uri mode vs embedded-path mode)."""
    if uri is not None:
        scheme = uri.split("://", 1)[0]
        if scheme not in SUPPORTED_URI_SCHEMES:
            raise ValueError(
                f"Unsupported scheme {scheme!r} — expected one of "
                f"{'/'.join(SUPPORTED_URI_SCHEMES)} (or pass --path for "
                "embedded FalkorDBLite)."
            )
        return {"mode": "uri", "uri": uri}
    if path is not None:
        return {"mode": "embedded", "path": path}
    env_uri = os.environ.get("TORTOISE_DB_URI")
    if env_uri:
        return classify_target(env_uri, None)
    return classify_target(DEFAULT_URI, None)


def connect_projection(cfg: dict):
    """Build a FalkorProjection for the classified target (no writes).

    Embedded paths must EXIST — a missing path silently spins up a fresh empty
    FalkorDBLite database, and an all-zero "clean" scan would be misleading
    (conf-65). Fail loudly instead.
    """
    from tortoise.projection import FalkorProjection
    if cfg["mode"] == "uri":
        return FalkorProjection.from_uri(cfg["uri"])
    path = cfg["path"]
    if path != ":memory:" and not os.path.isfile(path):
        raise FileNotFoundError(
            f"embedded graph path does not exist: {path!r} — refusing to "
            "silently create a fresh empty FalkorDBLite database (an all-zero "
            "'clean' scan would be misleading). Point at an existing graph, or "
            "use a docker:// URI."
        )
    return FalkorProjection(path)


# ── Query helpers ────────────────────────────────────────────────────────

def _q(proj, cypher: str, params: dict | None = None) -> list:
    """Run a Cypher query and return the result rows (read-only)."""
    res = proj.g.query(cypher, params=params or {})
    return res.result_set or []


def _samples(rows, keys: tuple[str, ...], limit: int = SAMPLE_LIMIT) -> list[dict]:
    """Convert result rows to a list of sample dicts (bounded)."""
    return [dict(zip(keys, row)) for row in rows[:limit]]


def _count_and_samples(proj, count_cypher: str, sample_cypher: str,
                       keys: tuple[str, ...], limit: int = SAMPLE_LIMIT) -> dict:
    """Exact count + bounded samples via separate queries.

    Large legacy populations (NULL-status points, sourceKind-less Points,
    evidence without Source backing) must never be fully materialized into
    Python — the count query stays exact, samples stay bounded with the LIMIT
    pushed INTO the query so the DB does the bounding (conf-75 contract).
    """
    crows = _q(proj, count_cypher)
    count = int(crows[0][0]) if crows else 0
    sample_cypher = (sample_cypher if "LIMIT" in sample_cypher.upper()
                     else f"{sample_cypher} LIMIT {limit}")
    return {"count": count, "samples": _samples(_q(proj, sample_cypher), keys, limit)}


# ── Scan sections ────────────────────────────────────────────────────────

def _graph_stats(proj) -> dict:
    nodes = _q(proj, "MATCH (n) RETURN count(n)")[0][0]
    edges = _q(proj, "MATCH ()-[r]->() RETURN count(r)")[0][0]
    by_label = {}
    try:
        rows = _q(proj, "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) "
                        "ORDER BY count(*) DESC")
        by_label = {str(r[0]): int(r[1]) for r in rows}
    except Exception as exc:  # noqa: BLE001 — best-effort label enumeration
        by_label = {"__error__": str(exc)}
    return {"nodes": int(nodes or 0), "edges": int(edges or 0), "by_label": by_label}


def _status_distribution(proj) -> dict:
    rows = _q(proj, "MATCH (p:Point) RETURN coalesce(p.status, '<null>') AS s, "
                    "count(*) ORDER BY count(*) DESC")
    dist = {str(r[0]): int(r[1]) for r in rows}
    return {
        **dist,
        "note": "EP default excludes 'draft' (live.py _live_only); NULL status "
                "is treated as LIVE. Phase 5 scope depends on this split.",
    }


def _stub_points(proj) -> dict:
    # Canonical stub: _create_edges writes content='[missing]', is_operator=false
    stub = _count_and_samples(
        proj,
        "MATCH (s:Point) WHERE s.content = '[missing]' RETURN count(s)",
        "MATCH (s:Point) WHERE s.content = '[missing]' "
        "RETURN s.id, s.content ORDER BY s.id",
        ("id", "content"),
    )
    # Non-operator Points with missing/empty content (degenerate, not operators)
    missing_content = _count_and_samples(
        proj,
        "MATCH (p:Point) WHERE p.is_operator = false AND "
        "(p.content IS NULL OR trim(p.content) = '') RETURN count(p)",
        "MATCH (p:Point) WHERE p.is_operator = false AND "
        "(p.content IS NULL OR trim(p.content) = '') RETURN p.id ORDER BY p.id",
        ("id",),
    )
    return {
        "count": stub["count"],
        "samples": stub["samples"],
        "also_missing_content_non_operator": {
            **missing_content,
            "note": "Points with NULL/empty content that are NOT operators — "
                    "degenerate non-stub Points (distinct from '[missing]' stubs).",
        },
        "note": "Stub Points auto-created by _create_edges (projection/edges.py "
                "~L45-95) when an operator references a missing short-ID Point.",
    }


def _stub_edges(proj) -> dict:
    # Exact per-type counts + bounded samples. Undirected match: IMPL/NAND
    # edges point operator→stub, INPUT edges point stub→operator — both are
    # stub endpoints.
    rows = _q(proj, "MATCH (s:Point {content:'[missing]'})-[r:IMPL|NAND|INPUT]-(o:Point) "
                    "RETURN type(r) AS rel, count(r) AS c")
    by_type = {str(r[0]): int(r[1]) for r in rows}
    samples = _count_and_samples(
        proj,
        "MATCH (s:Point {content:'[missing]'})-[r:IMPL|NAND|INPUT]-(o:Point) "
        "RETURN count(r)",
        "MATCH (s:Point {content:'[missing]'})-[r:IMPL|NAND|INPUT]-(o:Point) "
        "RETURN type(r) AS rel, o.id AS operator_id, s.id AS stub_id "
        "ORDER BY s.id LIMIT 200",
        ("rel", "operator_id", "stub_id"),
        limit=200,
    )
    return {
        "count": sum(by_type.values()),
        "by_edge_type": by_type,
        "samples": samples["samples"],
        "note": "Dead edges from real operators to stub endpoints — EP "
                "propagates confidence through these (scoping problem (a)).",
    }


def _degenerate_operators(proj) -> dict:
    base = ("MATCH (o:Point {is_operator: true}) "
            "OPTIONAL MATCH (o)-[e:IMPL|NAND]->(:Point) "
            "WITH o, count(e) AS n_inputs WHERE n_inputs < 2 ")
    crows = _q(proj, base + "RETURN count(o)")
    count = int(crows[0][0]) if crows else 0
    return {
        "count": count,
        "threshold": "IMPL|NAND input count < 2 — the engine's own exclusion "
                     "threshold (projection/__init__.py extract_svbp_factors: "
                     "'Operators with <2 inputs are excluded').",
        "samples": _samples(_q(proj, base + "RETURN o.id, o.op_type, n_inputs "
                                            "ORDER BY n_inputs "
                                            f"LIMIT {SAMPLE_LIMIT}"),
                            ("id", "op_type", "n_inputs")),
        "note": "Degenerate operators carry no usable factor; Phase 3 runs a "
                "reviewable degenerate-operator pass (scoping exit criterion 2). "
                "Count is exact (Cypher count); samples are bounded.",
    }


def _null_status_points(proj) -> dict:
    return {
        **_count_and_samples(
            proj,
            "MATCH (p:Point) WHERE p.status IS NULL RETURN count(p)",
            "MATCH (p:Point) WHERE p.status IS NULL "
            "RETURN p.id, p.is_operator ORDER BY p.id",
            ("id", "is_operator"),
        ),
        "note": "NULL status is treated as LIVE by _live_only (live.py: "
                "'coalesce($st, n.status, \\'live\\')') — these Points already "
                "participate in EP. Includes stub Points (stubs carry no "
                "status), which is why this count overlaps stub_points.",
    }


def _missing_source_kind(proj) -> dict:
    src = _count_and_samples(
        proj,
        "MATCH (s:Source) WHERE s.sourceKind IS NULL RETURN count(s)",
        "MATCH (s:Source) WHERE s.sourceKind IS NULL "
        "RETURN s.url, s.title ORDER BY s.url",
        ("url", "title"),
    )
    pts = _count_and_samples(
        proj,
        "MATCH (p:Point) WHERE p.is_operator = false AND p.sourceKind IS NULL "
        "RETURN count(p)",
        "MATCH (p:Point) WHERE p.is_operator = false AND p.sourceKind IS NULL "
        "RETURN p.id ORDER BY p.id",
        ("id",),
    )
    return {
        "sources": {
            **src,
            "note": "PRIMARY metric (exit criterion 3 is Source-level). "
                    "Source nodes with no sourceKind resolve tier-neutral.",
        },
        "points_legacy": {
            **pts,
            "note": "Legacy Point-level sourceKind (audit.py's historical "
                    "check) — tiering the SOURCE node is the modern path "
                    "(#398); Point-level annotations are legacy.",
        },
    }


def _source_tier_neutral(proj) -> dict:
    """Resolve each Source's effective tier (read-only, in-process).

    Mirrors source_credibility.resolve_tier precedence: explicit
    credibilityTier > sourceKind tier-form > registry default > None.
    Registry state = the module's SOURCE_KIND_DEFAULTS (the loaded default —
    registry persistence is a Phase-4 prerequisite, not yet committed).

    The tier vocabulary is computed in-process and pushed as query params so
    the NEUTRAL population is an exact Cypher COUNT + bounded samples — the
    Source population is never fully materialized into Python (conf-75).
    """
    from tortoise.source_credibility import SOURCE_KIND_DEFAULTS, _TIER_FORM
    # sourceKind values that resolve NON-neutral: the tier-form strings
    # themselves (T0-T4, resolve_tier's first check) + registered kinds whose
    # registry default is a tier. Everything else (unknown kinds, explicitly
    # None-registered legacy kinds) resolves neutral.
    tier_kinds = sorted(set(_TIER_FORM) | {
        k for k, v in SOURCE_KIND_DEFAULTS.items() if v in _TIER_FORM})

    crows = _q(proj, "MATCH (s:Source) RETURN count(s)")
    total = int(crows[0][0]) if crows else 0

    neutral_where = ("MATCH (s:Source) WHERE "
                     "(s.credibilityTier IS NULL OR "
                     "NOT s.credibilityTier IN $tv) "
                     "AND (s.sourceKind IS NULL OR NOT s.sourceKind IN $tk) ")
    params = {"tv": sorted(_TIER_FORM), "tk": tier_kinds}
    nrows = _q(proj, neutral_where + "RETURN count(s)", params=params)
    neutral_count = int(nrows[0][0]) if nrows else 0
    samples = _samples(_q(proj, neutral_where +
                          "RETURN s.url, s.sourceKind, s.credibilityTier "
                          "ORDER BY s.url LIMIT " + str(SAMPLE_LIMIT),
                          params=params),
                       ("url", "sourceKind", "credibilityTier"))
    return {
        "count": neutral_count,
        "total_sources": total,
        "pct_neutral": round(neutral_count / total, 4) if total else None,
        "samples": samples,
        "note": "Sources resolving NEUTRAL (no explicit credibilityTier, no "
                "tier-form sourceKind, no registry default). Exit criterion 3: "
                ">=50% of evidence-backed Sources resolved non-neutral. Count "
                "is exact (Cypher count); samples are bounded.",
    }


def _extracted_from(proj) -> dict:
    edge_rows = _q(proj, "MATCH (p:Point)-[:extractedFrom]->(:Source) "
                         "RETURN count(DISTINCT p)")
    prop_rows = _q(proj, "MATCH (p:Point) WHERE p.extractedFrom IS NOT NULL "
                         "RETURN count(p)")
    # Backing = extractedFrom EDGE or legacy extractedFrom PROPERTY
    # (entities.py ~L171-174) — a property-backed Point is NOT 'without
    # backing'. Exclude it so the two metrics stay disjoint (conf-60: it was
    # double-counted in both points_property_backed and
    # evidence_without_source_backing).
    ev_no_src = _count_and_samples(
        proj,
        "MATCH (p:Point {is_operator: false}) "
        "WHERE NOT (p)-[:extractedFrom]->(:Source) "
        "AND p.extractedFrom IS NULL RETURN count(p)",
        "MATCH (p:Point {is_operator: false}) "
        "WHERE NOT (p)-[:extractedFrom]->(:Source) "
        "AND p.extractedFrom IS NULL "
        "RETURN p.id, p.content ORDER BY p.id",
        ("id", "content"),
    )
    return {
        "points_edge_backed": int(edge_rows[0][0]) if edge_rows else 0,
        "points_property_backed": int(prop_rows[0][0]) if prop_rows else 0,
        "evidence_without_source_backing": {
            **ev_no_src,
            "note": "Non-operator Points with NO Source backing — no "
                    "extractedFrom edge AND no legacy extractedFrom property "
                    "(entities.py ~L171-174). The provenanceSource STRING "
                    "class (entities.py ~L143-147) has no Source node — "
                    "backfill must identity-validate before _link_source "
                    "MERGE (scoping P1-7: no stub-Source manufacturing).",
        },
        "note": "extractedFrom backing % — exit criterion 4 baseline. Backing "
                "= edge OR legacy property; points_edge_backed and "
                "points_property_backed are disjoint from "
                "evidence_without_source_backing by construction.",
    }


def _subject_stubs(proj) -> dict:
    # Stub signature from _create_about_edges: MERGE (s:Subject {name}) ON
    # CREATE SET s.id=$name, s.subjectKind='other' — id == name marks the
    # auto-created stub (deliberate create_subject uses a ULID id).
    base = ("MATCH (s:Subject) WHERE s.id = s.name "
            "AND s.subjectKind = 'other' ")
    crows = _q(proj, base + "RETURN count(s)")
    count = int(crows[0][0]) if crows else 0
    edge_rows = _q(proj, "MATCH (:Point)-[r:aboutSubject]->(s:Subject) "
                         "WHERE s.id = s.name AND s.subjectKind = 'other' "
                         "RETURN count(r)")
    return {
        "count": count,
        "about_subject_edges": int(edge_rows[0][0]) if edge_rows else 0,
        "samples": _samples(_q(proj, base + "RETURN s.name ORDER BY s.name "
                                            f"LIMIT {SAMPLE_LIMIT}"),
                            ("name",)),
        "note": "Auto-created Subject stubs (id=name, subjectKind='other'). "
                "Exit criterion 8: class decided in Phase 3 (0 aboutSubject-to-"
                "stub edges or documented acceptance). Count is exact (Cypher "
                "count); samples are bounded.",
    }


def _cap_skip(proj) -> dict:
    # Graph-detectable proxy: operator declared an op_type but has NO typed
    # input edges — every input edge was skipped (per-instance 500-stub cap,
    # edges.py ~L60) or the operator is fully degenerate. The skipped edge
    # itself leaves no trace: source of truth = event log > content-string;
    # neither available here → class UNVERIFIABLE (exit criterion 2 floor).
    base = ("MATCH (o:Point {is_operator: true}) "
            "WHERE o.op_type IS NOT NULL "
            "AND NOT (o)-[:IMPL|NAND]->(:Point) ")
    crows = _q(proj, base + "RETURN count(o)")
    count = int(crows[0][0]) if crows else 0
    return {
        "status": "UNVERIFIABLE_FROM_GRAPH_STATE",
        "typed_edge_less_operators": {
            "count": count,
            "samples": _samples(_q(proj, base + "RETURN o.id, o.op_type "
                                                "ORDER BY o.id "
                                                f"LIMIT {SAMPLE_LIMIT}"),
                                ("id", "op_type")),
        },
        "decision": {
            "owner": "TBD (epistemic-team)",
            "event_log_normalization_default": "graph-only durability unless "
                "the JSONL event log is declared source of truth (Phase 1 "
                "decision, scoping P1-2/exit criterion 2).",
            "blocks_close": True,
        },
        "note": "Cap-skip: at the per-instance 500-stub cap _create_edges "
                "SKIPS the missing source (no edge, no stub) — the skip is "
                "invisible in graph state. Determined from event-log evidence "
                "> operator content-string; neither → UNVERIFIABLE, which "
                "blocks close or requires explicit unremediated acceptance "
                "(scoping exit criterion 2).",
    }


# ── Report assembly ──────────────────────────────────────────────────────

def scan_graph(proj) -> dict:
    """Run the full read-only baseline scan. Returns the JSON-able report."""
    return {
        "tool": "334-wiring-baseline-scan",
        "issue": "334",
        "phase": "0/1 pre-migration (read-only)",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "graph_stats": _graph_stats(proj),
        "status_distribution": _status_distribution(proj),
        "stub_points": _stub_points(proj),
        "stub_edges": _stub_edges(proj),
        "degenerate_operators": _degenerate_operators(proj),
        "null_status_points": _null_status_points(proj),
        "missing_source_kind": _missing_source_kind(proj),
        "source_tier_neutral": _source_tier_neutral(proj),
        "extracted_from": _extracted_from(proj),
        "subject_stubs": _subject_stubs(proj),
        "cap_skip": _cap_skip(proj),
        "known_issues": [
            "tortoise/ep.py:1060 random.shuffle(factors) is UNSEEDED — "
            "Phase 5 EP determinism protocol must pin random.seed (+ "
            "PYTHONHASHSEED if cross-process). NOT fixed in Phase 0/1 "
            "(pre-migration read-only tooling)."
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="#334 Phase-1 baseline scan — read-only wiring-anomaly "
                    "report (JSON: counts + sample IDs; no graph writes)."
    )
    parser.add_argument("--uri", default=None, help="Override TORTOISE_DB_URI "
                        "(docker://, redis://, rediss://).")
    parser.add_argument("--path", default=None, help="Embedded FalkorDBLite path "
                        "(validation-only mode).")
    parser.add_argument("--output", default=None, help="Write JSON report to "
                        "this file (default: stdout).")
    args = parser.parse_args(argv)

    try:
        cfg = classify_target(args.uri, args.path)
    except ValueError as exc:
        print(f"[baseline-scan] ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        proj = connect_projection(cfg)
    except Exception as exc:  # noqa: BLE001 — unreachable must fail loudly
        print(f"[baseline-scan] FAIL: endpoint unreachable: {exc}",
              file=sys.stderr)
        return 1

    report = scan_graph(proj)
    # Never echo the raw URI (it carries the DB password) — conf-75.
    report["target"] = ({**cfg, "uri": redact_uri(cfg["uri"])}
                         if cfg.get("uri") else cfg)
    text = json.dumps(report, indent=2, default=str)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"[baseline-scan] report written to {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
