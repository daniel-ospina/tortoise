#!/usr/bin/env python3
"""Graph-scale diagnostics for the dreaming-EP decision gate (issue #1239).

Epic 903-C1, DE2E-10 (``docs/epics/2026-08-13-903-dreaming-ep/04-plan.md``
Substep 7): measure the real graph scale BEFORE building the scheduler
machinery. This script emits:

- node/edge counts (Points split by ``is_operator``; IMPL/NAND edge counts)
- operator fan-out distribution (edges per operator → histogram)
- region/neighborhood sizes (BFS-neighborhood sizes from a sample of claim
  anchors, reusing ``tortoise.analyze._bfs_select_operators``)
- connected-component stats (component count + sizes, via a small BFS over
  operator-mediated edges — FalkorDB has no native connected-components)

then asserts the DE2E-10 measurable invariants (counts > 0, fan-out sums to
the edge count, component stats emitted, neighborhood sample emitted).

Location note (issue #1239 said ``scripts/``): the repo moved repo-local
graph scripts to ``graph-scripts/`` in #129 — ``scripts/`` is now a symlink
to the shared agent-infra tooling repo, so a Tortoise-specific script lives
here instead.

Source selection (deterministic, script-standalone — zero deps beyond the
venv's tortoise install):

- ``--fixture`` → run against the F5 representative synthetic fixture
  (``tests/epic903_fixtures.f5_diagnostics``, pinned counts/fan-out). This is
  the CI-safe path: real-snapshot runs are optional (external dependency).
- otherwise → the live graph: ``--db-path PATH`` (explicit embedded path) or
  ``TORTOISE_DB_URI`` (docker:///redis://) or the canonical embedded default.
  If the live graph is unreachable OR empty, auto mode falls back to the F5
  fixture with a warning (an explicit ``--db-path`` never falls back — it
  fails loudly).

The stale-first-vs-full decision itself is a HUMAN gate (DE2E-10) — this
script emits the metrics the decision needs plus a mechanical hint from the
recorded rule (``docs/epics/2026-08-13-903-dreaming-ep/06-diagnostics.md``);
it never decides for the human.

Exit code: 0 when all invariants PASS, 1 otherwise. ``--json`` emits a
machine-readable report (parsed by ``tests/test_graph_diagnostics.py``).
"""
from __future__ import annotations

import argparse
import json
import os  # noqa: F401
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

# Script-standalone import support (repo convention): the repo root carries
# the `tortoise` package and the `tests` package (F5 fixture).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tortoise.analyze import _bfs_select_operators  # noqa: E402, I001
from tortoise.exceptions import EmbeddedStoreBusyError  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: Fallback-accepted failures when auto-selecting the live graph (everything
#: else — config errors like the FLY_APP_NAME production guard — re-raises).
#: ``redis.exceptions.ConnectionError`` (NOT the builtin ConnectionError —
#: redis-py subclasses RedisError) = unreachable docker:///redis:// target.
import redis.exceptions as _redis_exc  # noqa: E402

_FALLBACK_EXCEPTIONS: tuple[type[BaseException], ...] = (
    LookupError,                  # graph reachable but empty (0 Points)
    EmbeddedStoreBusyError,       # single-writer store held by a live process
    _redis_exc.ConnectionError,   # unreachable live DB target
)

#: The existing ``_bfs_select_operators`` 200-operator selector cap — I1's
#: default per-pass budget (``dream(budget=None)``). Mirrors the decision rule
#: in 06-diagnostics.md: below this cap a single full pass is bounded by the
#: SAME cap the window passes would use, so windows can never beat whole-graph.
FULL_REFRESH_OPERATOR_THRESHOLD = 200

#: Operator-edge types counted (matches the EP factor surface).
EDGE_TYPES = ("IMPL", "NAND")


# ── Graph queries ───────────────────────────────────────────────────

def _count_claims(g) -> int:
    rows = g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN count(n)"
    ).result_set
    return int(rows[0][0])


def _count_operators(g) -> int:
    rows = g.query(
        "MATCH (n:Point {is_operator:true}) RETURN count(n)"
    ).result_set
    return int(rows[0][0])


def _edge_type_counts(g) -> dict[str, int]:
    rows = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN type(r), count(r)"
    ).result_set
    counts = {t: 0 for t in EDGE_TYPES}
    for rtype, n in rows:
        if rtype in counts:
            counts[rtype] = int(n)
    return counts


def _operator_fan_out(g) -> dict[int, int]:
    """edges-per-operator histogram: {arity: number of operators}."""
    rows = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN o.id, count(r)"
    ).result_set
    hist: Counter[int] = Counter()
    for _oid, arity in rows:
        hist[int(arity)] += 1
    return dict(sorted(hist.items()))


def _all_ids(g) -> tuple[list[tuple[str, str]], list[str]]:
    """(content-sorted claim (id, content) rows, id-sorted operator ids).

    Claims sort by (content, id) — content is STORED DATA (stable per graph
    state) while ULIDs carry random suffixes (same-millisecond builds order
    differently), so an id-sorted sample would pick different anchors across
    isomorphic builds. Content-sorting makes the anchor sample deterministic
    for unique-content graphs (the F5 fixture — contents s1..iso5 — and the
    common case); for graphs with DUPLICATE content the id tiebreak is still
    ULID-random across builds (sizes-only output masks this; acceptable for a
    sampled diagnostic metric)."""
    claim_rows = g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN n.id, n.content"
    ).result_set
    claims = sorted(((r[0], r[1] or "") for r in claim_rows),
                    key=lambda row: (row[1], row[0]))
    op_ids = sorted(
        r[0] for r in g.query(
            "MATCH (n:Point {is_operator:true}) RETURN n.id"
        ).result_set
    )
    return claims, op_ids


def _sample_anchors(claims: list[tuple[str, str]], op_ids: list[str],
                    sample_size: int) -> list[str]:
    """Deterministic anchor sample, claim-dominant (W2: staleness-ranked
    CLAIMS are the window anchors). ``claims`` arrives content-sorted (see
    ``_all_ids``). Operator ids are degenerate BFS anchors (an operator's own
    operator is never "selected" — it IS the anchor), so they only top up the
    sample when claims are exhausted."""
    claim_ids = [cid for cid, _content in claims]
    if claim_ids:
        stride = max(1, len(claim_ids) // max(1, sample_size))
        anchors = claim_ids[::stride][:sample_size]
    else:
        anchors = []
    if len(anchors) < sample_size:
        anchors += op_ids[:sample_size - len(anchors)]
    return anchors


def _neighborhood_sizes(proj, anchors: list[str], max_hops: int) -> list[dict]:
    """BFS-neighborhood size per anchor via ``_bfs_select_operators``.

    ``operators`` = operators selected in the anchor's BFS closure (the
    scheduler-relevant quantity: per-pass operator budget — W2 selects
    operator sets, deduped across windows). Operator-less direct-edge factor
    endpoints (A9) are deliberately NOT counted: the stale-first per-pass
    budget is an operator count."""
    sizes: list[dict] = []
    for anchor in anchors:
        ops, _factor_anchors = _bfs_select_operators(proj, [anchor],
                                                     max_hops=max_hops)
        sizes.append({"anchor": anchor, "operators": len(ops)})
    return sizes


def _component_stats(g, claim_ids: list[str],
                     op_ids: list[str]) -> dict:
    """Connected components over operator-mediated edges (small BFS).

    Nodes = every Point; edges = operator→input (IMPL|NAND) for each
    operator endpoint. A claim touched by no operator is a singleton
    component. Operator-less direct edges are NOT in this component graph
    (consistent with the F5 fixture's ``compute_diagnostics_stats`` — the
    EP factor surface for component grouping is operator-mediated).
    """
    edge_rows = g.query(
        "MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point) "
        "RETURN o.id, c.id"
    ).result_set
    adj: dict[str, set[str]] = defaultdict(set)
    for oid, cid in edge_rows:
        adj[oid].add(cid)
        adj[cid].add(oid)
    all_points = set(op_ids) | set(claim_ids)
    for pid in all_points:
        adj.setdefault(pid, set())

    seen: set[str] = set()
    sizes: list[int] = []
    for start in sorted(all_points):
        if start in seen:
            continue
        frontier = deque([start])
        seen.add(start)
        size = 0
        while frontier:
            node = frontier.popleft()
            size += 1
            for nb in adj[node]:
                if nb not in seen:
                    seen.add(nb)
                    frontier.append(nb)
        sizes.append(size)
    return {
        "n_components": len(sizes),
        "component_sizes": sorted(sizes, reverse=True),
    }


# ── Stats + invariants ─────────────────────────────────────────────

def collect_stats(proj, sample_size: int = 10,
                  max_hops: int = 1) -> dict:
    """Emit all DE2E-10 metrics from a connected projection."""
    g = proj.g
    claim_ids, op_ids = _all_ids(g)
    edge_counts = _edge_type_counts(g)
    n_edges = sum(edge_counts.values())

    anchors = _sample_anchors(claim_ids, op_ids, sample_size)
    neighborhoods = _neighborhood_sizes(proj, anchors, max_hops)
    components = _component_stats(g, [cid for cid, _ in claim_ids], op_ids)

    nbr_ops = [n["operators"] for n in neighborhoods]
    stats = {
        "n_claims": _count_claims(g),
        "n_operators": _count_operators(g),
        "n_edges_impl": edge_counts["IMPL"],
        "n_edges_nand": edge_counts["NAND"],
        "n_edges": n_edges,
        "fan_out": _operator_fan_out(g),
        "neighborhoods": {
            "sample_size": len(neighborhoods),
            "max_hops": max_hops,
            "sizes": sorted(nbr_ops, reverse=True),
            "mean_operators": round(sum(nbr_ops) / len(nbr_ops), 2) if nbr_ops else 0,
            "max_operators": max(nbr_ops) if nbr_ops else 0,
            "min_operators": min(nbr_ops) if nbr_ops else 0,
        },
        "n_components": components["n_components"],
        "component_sizes": components["component_sizes"],
    }
    return stats


def assert_invariants(stats: dict) -> list[dict]:
    """DE2E-10 measurable invariants. Returns [{name, pass, detail}]."""
    fan_sum = sum(arity * count for arity, count in stats["fan_out"].items())
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "pass": bool(ok), "detail": detail})

    check("n_claims > 0", stats["n_claims"] > 0,
          f"n_claims={stats['n_claims']}")
    check("n_operators > 0", stats["n_operators"] > 0,
          f"n_operators={stats['n_operators']}")
    check("n_edges > 0", stats["n_edges"] > 0,
          f"n_edges={stats['n_edges']}")
    check("fan-out sums to edge count", fan_sum == stats["n_edges"],
          f"sum(arity×count)={fan_sum} vs n_edges={stats['n_edges']}")
    check("IMPL+NAND edges == total",
          stats["n_edges_impl"] + stats["n_edges_nand"] == stats["n_edges"],
          f"IMPL={stats['n_edges_impl']} + NAND={stats['n_edges_nand']}")
    check("components >= 1", stats["n_components"] >= 1,
          f"n_components={stats['n_components']}")
    check("component sizes sum to all points",
          sum(stats["component_sizes"]) ==
          stats["n_claims"] + stats["n_operators"],
          f"sum(component_sizes)={sum(stats['component_sizes'])} vs "
          f"points={stats['n_claims'] + stats['n_operators']}")
    check("neighborhood sample emitted", stats["neighborhoods"]["sample_size"] > 0,
          f"sample_size={stats['neighborhoods']['sample_size']}")
    return checks


def decision_hint(stats: dict) -> dict:
    """Mechanical hint from the recorded rule (06-diagnostics.md §Decision
    Rule). The HUMAN records the decision; this only surfaces which way the
    metrics point."""
    if stats["n_operators"] < FULL_REFRESH_OPERATOR_THRESHOLD:
        verdict = "full"
        basis = (f"total operators {stats['n_operators']} < "
                 f"{FULL_REFRESH_OPERATOR_THRESHOLD}: a full pass is bounded "
                 "by the same per-pass cap window passes would use, so "
                 "windows can never be smaller than the whole graph")
    elif (stats["neighborhoods"]["mean_operators"] >=
          stats["n_operators"] * 0.5):
        verdict = "full"
        basis = (f"neighborhood mean {stats['neighborhoods']['mean_operators']} "
                 f"operators ≈ whole graph ({stats['n_operators']} operators): "
                 "windows ≈ full pass")
    else:
        verdict = "stale-first"
        basis = (f"graph above the {FULL_REFRESH_OPERATOR_THRESHOLD}-operator "
                 "threshold with localized neighborhoods: bounded per-pass "
                 "windows pay off")
    return {"verdict": verdict, "basis": basis}


# ── Report formatting ──────────────────────────────────────────────

def format_report(stats: dict, source: str, checks: list[dict],
                  hint: dict) -> str:
    lines: list[str] = []
    lines.append("Tortoise graph-scale diagnostics — issue #1239 (epic 903-C1, DE2E-10)")
    lines.append(f"Source: {source}")
    lines.append("")
    lines.append("Node/edge counts")
    lines.append(f"  claims (non-operator Points): {stats['n_claims']}")
    lines.append(f"  operators:                    {stats['n_operators']}")
    lines.append(f"  IMPL edges:                   {stats['n_edges_impl']}")
    lines.append(f"  NAND edges:                   {stats['n_edges_nand']}")
    lines.append(f"  total edges:                  {stats['n_edges']}")
    lines.append("")
    lines.append("Operator fan-out distribution (edges per operator → # operators)")
    for arity, count in stats["fan_out"].items():
        lines.append(f"  arity {arity} → {count}")
    lines.append("")
    nbr = stats["neighborhoods"]
    lines.append(f"Region/neighborhood sizes (BFS max_hops={nbr['max_hops']}, "
                 f"sample of {nbr['sample_size']} anchors)")
    lines.append(f"  operators per neighborhood: {nbr['sizes']}")
    lines.append(f"  mean {nbr['mean_operators']} · min {nbr['min_operators']} · "
                 f"max {nbr['max_operators']}")
    lines.append("")
    lines.append("Connected components (over operator-mediated edges)")
    lines.append(f"  components: {stats['n_components']}")
    lines.append(f"  sizes: {stats['component_sizes']}")
    lines.append("")
    lines.append("Invariants (DE2E-10 measurable)")
    for c in checks:
        mark = "PASS" if c["pass"] else "FAIL"
        lines.append(f"  [{mark}] {c['name']}"
                     + (f"  ({c['detail']})" if c["detail"] else ""))
    n_pass = sum(1 for c in checks if c["pass"])
    lines.append(f"  {n_pass}/{len(checks)} invariants PASS")
    lines.append("")
    lines.append(f"Decision hint (recorded rule; the decision is a HUMAN gate): "
                 f"{hint['verdict']} — {hint['basis']}")
    lines.append("Record the decision in "
                 "docs/epics/2026-08-13-903-dreaming-ep/06-diagnostics.md")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────

def _build_fixture():
    """F5 — representative synthetic diagnostics graph (pinned counts)."""
    from tests.epic903_fixtures import f5_diagnostics
    fx = f5_diagnostics()
    return fx.sdk, (f"F5 representative synthetic fixture (pinned: "
                    f"{fx.stats['n_claims']} claims / {fx.stats['n_operators']} "
                    f"operators / {fx.stats['n_edges']} edges)")


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer")  # noqa: B904
    if n <= 0:
        raise argparse.ArgumentTypeError(f"{value!r} must be >= 1")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Graph-scale diagnostics for the epic 903 decision gate.")
    parser.add_argument("--fixture", action="store_true",
                        help="run against the F5 representative synthetic "
                             "fixture (deterministic, CI-safe)")
    parser.add_argument("--db-path", default=None,
                        help="explicit embedded DB path (never falls back)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON report on stdout")
    parser.add_argument("--sample-size", type=_positive_int, default=10,
                        help="neighborhood anchor sample size (default 10)")
    parser.add_argument("--max-hops", type=_positive_int, default=1,
                        help="BFS max_hops for neighborhoods (default 1)")
    args = parser.parse_args(argv)

    sdk: TortoiseSDK | None = None
    source = ""
    if args.fixture:
        sdk, source = _build_fixture()
    else:
        try:
            sdk = TortoiseSDK(args.db_path)
            proj = sdk._get_proj()
            if _count_claims(proj.g) + _count_operators(proj.g) == 0:
                raise LookupError("graph is empty (0 Points)")
            source = f"live graph ({sdk._db_uri or sdk._db_path})"
        except Exception as exc:  # noqa: BLE001, RUF100
            if sdk is not None:
                try:  # noqa: SIM105
                    sdk.close()
                except Exception:  # noqa: BLE001, RUF100
                    pass
                sdk = None
            if args.db_path:
                print(f"[graph-diagnostics] error on explicit target "
                      f"{args.db_path!r}: {exc}", file=sys.stderr)
                return 2
            if not isinstance(exc, _FALLBACK_EXCEPTIONS):
                raise
            print(f"[graph-diagnostics] live graph unavailable ({exc}); "
                  "falling back to the F5 fixture.", file=sys.stderr)
            sdk, source = _build_fixture()

    try:
        stats = collect_stats(sdk._get_proj(), args.sample_size, args.max_hops)
        checks = assert_invariants(stats)
        hint = decision_hint(stats)
        report = format_report(stats, source, checks, hint)
        if args.json:
            print(json.dumps({
                "source": source,
                "stats": stats,
                "invariants": {"checks": checks, "all_pass": all(
                    c["pass"] for c in checks)},
                "decision_hint": hint,
            }, indent=2, sort_keys=True))
        else:
            print(report)
    finally:
        if sdk is not None:
            try:  # noqa: SIM105
                sdk.close()
            except Exception:  # noqa: BLE001, RUF100
                pass

    return 0 if all(c["pass"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
