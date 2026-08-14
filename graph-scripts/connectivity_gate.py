#!/usr/bin/env python3
"""#334 Phase 0 — connectivity gate for the Work Graph Wiring Remediation epic.

Read-only Phase-0 gate: verifies a Tortoise/FalkorDB endpoint is reachable and
reports graph stats (counts by node type). Exits NON-ZERO with a clear message
when the endpoint is unreachable. Never writes to the graph.

Per the scoping doc (docs/scoping/2026-08-13-334-wiring-remediation-scoping.md,
Phase 1 + coherence-review P1-2 "connectivity-mode Phase-0 gate + bolt://
fallback"), the gate classifies the connection mode so downstream Phase 0/1
tooling picks the right restore path:

  - docker:// / redis:// / rediss:// URI  → RDB snapshot path available
    (docker-aware BGSAVE + RDB copy; see graph-scripts/rdb_snapshot_restore.py).
  - embedded file path (FalkorDBLite)     → NO docker RDB. Restore fallback is
    backup.py JSONL replay — which per scoping solution-verify P1-2 is NOT a
    real restore (resurrects stubs, drops SDK-created points). Embedded mode is
    for validating tooling on fresh graphs only (2026-08-15 owner decision:
    legacy/local graphs are irrelevant; the real baseline scan runs on the
    production URI at launch).

Usage:
  # Env-based target (canonical):
  TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise \
    python3 graph-scripts/connectivity_gate.py

  # Explicit URI or embedded path:
  python3 graph-scripts/connectivity_gate.py --uri docker://:pw@localhost:6379/tortoise
  python3 graph-scripts/connectivity_gate.py --path /tmp/fresh-graph.db

Exit codes: 0 = reachable (stats printed), 1 = unreachable/misconfigured,
2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Allow running from any directory (repo-root import).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"
SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")
CONNECT_TIMEOUT_S = 5


# ── Connection resolution ────────────────────────────────────────────────

def classify_target(uri: str | None, path: str | None) -> dict:
    """Classify a connection target into a mode dict.

    Mode is either ``uri`` (docker:///redis:///rediss:// → docker-aware RDB
    snapshot path available) or ``embedded`` (file path → no docker RDB;
    backup.py JSONL fallback only, tooling-validation use).
    """
    if uri is not None:
        scheme = uri.split("://", 1)[0]
        if scheme not in SUPPORTED_URI_SCHEMES:
            raise ValueError(
                f"Unsupported scheme {scheme!r} — expected one of "
                f"{'/'.join(SUPPORTED_URI_SCHEMES)} (or pass --path for "
                "embedded FalkorDBLite)."
            )
        return {
            "mode": "uri",
            "uri": uri,
            "snapshot_path": "docker-aware RDB (BGSAVE + RDB copy) — "
                             "see graph-scripts/rdb_snapshot_restore.py",
        }
    if path is not None:
        return {
            "mode": "embedded",
            "path": path,
            "snapshot_path": "NONE — embedded FalkorDBLite has no docker RDB; "
                             "backup.py JSONL replay is NOT a real restore "
                             "(scoping solution-verify P1-2). Validation-only.",
        }
    # Default: TORTOISE_DB_URI env, else the canonical local URI.
    env_uri = os.environ.get("TORTOISE_DB_URI")
    if env_uri:
        return classify_target(env_uri, None)
    return classify_target(DEFAULT_URI, None)


def redact_uri(uri: str) -> str:
    """Return the URI with the password portion redacted (safe to print/store).

    docker://:pw@host:6379/db  →  docker://:****@host:6379/db
    redis://user:pw@host/db    →  redis://user:****@host/db
    URIs without credentials are returned unchanged (conf-75: never leak the
    DB password into stdout or sidecar files).
    """
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(uri)
    if not parts.username and not parts.password:
        return uri
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    user = parts.username or ""
    netloc = f"{user}:****@{host}" if user else f":****@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query,
                       parts.fragment))


def connect_projection(cfg: dict):
    """Build a FalkorProjection for the classified target (no writes).

    Embedded paths must EXIST — a missing path silently spins up a fresh empty
    FalkorDBLite database, and an all-zero "clean" scan/baseline would be
    misleading (conf-65). Fail loudly instead.
    """
    from tortoise.projection import FalkorProjection
    if cfg["mode"] == "uri":
        return FalkorProjection.from_uri(cfg["uri"])
    path = cfg["path"]
    if path != ":memory:" and not os.path.isfile(path):
        raise FileNotFoundError(
            f"embedded graph path does not exist: {path!r} — refusing to "
            "silently create a fresh empty FalkorDBLite database (an all-zero "
            "'clean' scan/baseline would be misleading). Point at an existing "
            "graph, or use a docker:// URI."
        )
    return FalkorProjection(path)


# ── Graph stats (read-only) ──────────────────────────────────────────────

def graph_stats(proj) -> dict:
    """Count nodes/edges and nodes by label. Pure reads.

    Returns {"nodes": int, "edges": int, "by_label": {label: count}, ...}.
    """
    nodes = proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = proj.g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    by_label: dict[str, int] = {}
    try:
        rows = proj.g.query(
            "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS c "
            "ORDER BY c DESC"
        ).result_set
        by_label = {str(r[0]): int(r[1]) for r in rows}
    except Exception as exc:  # noqa: BLE001 — label query is best-effort
        by_label = {"__error__": str(exc)}
    return {
        "nodes": int(nodes or 0),
        "edges": int(edges or 0),
        "by_label": by_label,
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="#334 Phase-0 connectivity gate — reachability + graph stats "
                    "(read-only, exits non-zero when unreachable)."
    )
    parser.add_argument("--uri", default=None, help="Override TORTOISE_DB_URI "
                        "(docker://, redis://, rediss://).")
    parser.add_argument("--path", default=None, help="Embedded FalkorDBLite path "
                        "(no docker RDB — validation-only mode).")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead "
                        "of human-readable output.")
    args = parser.parse_args(argv)

    try:
        cfg = classify_target(args.uri, args.path)
    except ValueError as exc:
        print(f"[connectivity-gate] ERROR: {exc}", file=sys.stderr)
        return 2

    # Never echo the raw URI (it carries the DB password) — conf-75.
    target = ({**cfg, "uri": redact_uri(cfg["uri"])}
              if cfg.get("uri") else cfg)

    if args.json:
        out = {
            "tool": "334-connectivity-gate",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "target": target,
            "ok": None,
            "stats": None,
        }
    else:
        print("=" * 60)
        print("#334 PHASE-0 CONNECTIVITY GATE (read-only)")
        print("=" * 60)
        print(f"  Target: {target.get('uri') or target.get('path')}")
        print(f"  Mode:   {cfg['mode']}")
        print(f"  Restore path: {cfg['snapshot_path']}")
        print()

    try:
        proj = connect_projection(cfg)
        stats = graph_stats(proj)
    except Exception as exc:  # noqa: BLE001 — unreachable must fail the gate
        if args.json:
            out["ok"] = False
            out["error"] = str(exc)
            print(json.dumps(out, indent=2, default=str))
        else:
            print("[connectivity-gate] FAIL: endpoint unreachable.", file=sys.stderr)
            print(f"  Target: {target.get('uri') or target.get('path')}", file=sys.stderr)
            print(f"  Reason: {exc}", file=sys.stderr)
            print("  Check the endpoint is up and TORTOISE_DB_URI is correct.", file=sys.stderr)
        return 1

    if args.json:
        out["ok"] = True
        out["stats"] = stats
        print(json.dumps(out, indent=2, default=str))
    else:
        print("[connectivity-gate] OK — endpoint reachable.")
        print(f"  Nodes: {stats['nodes']}  Edges: {stats['edges']}")
        print("  Nodes by type:")
        for label, count in stats["by_label"].items():
            print(f"    {label:<24} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
