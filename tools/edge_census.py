#!/usr/bin/env python3
"""Read-only edge census + isolated per-edge RAM probe (#4503).

**Why this exists.** Tortoise's accounting surfaces are all measured in
**nodes**: the cap counts ``Point``/``Object``/``Subject`` nodes
(``tortoise/quota.py``), the meter counts ``write_ops`` + ``nodes_written``
(``tortoise/metering.py``), and the declared cost basis is
``product/pricing.json`` → ``billing.cost_basis.bytes_per_node``. **No
accounting surface has a relationship term at all.** On top of that, EP
persists its belief messages *on the relationship* (four float properties per
edge — ``r.msg_alpha``, ``r.msg_beta``, ``r.back_msg_alpha``,
``r.back_msg_beta``), so the state that grows with relationship **density**
rather than node **volume** is uncapped, unmetered and unpriced — and, unlike
the node-resident EP state fixed by #2884, it is never journaled (#5380).

This tool measures that. It is **read-only**: it never writes to a graph it
was pointed at. The only writes it performs are inside a **disposable
isolated container it starts and removes itself**, for the RAM probe.

Two subcommands
---------------
``census``
    Counts relationships on a graph you point it at: total, per type, and per
    EP message slot, plus the "carries at least one slot" / "carries all four"
    split. Also reports the three **node** denominators the accounting
    surfaces disagree about (resident, ``:Point``, and — only when ``--org``
    is given — the cap's own ``points`` count), so the ratio is always printed
    with the predicate that produced it.

``probe``
    Measures the marginal RAM of an edge by shape, in a **disposable isolated
    FalkorDB container**, using staged ``INFO memory`` → ``used_memory``
    deltas. This is the same methodology as the node probe in
    ``docs/research/2026-09-24-4333-node-volume-storage-cost/research-brief.md``
    §3.4, which is what makes the two sets of figures comparable — the
    cross-check is load-bearing evidence, not a convenience.

Both subcommands print their **raw** readings alongside the derived figures,
and every marginal carries the ``N`` it was divided by: a per-element number
without its N is not reproducible (measurements at N=20,000 and N=5,000 gave
101.8 B and 174.6 B for the same bare-node shape — allocation granularity, not
a contradiction).

Usage::

    python3 tools/edge_census.py census --uri docker://:falkordb@localhost:6379/mygraph
    python3 tools/edge_census.py census --uri ... --org org_abc123
    python3 tools/edge_census.py probe --n 5000
    python3 tools/edge_census.py probe --json > receipt.json

Needs a running docker for ``probe`` only. ``--json`` is available on both.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from tortoise.quota import QuotaCheckError

#: The four EP message properties, and the ONE declaration of "the class this
#: finding is about". The census, the tests and the report all consume this
#: tuple; duplicating it is how the census and the guards would silently drift
#: apart. Written by ``tortoise/ep.py`` at ``:241``/``:256`` (batched flush) and
#: ``:333``/``:366`` (direct path).
EP_EDGE_SLOTS: tuple[str, ...] = (
    "msg_alpha",
    "msg_beta",
    "back_msg_alpha",
    "back_msg_beta",
)

#: The three node denominators the accounting surfaces disagree about. Kept as
#: names, not numbers: this tool must never invent a denominator.
NODE_DENOMINATORS: tuple[str, ...] = ("resident", "point_label", "capped_points")


class CensusError(RuntimeError):
    """A count could not be read. Fail loud — never report a silent 0."""


# ── graph access (duck-typed so tests can inject a fake) ────────────────────

def _scalar(graph: Any, cypher: str) -> int:
    """Run a single-value Cypher query and return the int.

    ``graph`` is anything exposing ``.query(cypher) -> obj.result_set`` — the
    FalkorDB client and ``proj.g`` both do. An empty or non-integer result is
    an error, not a 0: a count this tool cannot read must never be printed as
    a count of zero.
    """
    try:
        result = graph.query(cypher)
    except Exception as exc:
        raise CensusError(f"query failed: {cypher!r}: {exc}") from exc
    rows = getattr(result, "result_set", None)
    if not rows or not rows[0]:
        raise CensusError(f"query returned no rows: {cypher!r}")
    value = rows[0][0]
    if isinstance(value, bool) or not isinstance(value, int):
        raise CensusError(
            f"query did not return an integer: {cypher!r} -> {value!r}")
    return value


def _relationship_types(graph: Any) -> list[str]:
    """Discovered relationship types, via ``db.relationshipTypes()``.

    Discovered rather than hardcoded so a new relationship type is counted
    without a code change — and so the census cannot undercount by omission.
    """
    try:
        result = graph.query("CALL db.relationshipTypes()")
    except Exception as exc:
        raise CensusError(f"db.relationshipTypes() failed: {exc}") from exc
    rows = getattr(result, "result_set", None) or []
    types = []
    for row in rows:
        if row and isinstance(row[0], str) and row[0]:
            types.append(row[0])
    return sorted(types)


# ── the census ─────────────────────────────────────────────────────────────

def relationship_census(graph: Any) -> dict[str, Any]:
    """Count relationships on ``graph``: total, per type, per EP slot.

    Returns a dict with:

    - ``total`` — every relationship, of every type.
    - ``by_type`` — ``{type: count}`` for every discovered type.
    - ``by_slot`` — ``{slot: count}`` for each of :data:`EP_EDGE_SLOTS` —
      edges carrying that property.
    - ``ep_bearing`` — edges carrying **at least one** slot.
    - ``all_four_slots`` — edges carrying **all four**.

    Both ``ep_bearing`` and ``all_four_slots`` are reported because a
    half-written edge is a different thing from a fully-messaged one, and the
    difference is invisible if only one of the two is printed.
    """
    total = _scalar(graph, "MATCH ()-[r]->() RETURN count(r)")
    by_type = {
        rtype: _scalar(graph, f"MATCH ()-[r:{rtype}]->() RETURN count(r)")
        for rtype in _relationship_types(graph)
    }
    by_slot = {
        slot: _scalar(
            graph,
            f"MATCH ()-[r]->() WHERE r.{slot} IS NOT NULL RETURN count(r)",
        )
        for slot in EP_EDGE_SLOTS
    }
    any_clause = " OR ".join(
        f"r.{slot} IS NOT NULL" for slot in EP_EDGE_SLOTS)
    all_clause = " AND ".join(
        f"r.{slot} IS NOT NULL" for slot in EP_EDGE_SLOTS)
    ep_bearing = _scalar(
        graph,
        f"MATCH ()-[r]->() WHERE {any_clause} RETURN count(r)",
    )
    all_four = _scalar(
        graph,
        f"MATCH ()-[r]->() WHERE {all_clause} RETURN count(r)",
    )
    return {
        "total": total,
        "by_type": by_type,
        "by_slot": by_slot,
        "ep_bearing": ep_bearing,
        "all_four_slots": all_four,
    }


def node_census(graph: Any) -> dict[str, int]:
    """The node denominators this tool can read straight off the graph.

    ``capped_points`` is deliberately **absent** here — it is the cap's own
    predicate and only ``quota.count_org_usage`` may produce it (see
    :func:`accounting_view`). Reading it here would be a second implementation
    of the cap's denominator, which is the drift this finding is about.
    """
    return {
        "resident": _scalar(graph, "MATCH (n) RETURN count(n)"),
        "point_label": _scalar(graph, "MATCH (n:Point) RETURN count(n)"),
    }


def accounting_view(
    *,
    edges: dict[str, Any],
    nodes: dict[str, int],
    capped_points: int | None = None,
) -> dict[str, Any]:
    """Join the edge census to the node denominators.

    ``capped_points`` is the cap's own count (``count_org_usage(org, "points")``)
    and is ``None`` unless the caller actually read it. The ratio that needs it
    raises rather than substituting a neighbouring number: **``null`` is
    honest, ``0`` is a lie**, and silently falling back to ``resident`` would
    reproduce exactly the denominator confusion this tool exists to expose.
    """
    out: dict[str, Any] = {
        "nodes": dict(nodes),
        "edges": edges,
        "ratios": {},
    }
    out["nodes"]["capped_points"] = capped_points
    resident = nodes.get("resident")
    total_edges = edges.get("total")
    if capped_points is not None and resident is not None and capped_points > 0:
        out["ratios"]["resident_over_capped"] = round(
            resident / capped_points, 3)
    if resident:
        out["ratios"]["edges_over_resident"] = round(total_edges / resident, 3)
    return out


def assert_subset_ratio_available(view: dict[str, Any]) -> None:
    """Raise if the cap denominator was requested but never read.

    Callers that *need* the cap denominator call this so a missing ``--org``
    cannot be mistaken for a ratio of 0 or 1.

    Keys on ``capped_points is None`` (the read never happened) rather than on
    the ratio being absent — a cap count of **0** is a legitimately
    unratio-able graph, not a missing read, and reporting that as an error
    would make an empty org look like a tool failure.
    """
    if view.get("nodes", {}).get("capped_points") is None:
        raise CensusError(
            "resident_over_capped needs the cap's own count — pass --org so "
            "count_org_usage(org, 'points') is read, or drop the request "
            "rather than substituting another denominator"
        )


# ── the probe ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Stage:
    """One staged reading from the isolated probe.

    ``per_element`` is how many elements this stage added (``0`` for the
    baseline). The marginal is ``delta / per_element`` and is only meaningful
    with ``per_element`` attached — hence the field, rather than a bare number.
    """

    label: str
    used_memory: int
    per_element: int = 0


def probe_marginals(stages: Sequence[Stage]) -> list[dict[str, Any]]:
    """Derived per-stage deltas and marginals from raw ``used_memory`` reads.

    Pure arithmetic, so the probe's conclusions are testable without docker.

    Raises :class:`CensusError` on a non-monotonic reading or a zero
    ``per_element`` with a non-zero delta — a stage that cannot be divided must
    not be reported as 0 bytes.
    """
    if not stages:
        raise CensusError("no probe stages")
    out: list[dict[str, Any]] = []
    previous: Stage | None = None
    for stage in stages:
        if previous is None:
            out.append({
                "label": stage.label,
                "used_memory": stage.used_memory,
                "delta": None,
                "per_element": stage.per_element,
                "marginal_bytes": None,
            })
            previous = stage
            continue
        delta = stage.used_memory - previous.used_memory
        if delta < 0:
            raise CensusError(
                f"used_memory went DOWN at {stage.label!r} "
                f"({previous.used_memory} -> {stage.used_memory}) — a fresh "
                f"isolated instance must be monotonic; the probe is invalid"
            )
        if stage.per_element <= 0:
            marginal = None
            if delta:
                raise CensusError(
                    f"stage {stage.label!r} added {delta} bytes but declares "
                    f"per_element={stage.per_element} — cannot derive a "
                    f"marginal, and reporting 0 would be a false measurement"
                )
        else:
            marginal = round(delta / stage.per_element, 1)
        out.append({
            "label": stage.label,
            "used_memory": stage.used_memory,
            "delta": delta,
            "per_element": stage.per_element,
            "marginal_bytes": marginal,
        })
        previous = stage
    return out


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check)


def docker_available() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}",
                       check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _container_used_memory(container: str) -> int:
    proc = _docker("exec", container, "redis-cli", "INFO", "memory")
    for line in proc.stdout.replace("\r", "").splitlines():
        if line.startswith("used_memory:"):
            return int(line.split(":", 1)[1])
    raise CensusError(
        f"used_memory unreadable from {container!r} — refusing to report a "
        f"size from a probe that could not be read")


def _container_query(container: str, graph: str, cypher: str) -> None:
    proc = _docker("exec", container, "redis-cli", "GRAPH.QUERY", graph, cypher,
                   check=False)
    if proc.returncode != 0:
        raise CensusError(f"probe query failed in {container!r}: {cypher!r}")


#: The staged shapes. Deliberately the SAME shapes the #4333 §3.4 node probe
#: used for its props-only Point, so the two probes are comparable; the edge
#: attr set is the real one (`tortoise/projection/__init__.py`, the replay
#: attr tuple: direction, confidence, weight, label, batch_id).
_POINT_PROPS = (
    "SET n.content = 'a claim about the storage accounting surface ' + n.id, "
    "n.pointKind = 'statement', n.status = 'live', n.confidence = 0.5, "
    "n.createdAt = '2026-09-25T00:00:00+00:00', n.is_episodic = false"
)
_EDGE_ATTRS = (
    "SET r.direction = 'bidirectional', r.confidence = 0.5, r.weight = 1.0, "
    "r.label = 'IMPL', r.batch_id = '01J8ZQ0V4Q9K5W7T2N3M6P4R8X'"
)
_EDGE_SLOTS = (
    "SET r.msg_alpha = 0.5, r.msg_beta = 0.5, "
    "r.back_msg_alpha = 0.5, r.back_msg_beta = 0.5"
)


def run_probe(
    *,
    n: int = 5000,
    image: str = "falkordb/falkordb:latest",
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """Measure marginal RAM per element shape in a disposable container.

    Starts an isolated ``falkordb`` container, runs the staged shapes, reads
    ``INFO memory`` → ``used_memory`` between stages, and **always** removes
    the container — including on failure. Returns the raw readings *and* the
    derived marginals; the raw readings are part of the result because the
    marginal alone is not reproducible.
    """
    if n < 100:
        raise CensusError(f"n={n} is too small to measure (need >= 100)")
    if not docker_available():
        raise CensusError(
            "docker is not available — the probe needs a disposable FalkorDB "
            "container. The census subcommand does not.")

    graph = "edge_census_probe"
    edges = n - 1
    container = f"edge-census-probe-{os.getpid()}-{int(time.time())}"
    log(f"starting disposable container {container!r} ({image})")
    _docker("run", "-d", "--name", container, image)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if _docker("exec", container, "redis-cli", "PING",
                       check=False).stdout.strip() == "PONG":
                break
            time.sleep(1)
        else:
            raise CensusError(f"{container!r} never became ready (60s)")

        stages: list[Stage] = [
            Stage("baseline (fresh instance)",
                  _container_used_memory(container))]
        _container_query(
            container, graph,
            f"UNWIND range(1,{n}) AS i CREATE (:Point {{id: 'p' + toString(i)}})")
        stages.append(Stage("bare :Point node (label + id only)",
                            _container_used_memory(container), n))
        _container_query(container, graph, f"MATCH (n:Point) {_POINT_PROPS}")
        stages.append(Stage("+ keyword-only Point props",
                            _container_used_memory(container), n))
        _container_query(
            container, graph,
            f"UNWIND range(1,{edges}) AS i "
            f"MATCH (a:Point {{id: 'p' + toString(i)}}), "
            f"      (b:Point {{id: 'p' + toString(i + 1)}}) "
            f"CREATE (a)-[:IMPL]->(b)")
        stages.append(Stage("bare :IMPL edge (no properties)",
                            _container_used_memory(container), edges))
        _container_query(container, graph, f"MATCH ()-[r:IMPL]->() {_EDGE_ATTRS}")
        stages.append(Stage("+ real IMPL attrs "
                            "(direction,confidence,weight,label,batch_id)",
                            _container_used_memory(container), edges))
        _container_query(container, graph, f"MATCH ()-[r:IMPL]->() {_EDGE_SLOTS}")
        stages.append(Stage("+ the four EP message slots",
                            _container_used_memory(container), edges))
    finally:
        log(f"removing container {container!r}")
        _docker("rm", "-f", container, check=False)

    derived = probe_marginals(stages)
    by_label = {d["label"]: d for d in derived}
    baseline = stages[0].used_memory
    bare_node = by_label["bare :Point node (label + id only)"]["marginal_bytes"]
    # Every edge marginal is measured from the stage that PRECEDED the edges
    # (the dressed-node stage) — not from the bare-node stage. Dividing
    # (final - bare_nodes) by the edge count would fold the keyword-prop node
    # cost into the per-edge figure, which is the dressed-vs-bare mistake this
    # census exists to avoid.
    kw_point = by_label["+ keyword-only Point props"]
    kw_point_used = kw_point["used_memory"]
    kw_point_total = round((kw_point_used - baseline) / n, 1)
    bare_edge = by_label["bare :IMPL edge (no properties)"]["marginal_bytes"]
    attrs = by_label["+ real IMPL attrs "
                     "(direction,confidence,weight,label,batch_id)"]
    ep_stage = by_label["+ the four EP message slots"]
    dressed_edge = round((ep_stage["used_memory"] - kw_point_used) / edges, 1)

    # Small-n runs are dominated by fixed per-instance overhead: at n=800 the
    # bare-node marginal measured 731 B against 101.8 B at n=20,000. Flag it in
    # the receipt rather than letting a reader compare across n.
    small_n_note = None
    if n < 5000:
        small_n_note = (
            f"n={n} is below the 5,000 used for the reported figures — "
            f"fixed per-instance overhead dominates, so the NODE marginals "
            f"(and any total that includes them) are inflated and must not be "
            f"compared with a larger-n run. The bare-edge and property deltas "
            f"were stable across n=800/5,000/20,000; the node figures were not."
        )

    return {
        "n_nodes": n,
        "n_edges": edges,
        "image": image,
        "small_n_warning": small_n_note,
        "raw_stages": [
            {"label": s.label, "used_memory": s.used_memory,
             "per_element": s.per_element}
            for s in stages
        ],
        "derived": derived,
        "summary_bytes": {
            "bare_node": bare_node,
            "keyword_only_point_total": kw_point_total,
            "bare_edge": bare_edge,
            "edge_attrs_delta": attrs["marginal_bytes"],
            "edge_ep_slots_delta": ep_stage["marginal_bytes"],
            "ep_bearing_edge_total": dressed_edge,
        },
        "ratios": {
            "ep_bearing_edge_over_keyword_only_point": round(
                dressed_edge / kw_point_total, 3) if kw_point_total else None,
        },
        "caveat": ("every marginal is delta / per_element at THIS n; the "
                   "bare-node figure moved 101.8 B (n=20000) to 174.6 B "
                   "(n=5000) between runs — allocation granularity, so a "
                   "figure without its n is not reproducible"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _open_sdk(uri: str | None, graph_name: str | None, embedded: str | None):
    """Open an SDK handle. Never writes; the SDK is used for URI parsing.

    Returns the SDK (not just its graph) because ``--org`` must count the cap in
    **this** graph — see ``_org_capped_points``.
    """
    from tortoise.sdk import TortoiseSDK  # imported lazily — probe needs no DB

    if uri:
        os.environ["TORTOISE_DB_URI"] = uri
        return TortoiseSDK(graph_name=graph_name)
    if embedded:
        os.environ.pop("TORTOISE_DB_URI", None)
        return TortoiseSDK(embedded, graph_name=graph_name)
    raise CensusError("census needs --uri or --embedded")


def _org_capped_points(org: str, sdk) -> int:
    """The cap's OWN count, via the cap's own function — in the census's graph.

    Never re-implemented here: a second implementation of the cap predicate is
    exactly the drift this finding is about.

    ⛔ ``sdk`` is REQUIRED and forwarded. Calling with ``sdk=None`` makes the
    callee build its OWN SDK from the environment (``quota.py``: ``if sdk is
    None: sdk = _make_sdk(namespace=org_id)``), which reads ``TORTOISE_DB_PATH``
    in embedded mode or the graph ``org_<org>`` in URI mode — **neither is the
    graph this census just read**. That produced a ``capped_points`` of 0 beside
    a ``point_label`` of 2000 from one run; and because
    ``assert_subset_ratio_available`` keys on ``None``, the wrong-database 0 was
    indistinguishable from a legitimate empty-org 0. Found in review.
    """
    from tortoise.quota import count_org_usage
    return count_org_usage(org, "points", sdk=sdk)


def _print_census(view: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(view, indent=2, sort_keys=True))
        return
    edges, nodes, ratios = view["edges"], view["nodes"], view["ratios"]
    print(f"relationships: {edges['total']}")
    for rtype, count in sorted(edges["by_type"].items(),
                               key=lambda kv: -kv[1]):
        print(f"  by type  {rtype:<24} {count}")
    for slot in EP_EDGE_SLOTS:
        print(f"  by slot  {slot:<24} {edges['by_slot'][slot]}")
    print(f"  EP-bearing (>=1 slot)          {edges['ep_bearing']}")
    print(f"  all four slots                 {edges['all_four_slots']}")
    print("nodes:")
    for name in ("resident", "point_label", "capped_points"):
        print(f"  {name:<16} {nodes.get(name)}")
    print("ratios:")
    for name, value in sorted(ratios.items()):
        print(f"  {name:<24} {value}")
    if nodes.get("capped_points") is None:
        print("  (resident_over_capped omitted — pass --org; this tool never "
              "substitutes another denominator)")
    elif nodes["capped_points"] == 0:
        print("  (resident_over_capped omitted — the cap's own count is 0, "
              "so the ratio is undefined; it is NOT reported as 0 or 1)")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edge_census",
        description="Read-only edge census + isolated per-edge RAM probe "
                    "(#4503). No cap, meter, price or graph is changed.")
    sub = parser.add_subparsers(dest="command", required=True)

    census = sub.add_parser("census", help="count relationships and EP slots")
    census.add_argument("--uri", help="FalkorDB URI (docker:// or bolt://)")
    census.add_argument("--graph", help="graph name override")
    census.add_argument("--embedded", help="embedded DB path (no URI)")
    census.add_argument("--org", help="org id — also read the cap's own count")
    census.add_argument("--json", action="store_true", help="emit JSON")

    probe = sub.add_parser(
        "probe", help="marginal RAM per element shape (disposable container)")
    probe.add_argument("--n", type=int, default=5000, help="node count")
    probe.add_argument("--image", default="falkordb/falkordb:latest")
    probe.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def _print_probe(result: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(f"isolated probe: image={result['image']} "
          f"n_nodes={result['n_nodes']} n_edges={result['n_edges']}")
    print("raw stages:")
    for stage in result["raw_stages"]:
        print(f"  {stage['label']:<62} used_memory={stage['used_memory']:>10} "
              f"per_element={stage['per_element']}")
    print("derived:")
    for row in result["derived"]:
        marginal = row["marginal_bytes"]
        marginal = "-" if marginal is None else f"{marginal} B"
        print(f"  {row['label']:<62} delta={row['delta']!s:>9} "
              f"marginal={marginal}")
    print("summary (B, at this n):")
    for name, value in sorted(result["summary_bytes"].items()):
        print(f"  {name:<36} {value}")
    for name, value in sorted(result["ratios"].items()):
        print(f"  {name:<36} {value}")
    if result.get("small_n_warning"):
        print(f"⚠️ {result['small_n_warning']}")
    print(f"⚠️ {result['caveat']}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "census":
            sdk = _open_sdk(args.uri, args.graph, args.embedded)
            graph = sdk._get_proj().g
            edges = relationship_census(graph)
            nodes = node_census(graph)
            capped = (_org_capped_points(args.org, sdk)
                      if args.org else None)
            view = accounting_view(edges=edges, nodes=nodes,
                                   capped_points=capped)
            if args.org:
                assert_subset_ratio_available(view)
            _print_census(view, as_json=args.json)
            return 0
        result = run_probe(n=args.n, image=args.image,
                           log=lambda m: print(m, file=sys.stderr))
        _print_probe(result, as_json=args.json)
        return 0
    except CensusError as exc:
        print(f"edge_census: {exc}", file=sys.stderr)
        return 2
    except QuotaCheckError as exc:
        # `count_org_usage` fails closed rather than returning a bogus 0; report
        # that as the tool's own exit-2 diagnostic instead of a traceback.
        print(f"edge_census: the cap's own count could not be read: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
