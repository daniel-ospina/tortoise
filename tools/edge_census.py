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

This tool measures that. Its reads are **read-only**, and the boundary is
worth stating exactly:

- A census over a **`--uri`** connection and **without `--org`** reads through
  a **raw `falkordb` client** and issues no DDL whatsoever. Nothing on the
  caller's graph is created, altered or deleted. This is the path to use
  against a graph you do not own.
- A census over **`--embedded <path>`** necessarily opens the **SDK**, because
  the embedded backend is the SDK's own entry point — so it constructs a
  projection, which ensures indexes on that database (idempotent; the
  application does the same at startup) and may run a health recovery. The
  ``--embedded`` path therefore writes schema and must not be pointed at a
  database you need left untouched.
- **`--org`** also opens the **SDK**, either path, because the cap's count
  must come from the cap's own function rather than a second implementation of
  its predicate. With ``--uri`` the SDK is the **only** handle — the census is
  taken from that same handle, so the edge count and the cap count can never
  describe two different graphs. A read against a production graph you do not
  own should therefore use a URI and no ``--org``.
- ⛔ **`--org` over `--uri` writes schema, and cannot be made not to.** The cap
  count runs through ``sdk._get_proj()``, and constructing the projection runs
  ``_ensure_indexes()`` unconditionally — so the DDL is not a side effect of
  *this* tool's handle choice; it is inside the cap's own function (measured:
  ``--uri`` with ``--org`` created 6 indexes on the target graph, including a
  384-dim VECTOR index on ``Point``; ``--uri`` without ``--org`` created 0).
  Because it cannot be avoided by opening the graph differently, it is instead
  **refused by default**: ``--uri`` + ``--org`` exits 2 unless
  ``--accept-schema-writes`` is passed. The ``--embedded`` path needs no flag
  — the embedded backend *is* the SDK, so that lane is already understood to
  ensure indexes.
- A graph that **does not exist** is REFUSED, never created. FalkorDB
  materialises the keyspace on the first query, so a mistyped graph name would
  otherwise be created by the act of measuring it; ``--create-if-missing`` is
  the deliberate opt-in.
- The only writes the tool itself performs are inside a **disposable isolated
  container it starts and removes itself** (``--network none``, ``--rm``), for
  the RAM probe.

Relationship-type names are read from the graph and are therefore never
interpolated into Cypher. The census returns the name as a **value**
(``RETURN type(r), count(r)``), so a name crafted to look like Cypher has no
interpreter to reach at all — strictly stronger than binding it as a parameter,
and it counts types no allowlist has ever heard of. Names that reach the
**human-readable** report are also sanitised (``_printable``): a stored name can
carry ANSI/OSC sequences or newlines, and the report is an interpreter too.

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
101.8 B and 173.6 B for the same bare-node shape — allocation granularity, not
a contradiction).

Usage::

    python3 tools/edge_census.py census --uri docker://:falkordb@localhost:6379/mygraph
    python3 tools/edge_census.py census --uri ... --org org_abc123 --accept-schema-writes
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

def _scalar(graph: Any, cypher: str, params: dict | None = None) -> int:
    """Run a single-value Cypher query and return the int.

    ``graph`` is anything exposing ``.query(cypher) -> obj.result_set`` — the
    FalkorDB client and ``proj.g`` both do. An empty or non-integer result is
    an error, not a 0: a count this tool cannot read must never be printed as
    a count of zero.

    ``params`` exists so graph-sourced strings can be bound rather than
    interpolated — see ``relationship_census``.
    """
    try:
        result = graph.query(cypher, params=params)
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


def _rows(graph: Any, cypher: str,
          params: dict | None = None) -> list[Any]:
    """Run a multi-row query, failing LOUD on an unreadable result.

    The multi-row sibling of :func:`_scalar`: an unreadable result set is an
    error, never an empty list. A census that cannot read a breakdown must not
    report one as absent.
    """
    try:
        result = graph.query(cypher, params=params)
    except Exception as exc:
        raise CensusError(f"query failed: {cypher!r}: {exc}") from exc
    rows = getattr(result, "result_set", None)
    if rows is None:
        raise CensusError(f"query returned no result set: {cypher!r}")
    return list(rows)


# ── the census ─────────────────────────────────────────────────────────────

def relationship_census(graph: Any) -> dict[str, Any]:
    """Count relationships on ``graph``: total, per type, per EP slot.

    Returns a dict with:

    - ``total`` — every relationship, of every type.
    - ``by_type`` — ``{type: count}`` for every relationship type present.
    - ``by_slot`` — ``{slot: count}`` for each of :data:`EP_EDGE_SLOTS` —
      edges carrying that property.
    - ``ep_bearing`` — edges carrying **at least one** slot.
    - ``all_four_slots`` — edges carrying **all four**.

    Both ``ep_bearing`` and ``all_four_slots`` are reported because a
    half-written edge is a different thing from a fully-messaged one, and the
    difference is invisible if only one of the two is printed.

    ⛔ The total and the breakdown come from **one** query. Reading a total and
    then each type's count as separate round-trips let the two disagree under
    any concurrent write — an edge added between them is counted in the
    breakdown but not the total — so a *healthy* graph aborted on a false
    refusal. One aggregate read cannot disagree with itself.

    ⛔ The type NAME never enters the query text: it is returned as a **value**
    (``RETURN type(r), count(r)``), so a name crafted to look like Cypher has
    no interpreter to reach. Interpolating it into the pattern
    (``-[r:TYPE]->``) was a WRITE primitive — review demonstrated a type stored
    as ``IMPL]->() DELETE r WITH 1 AS x MATCH ()-[r`` producing a query that
    DELETED the graph's relationships and returned the deletion count as a
    "count". Returning it as data removes the primitive entirely and counts
    types the allowlist has never heard of.
    """
    by_type: dict[str, int] = {}
    for row in _rows(graph, "MATCH ()-[r]->() RETURN type(r), count(r)"):
        if (not row or len(row) < 2 or not isinstance(row[0], str)
                or isinstance(row[1], bool) or not isinstance(row[1], int)):
            raise CensusError(
                f"unreadable per-type row from the graph: {row!r} — refusing "
                f"to report a partial breakdown as the whole one")
        by_type[row[0]] = row[1]
    total = sum(by_type.values())
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
        elif delta == 0:
            # The mirror of the case above, and it is the one that bites: a
            # stage that DECLARES elements while `used_memory` did not move at
            # all means the staged write did not happen (or was not observed).
            # Emitting `marginal_bytes: 0.0` there is not a small number, it is
            # an absent measurement wearing a number's clothes — and adding
            # N>=100 elements to a fresh instance always grows used_memory, so
            # the honest reading is "this stage did not run".
            raise CensusError(
                f"stage {stage.label!r} declares per_element="
                f"{stage.per_element} but used_memory did not move "
                f"({previous.used_memory} -> {stage.used_memory}) — the staged "
                f"write did not happen, so a 0.0 marginal here would be a "
                f"false measurement"
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


def _docker(*args: str, check: bool = True,
            timeout: float | None = 60.0) -> subprocess.CompletedProcess:
    """Run ``docker`` with a BOUND.

    ``timeout`` matters: the readiness loop's own deadline is only checked
    BETWEEN iterations, so a single wedged ``docker exec`` (a hung daemon, a
    socket in an uninterruptible state) would block past it indefinitely. The
    bound turns that into an ordinary failure.
    """
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=check,
        timeout=timeout)


def docker_available() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}",
                       check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _container_used_memory(container: str) -> int:
    try:
        proc = _docker("exec", container, "redis-cli", "INFO", "memory")
    except (subprocess.SubprocessError, OSError) as exc:
        raise CensusError(
            f"could not read used_memory from {container!r}: {exc}") from exc
    for line in proc.stdout.replace("\r", "").splitlines():
        if line.startswith("used_memory:"):
            return int(line.split(":", 1)[1])
    raise CensusError(
        f"used_memory unreadable from {container!r} — refusing to report a "
        f"size from a probe that could not be read")


def _container_query(container: str, graph: str, cypher: str) -> None:
    """Run one staged write in the probe container, failing LOUD on refusal.

    ⛔ ``redis-cli`` exits 0 on a SERVER-SIDE error, so the exit code alone is
    not a verdict. Worse, the error is not always spelled ``errMsg`` — only the
    FalkorDB-wrapped Cypher errors are. Every other refusable command returns a
    bare error token on STDOUT with returncode 0: ``WRONGTYPE ...``,
    ``OOM command not allowed ...``, ``NOAUTH``, ``LOADING``, ``MISCONF``,
    ``READONLY``, ``BUSY``. Accepting any of those leaves a stage that did
    nothing with its ``used_memory`` delta attributed to it — a false marginal,
    which is exactly the "silence is how a probe lies" failure
    ``probe_marginals`` exists to catch.

    An EMPTY stdout is also a failure: a successful ``GRAPH.QUERY`` always
    prints its statistics.
    """
    proc = _docker("exec", container, "redis-cli", "GRAPH.QUERY", graph, cypher,
                   check=False)
    out = proc.stdout or ""
    head = out.lstrip()
    if (proc.returncode != 0 or not head or "errMsg" in out
            or any(head.startswith(tok) for tok in _REDIS_ERROR_TOKENS)):
        raise CensusError(
            f"probe query failed in {container!r}: {cypher!r}: "
            f"{(proc.stderr or out).strip()[:400]}")


#: Error tokens ``redis-cli`` prints on STDOUT with returncode 0. FalkorDB's
#: own Cypher refusals arrive wrapped in ``errMsg``; every other refusable
#: command returns the bare token as the FIRST thing it writes.
_REDIS_ERROR_TOKENS = (
    "ERR", "errMsg", "WRONGTYPE", "OOM ", "NOAUTH", "LOADING", "MISCONF",
    "READONLY", "BUSY", "NOSCRIPT", "EXECABORT", "NOPERM", "WRONGPASS",
)


#: Label every probe container, so an orphan from a SIGKILLed run is
#: identifiable and reapable instead of accumulating invisibly.
_PROBE_LABEL = "edge-census-probe=1"


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


def derive_marginals(stages: list[Stage], *, n: int,
                     edges: int) -> dict[str, Any]:
    """Turn raw stage readings into the reported marginals.

    Extracted from ``run_probe`` so the ARITHMETIC is reachable without docker.
    ``run_probe`` needs a container, so before this split a divisor could be
    changed to the wrong denominator — ``/ n`` where ``/ edges`` belongs — with
    the whole suite still green. The module calls that property load-bearing
    ("a per-element number without its ``n`` is not reproducible"), so the
    divisors are pinned by tests against this function directly.

    Every edge marginal is measured from the stage that PRECEDED the edges (the
    dressed-node stage) — not from the bare-node stage. Dividing
    ``(final - bare_nodes)`` by the edge count would fold the keyword-prop node
    cost into the per-edge figure, which is the dressed-vs-bare mistake this
    census exists to avoid.
    """
    derived = probe_marginals(stages)
    by_label = {d["label"]: d for d in derived}
    baseline = stages[0].used_memory
    bare_node = by_label["bare :Point node (label + id only)"]["marginal_bytes"]
    kw_point = by_label["+ keyword-only Point props"]
    kw_point_used = kw_point["used_memory"]
    # / n — the keyword-only stage holds n NODES, not edges.
    kw_point_total = round((kw_point_used - baseline) / n, 1)
    bare_edge = by_label["bare :IMPL edge (no properties)"]["marginal_bytes"]
    attrs = by_label["+ real IMPL attrs "
                     "(direction,confidence,weight,label,batch_id)"]
    ep_stage = by_label["+ the four EP message slots"]
    # / edges — the EP-slot delta is the cost of dressing `edges` EDGES.
    dressed_edge = round((ep_stage["used_memory"] - kw_point_used) / edges, 1)

    return {
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
                   "bare-node figure moved 101.8 B (n=20000) to 173.6 B "
                   "(n=5000) between runs — allocation granularity, so a "
                   "figure without its n is not reproducible"),
    }


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
    # `--rm` so a failure that escapes the teardown below still removes the
    # container; `--network none` so the probe cannot reach any sibling
    # container — the caller's own FalkorDB is routinely a bridge container on
    # the documented local URI, and the probe only ever talks to its own
    # loopback. `--label` marks an orphan (SIGKILL / host crash) reapable.
    # The `run` is INSIDE the try: starting the container is the step most
    # likely to fail (image pull, name collision, daemon error), and above the
    # try a failure there left a container behind with no `rm`.
    try:
        try:
            _docker("run", "-d", "--rm", "--network", "none",
                    "--label", _PROBE_LABEL, "--name", container, image)
        except (subprocess.SubprocessError, OSError) as exc:
            raise CensusError(
                f"could not start the probe container from {image!r}: {exc}"
            ) from exc
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

    measured = derive_marginals(stages, n=n, edges=edges)

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
        **measured,
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def _assert_graph_exists(uri: str, graph_name: str | None,
                        *, create_if_missing: bool = False) -> None:
    """Refuse to measure a graph that does not exist yet.

    ⛔ A read against a graph that does not exist still has a side effect:
    FalkorDB materialises the keyspace on the first query, so a bare ``MATCH``
    creates an empty graph of that name. On a production instance a typo'd
    graph name would therefore be created by the very act of measuring it.

    ``GRAPH.LIST`` does not itself create anything (verified against a live
    instance), so this check is side-effect free — unlike the query it guards.
    """
    from falkordb import FalkorDB

    from tortoise.projection import resolve_db_endpoint

    endpoint = resolve_db_endpoint(uri, graph_name)
    client = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl)
    if endpoint.graph_name not in client.list_graphs() and not create_if_missing:
        raise CensusError(
            f"graph {endpoint.graph_name!r} does not exist on "
            f"{endpoint.host}:{endpoint.port} — and reading a non-existent "
            f"graph CREATES it (FalkorDB materialises the keyspace on the "
            f"first query), so this tool will not be the thing that creates "
            f"it. Check the name, or pass --create-if-missing to accept that "
            f"side effect deliberately")


def _raw_graph_from_uri(uri: str, graph_name: str | None,
                        *, create_if_missing: bool = False):
    """A RAW FalkorDB handle — no SDK, so no DDL touches the caller's graph.

    ⛔ The SDK is the obvious handle here and it is the WRONG one for a
    measurement tool. ``sdk._get_proj()`` constructs a ``FalkorProjection``
    whose ``__init__`` unconditionally runs ``_ensure_indexes()`` — CREATE
    INDEX / DROP INDEX DDL issued against the target graph — and an embedded
    health recovery that can rebuild a database. A tool whose entire promise
    is "read, change nothing" must not open a handle that writes schema and
    then recommend itself against a live production graph.

    ⛔ A read against a graph that DOES NOT EXIST still has a side effect:
    FalkorDB materialises the keyspace on the first query, so a bare ``MATCH``
    creates an empty graph of that name. On a production instance a typo'd
    graph name would therefore be created by the very act of measuring it. The
    existence check below costs one ``GRAPH.LIST`` and refuses instead.

    This path reuses the repo's own URI parser (``resolve_db_endpoint``, the
    canonical derivation) and nothing else.
    """
    from falkordb import FalkorDB

    from tortoise.projection import resolve_db_endpoint

    endpoint = resolve_db_endpoint(uri, graph_name)
    _assert_graph_exists(uri, graph_name, create_if_missing=create_if_missing)
    client = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl)
    return client.select_graph(endpoint.graph_name)


def _printable(name: str) -> str:
    """Make a GRAPH-SOURCED string safe to print.

    A relationship-type name is graph data, and the report is an interpreter
    like any other: control bytes in a stored name drive the terminal — clear
    screen, set the window title, OSC 52 clipboard writes — and an embedded
    newline forges report rows (demonstrated in review with a type name
    containing both an OSC 52 sequence and ``\\n  by slot  msg_beta  -88888``).
    The ``--json`` path needs none of this (``json.dumps`` escapes), so this is
    the human-readable path's sanitiser only.
    """
    return "".join(
        ch if ch.isprintable() and ch != "\x7f" else f"\\x{ord(ch):02x}"
        for ch in name)


def _open_sdk(uri: str | None, graph_name: str | None, embedded: str | None):
    """Open an SDK handle. Needed only for ``--org`` (the cap's own function).

    ⚠️ Opening the SDK is NOT side-effect free: it constructs a projection,
    which ensures indexes on the target graph (and, on the embedded path, may
    run a health recovery). That is why the plain census does not use this
    path — see ``_raw_graph_from_uri`` — and why ``--org`` is the only thing
    that pays the cost.

    The caller's ``TORTOISE_DB_URI`` is restored before returning: the SDK
    captures ``self._db_uri`` in ``__init__`` and ``_get_proj()`` reads that
    captured value, so restoring the environment afterwards does not change
    which graph the handle binds — but it does stop one in-process invocation
    from re-pointing the next one (``main()`` is called in-process by this
    tool's own tests, and by any script that imports it).
    """
    from tortoise.sdk import TortoiseSDK  # imported lazily — probe needs no DB

    prior = os.environ.get("TORTOISE_DB_URI")
    try:
        # ⛔ `embedded` is checked FIRST. It used to be second, and that order
        # was a P0: `main()` folded an ambient ``TORTOISE_DB_URI`` into `uri`
        # even when ``--embedded`` was passed, so `--embedded /tmp/local.db`
        # with that variable set in the environment opened the URI reader
        # instead — running ``_ensure_indexes()`` on a graph the user never
        # named and then censusing it. Whichever database the caller named
        # must win over one inherited from the environment.
        if embedded:
            os.environ.pop("TORTOISE_DB_URI", None)
            return TortoiseSDK(embedded, graph_name=graph_name)
        if uri:
            os.environ["TORTOISE_DB_URI"] = uri
            return TortoiseSDK(graph_name=graph_name)
    finally:
        if prior is None:
            os.environ.pop("TORTOISE_DB_URI", None)
        else:
            os.environ["TORTOISE_DB_URI"] = prior
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
        # GRAPH-SOURCED name: sanitise before it reaches the terminal.
        print(f"  by type  {_printable(rtype):<24} {count}")
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
        description="Edge census + isolated per-edge RAM probe (#4503). A "
                    "census over --uri WITHOUT --org issues no DDL; --embedded "
                    "always ensures indexes on its own database, and --uri "
                    "with --org is refused unless --accept-schema-writes is "
                    "given. No cap, meter or price is ever changed.")
    sub = parser.add_subparsers(dest="command", required=True)

    census = sub.add_parser("census", help="count relationships and EP slots")
    census.add_argument(
        "--uri",
        help="FalkorDB URI (docker:// / redis:// / rediss://). Falls back to "
             "the TORTOISE_DB_URI environment variable, so a password need "
             "not appear in the process argument list.")
    census.add_argument("--graph", help="graph name override")
    census.add_argument("--embedded", help="embedded DB path (no URI)")
    census.add_argument("--org", help="org id — also read the cap's own count")
    census.add_argument(
        "--accept-schema-writes", action="store_true",
        help="accept that --org over --uri runs the SDK, which ensures "
             "indexes on the censused graph (CREATE/DROP INDEX, incl. a "
             "vector index). The cap's own count goes through the same "
             "projection, so this DDL cannot be avoided by choosing a "
             "different handle — without this flag the tool refuses instead "
             "of writing schema you did not agree to")
    census.add_argument(
        "--create-if-missing", action="store_true",
        help="accept that reading a NON-EXISTENT graph creates it (FalkorDB "
             "materialises the keyspace on the first query); without this the "
             "census refuses instead of creating a graph by accident")
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
            # ⛔ An ambient ``TORTOISE_DB_URI`` must NOT be folded in when
            # ``--embedded`` names the database. It was, and because the SDK
            # open preferred the URI, `--embedded /tmp/local.db` against a box
            # with that variable set silently opened the URI graph instead:
            # it ran ``_ensure_indexes()`` there with NO consent flag, and then
            # reported that graph's counts. Two failures from one root cause —
            # schema written to a graph the user never named, and a measurement
            # of the wrong graph. Naming both is contradictory, so it is
            # refused rather than silently resolved in either direction.
            if args.embedded and args.uri:
                raise CensusError(
                    "--embedded and --uri name two different databases; pass "
                    "exactly one")
            # `--uri` first, then the environment: a password on argv is
            # visible to every process listing on the host. Same variable the
            # SDK reads, so both spellings reach the same connection.
            uri = args.uri or (None if args.embedded
                               else os.environ.get("TORTOISE_DB_URI")) or None
            return _run_census(args, uri)
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
    except (subprocess.SubprocessError, OSError) as exc:
        # A missing/broken docker or socket is a tool-level failure, not a
        # traceback: translate it to the same exit-2 diagnostic as everything
        # else the tool cannot answer honestly.
        print(f"edge_census: external command failed: {exc}", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as exc:
        # Ordinary BAD INPUT, not a crash: an unsupported URI scheme
        # (`resolve_db_endpoint`), an invalid graph name or an empty production
        # URI (`TortoiseSDK.__init__`). These are the tool's own diagnostics —
        # a traceback here is a contract deviation, not extra information.
        print(f"edge_census: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # ⛔ A broker that is DOWN is not a crash. `redis` raises its own
        # exception family — ``ConnectionError -> RedisError -> Exception`` —
        # which is NEITHER a RuntimeError NOR a ValueError, so a refused
        # socket escaped the two clauses above as a traceback and exit 1, in a
        # tool whose contract says exit 2. Reproduced against a closed port:
        # ``--uri docker://:falkordb@localhost:65533/...`` raised
        # ``redis.exceptions.ConnectionError`` straight past this handler.
        #
        # Narrowed to that family before translating, so a genuine defect still
        # surfaces as a traceback rather than being reported as bad input.
        from redis.exceptions import RedisError
        if not isinstance(exc, RedisError):
            raise
        print(f"edge_census: could not reach the graph: {exc}",
              file=sys.stderr)
        return 2


def _run_census(args: Any, uri: str | None) -> int:
    """Run the census, keeping the SDK — and its schema writes — optional.

    A **URI** census WITHOUT ``--org`` touches the graph through the raw client
    only, so it issues no DDL at all. ``--embedded`` opens the SDK on every
    path, ``--org`` or not — the embedded backend IS the SDK, so that lane
    always ensures indexes.

    ``--org`` over a **URI** has to open the SDK, because the cap count must
    come from the cap's own function rather than a second implementation of its
    predicate — and that path runs ``_ensure_indexes()``. That DDL is therefore
    NOT something this function can decline on the caller's behalf, so it is
    refused unless ``--accept-schema-writes`` says the schema write is wanted.

    The SDK is closed again on the way out.
    """
    if uri and args.org and not args.accept_schema_writes:
        raise CensusError(
            "--org needs the SDK, and opening the SDK runs "
            "_ensure_indexes() — CREATE/DROP INDEX DDL on the very graph "
            "being censused (a 384-dim VECTOR index among them). That "
            "cannot be avoided by opening the graph another way, because "
            "the cap's own count runs through the same projection. Re-run "
            "with --accept-schema-writes if that write is acceptable, or "
            "drop --org to census the graph without touching its schema")
    sdk = None
    try:
        if args.embedded or (uri and args.org):
            # ⛔ ONE handle. `--org` needs the SDK (the cap count must come from
            # the cap's own function), so the census is taken from THAT SDK's
            # graph. Opening a raw handle for the census and a second SDK for
            # the cap read `--uri` twice, and any divergence between them — a
            # different default graph, a re-resolution — would pair an edge
            # count from graph A with a cap count from graph B.
            # The existence check stays OUTSIDE the SDK: the SDK's own open
            # would CREATE a missing graph (and ensure indexes on a present
            # one), so the refusal has to come first.
            if uri:
                _assert_graph_exists(
                    uri, args.graph,
                    create_if_missing=args.create_if_missing)
            sdk = _open_sdk(uri, args.graph, args.embedded)
            graph = sdk._get_proj().g
        elif uri:
            graph = _raw_graph_from_uri(uri, args.graph,
                                        create_if_missing=args.create_if_missing)
        else:
            raise CensusError("census needs --uri, --embedded, or "
                              "TORTOISE_DB_URI")
        edges = relationship_census(graph)
        nodes = node_census(graph)
        capped = _org_capped_points(args.org, sdk) if args.org else None
        view = accounting_view(edges=edges, nodes=nodes,
                               capped_points=capped)
        if args.org:
            assert_subset_ratio_available(view)
        _print_census(view, as_json=args.json)
        return 0
    finally:
        if sdk is not None:
            sdk.close()


if __name__ == "__main__":
    raise SystemExit(main())
