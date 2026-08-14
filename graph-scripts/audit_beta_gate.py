#!/usr/bin/env python3
"""Pre-beta graph integrity gate (#1200) — repeatable structure + audit check.

Runs the same surface the hosted MCP tools expose (`tortoise_check_structure`,
`tortoise_summarize_structure`) plus the tortoise/audit.py audit and the
beta-gate risk queries (orphan NANDs, batch-connected mitigations,
missing_sourceKind per #1158) against ANY Tortoise graph the environment
points at:

  - Hosted/selfhost FalkorDB:  TORTOISE_DB_URI (docker:// | redis:// | rediss://)
  - Embedded FalkorDBLite:     TORTOISE_DB_PATH (default ~/.tortoise/tortoise.db)

Usage:
    # local embedded graph (default)
    python3 graph-scripts/audit_beta_gate.py

    # snapshot copy (avoid single-writer contention, #942)
    TORTOISE_DB_PATH=/tmp/snapshot.db python3 graph-scripts/audit_beta_gate.py

    # hosted team graph via FalkorDB connection string
    TORTOISE_DB_URI='rediss://...' python3 graph-scripts/audit_beta_gate.py --namespace team_<id>

    # machine-readable output (for CI / cohort gate)
    python3 graph-scripts/audit_beta_gate.py --json

Exit codes: 0 = clean gate (no P1), 1 = P1 findings (gate FAIL), 2 = error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def _q(proj, cypher: str, **params) -> list:
    return proj.g.query(cypher, params).result_set


def _redact_uri(uri: str) -> str:
    """Strip credentials from a connection URI before display (#1200 review)."""
    from urllib.parse import urlunsplit, urlsplit
    parts = urlsplit(uri)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def run_check(sdk, proj) -> dict:
    """Run the full integrity surface. Returns structured findings."""
    out: dict = {}

    # ── baseline ──────────────────────────────────────────────────────
    rows = _q(proj, "MATCH (n:Point) RETURN count(n)")
    out["node_count"] = rows[0][0] if rows else 0
    rows = _q(proj, "MATCH (n:Point {is_operator:true}) RETURN count(n)")
    out["operator_count"] = rows[0][0] if rows else 0
    rows = _q(proj, "MATCH ()-[e]->() RETURN count(e)")
    out["edge_count"] = rows[0][0] if rows else 0
    out["point_kinds"] = [
        {"kind": r[0], "count": r[1]}
        for r in _q(
            proj,
            "MATCH (n:Point) WHERE n.is_operator = false AND n.pointKind IS NOT NULL "
            "RETURN n.pointKind, count(n) ORDER BY count(n) DESC",
        )
    ]

    # ── chain structure (tortoise_check_structure surface) ────────────
    out["check_structure"] = sdk.check_structure()
    out["summarize_structure"] = sdk.summarize_structure()

    # ── tortoise/audit.py ─────────────────────────────────────────────
    from tortoise.audit import audit_graph
    result = audit_graph(proj)
    out["audit"] = {
        "high": result.high_count(),
        "medium": result.medium_count(),
        "low": result.low_count(),
        "issues": [
            {"type": i.issue_type, "severity": i.severity, "node_id": i.node_id,
             "detail": i.detail}
            for i in result.issues
        ],
    }

    # ── beta-gate risk surfaces (#1200) ───────────────────────────────
    # 5a. Orphan NAND operators — NAND op points with ZERO edges (created,
    #     never wired). EP can never apply them; they silently rot.
    out["orphan_nands"] = [
        {"id": r[0], "content": (r[1] or "")[:120]}
        for r in _q(
            proj,
            "MATCH (n:Point {is_operator:true, op_type:'NAND'}) "
            "WHERE NOT (n)--() RETURN n.id, n.content LIMIT 100",
        )
    ]

    # 5b. Batch-connected mitigations — ONE mitigation point targeting MANY
    #     operators. Skill (how-to-use-tortoise): "Batch-connecting
    #     mitigations → EP weights cascade-nuked." Connect one at a time.
    #     Matches BOTH edge shapes: legacy `:mitigates` (m→op) and the
    #     current `:mitigated_by` (op→m, sdk.mitigate_operator). Targets are
    #     deduped ACROSS shapes (UNWIND both lists → collect(DISTINCT)) so a
    #     double-wired operator (both labels) counts once, not twice (#1200
    #     review: false batch-mitigation P1).
    out["batch_mitigations"] = [
        {"id": r[0], "content": (r[1] or "")[:120], "targets": r[2]}
        for r in _q(
            proj,
            "MATCH (m:Point) "
            "OPTIONAL MATCH (m)-[:mitigates]->(t1:Point) "
            "OPTIONAL MATCH (t2:Point)-[:mitigated_by]->(m) "
            "WITH m, collect(DISTINCT t1) AS legacy_tgts, collect(DISTINCT t2) AS cur_tgts "
            "UNWIND legacy_tgts + cur_tgts AS t "
            "WITH m, collect(DISTINCT t) AS tgts "
            "WHERE size(tgts) > 1 "
            "RETURN m.id, m.content, size(tgts) ORDER BY size(tgts) DESC LIMIT 100",
        )
    ]

    # 5c. Unmitigated low-confidence operators — influence unchecked.
    #     Mitigation attaches to the OPERATOR, not the edge target:
    #     legacy (m)-[:mitigates]->(op) and current (op)-[:mitigated_by]->(m)
    #     (sdk.mitigate_operator). Check op for both shapes (#1200 review:
    #     previously checked tgt, so every mitigated operator looked bare).
    out["unmitigated_low_conf"] = [
        {"id": r[0], "confidence": r[1], "target": (r[2] or "")[:120]}
        for r in _q(
            proj,
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(tgt:Point) "
            "WHERE op.confidence <= 0.35 "
            "OPTIONAL MATCH (op)<-[mit_legacy:mitigates]-(:Point) "
            "OPTIONAL MATCH (op)-[mit_cur:mitigated_by]->(:Point) "
            "WITH op, tgt, mit_legacy, mit_cur "
            "WHERE mit_legacy IS NULL AND mit_cur IS NULL "
            "RETURN DISTINCT op.id, op.confidence, tgt.content LIMIT 100",
        )
    ]

    # 5d. Evidence without sourceKind (#1158 audit.py gap).
    out["missing_sourcekind"] = [
        {"id": r[0], "content": (r[1] or "")[:120]}
        for r in _q(
            proj,
            "MATCH (op:Point {is_operator:true})-[:IMPL|NAND]->(ev:Point) "
            "WHERE ev.sourceKind IS NULL AND ev.is_operator = false "
            "RETURN DISTINCT ev.id, ev.content LIMIT 100",
        )
    ]

    # 5e. NAND edges targeting retracted/superseded points (dangling attacks).
    #     'deleted' is NOT a point status — delete_point tombstones to
    #     'retracted' (POINT_STATUS_VALUES: draft/live/retracted/superseded/
    #     outdated/archived).
    out["nand_to_dead"] = [
        {"from": r[0], "to": r[1], "status": r[2]}
        for r in _q(
            proj,
            "MATCH (n:Point {is_operator:true, op_type:'NAND'})-[:NAND]->(t:Point) "
            "WHERE t.status IN ['retracted', 'superseded'] "
            "RETURN n.id, t.id, t.status LIMIT 100",
        )
    ]

    # ── gate verdict ──────────────────────────────────────────────────
    p1 = out["check_structure"]  # chain violations are P1-relevant
    # impl_instead_of_nand is keyword-based (content "not"/"no "/"fail"…) and
    # false-positives on ingested doc text — advisory, NOT a cohort-blocking
    # P1 (#1200 review). Everything else at severity=high stays hard-FAIL.
    ADVISORY_AUDIT_TYPES = {"impl_instead_of_nand"}
    advisory_audit = [i for i in result.issues if i.issue_type in ADVISORY_AUDIT_TYPES]
    p1_audit = [
        i for i in result.issues
        if i.severity == "high" and i.issue_type not in ADVISORY_AUDIT_TYPES
    ]
    p1_risk = out["orphan_nands"] or out["batch_mitigations"] or out["nand_to_dead"]
    out["gate"] = {
        "PASS": not (p1 or p1_audit or p1_risk),
        "chain_violations": len(p1),
        "audit_high": len(p1_audit),
        "audit_advisory": len(advisory_audit),
        "risk_surface_hits": {
            "orphan_nands": len(out["orphan_nands"]),
            "batch_mitigations": len(out["batch_mitigations"]),
            "nand_to_dead": len(out["nand_to_dead"]),
        },
    }
    return out


def _hr(title: str) -> None:
    print(f"\n{'=' * 68}\n{title}\n{'=' * 68}")


def print_report(out: dict, db_target: str) -> None:
    _hr("Pre-beta graph integrity gate (#1200)")
    print(f"DB target: {db_target}")
    print(
        f"Points: {out['node_count']} ({out['operator_count']} operators), "
        f"Edges: {out['edge_count']}"
    )
    if out["node_count"] == 0:
        print(
            "⚠️  GRAPH IS EMPTY — verify TORTOISE_DB_URI / TORTOISE_DB_PATH points "
            "at the intended graph before trusting a PASS."
        )
    if out["point_kinds"]:
        print("PointKinds:", ", ".join(f"{k['kind']}={k['count']}" for k in out["point_kinds"][:12]))

    _hr("tortoise_summarize_structure")
    print(json.dumps(out["summarize_structure"], indent=2))

    _hr(f"tortoise_check_structure — {len(out['check_structure'])} violation(s)")
    for v in out["check_structure"]:
        print(f"  [{v.get('type')}] {v.get('id')} — {v.get('message')}")

    _hr("tortoise/audit.py audit_graph")
    a = out["audit"]
    print(f"Issues: {a['high']} high, {a['medium']} medium, {a['low']} low")
    by_type: dict = {}
    for i in a["issues"]:
        by_type.setdefault(i["type"], []).append(i)
    for itype, items in by_type.items():
        print(f"  [{itype}] {len(items)}")

    _hr("Beta-gate risk surfaces")
    print(f"5a orphan NAND operators:            {len(out['orphan_nands'])}")
    print(f"5b batch-connected mitigations:      {len(out['batch_mitigations'])}")
    print(f"5c unmitigated low-confidence ops:   {len(out['unmitigated_low_conf'])}")
    print(f"5d evidence missing sourceKind #1158: {len(out['missing_sourcekind'])}")
    print(f"5e NAND → retracted/deleted:         {len(out['nand_to_dead'])}")

    g = out["gate"]
    verdict = "PASS" if g["PASS"] else "FAIL"
    print(
        f"\nGATE: {verdict} — chain={g['chain_violations']}, "
        f"audit_high={g['audit_high']}, audit_advisory={g['audit_advisory']}, "
        f"risk_surfaces={g['risk_surface_hits']}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--namespace", default=None,
                    help="Team namespace (e.g. team_abc123) for hosted/selfhost graphs")
    ap.add_argument("--json", action="store_true", help="Emit JSON report")
    args = ap.parse_args()

    from tortoise.sdk import TortoiseSDK
    sdk: TortoiseSDK | None = None
    try:
        sdk = TortoiseSDK(namespace=args.namespace)
        # Fail-loud on a missing embedded DB: redislite would otherwise CREATE
        # an empty graph at a typo'd path and the gate would PASS vacuously
        # (#1200 — a pre-beta gate must never silently validate the wrong target).
        if sdk._db_path and not os.path.exists(sdk._db_path):
            print(
                f"ERROR: embedded DB path does not exist: {sdk._db_path} "
                "(refusing to create an empty graph — check TORTOISE_DB_PATH "
                "or set TORTOISE_DB_URI for hosted/selfhost FalkorDB)",
                file=sys.stderr,
            )
            return 2
        proj = sdk._get_proj()
        db_target = sdk._db_path or sdk._db_uri or "<unknown>"
        out = run_check(sdk, proj)
    except Exception as e:  # noqa: BLE001 — gate must distinguish error from P1 fail
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    finally:
        if sdk is not None:
            try:
                sdk.close()
            except Exception:
                pass

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print_report(out, _redact_uri(db_target))

    return 0 if out["gate"]["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
