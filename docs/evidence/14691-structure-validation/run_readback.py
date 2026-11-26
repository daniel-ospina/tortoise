#!/usr/bin/env python
"""#14691 T6 — step 2: read the structure back, MCP ONLY.

METHOD RULE (the constraint this lane is judged by): every read here goes
through the **Tortoise MCP server** over stdio — the door a customer's agent
connects to (`python -m tortoise.mcp_server`). This file does not import
`TortoiseSDK`, does not build Cypher, and does not open the record store.

⛔ It also does not reach around the product. T6's assertion 2 asks whether the
ownership/containment edges are correctly ORIENTED, and
`GET /v1/entities/{id}/relations` (`tortoise/entity_reads.py:entity_relations`)
is a real product door that reports direction — but it has **no MCP or SDK
door**, and `AGENTS.md` forbids hand-rolling HTTP to `/v1/…`. So this file
records WHAT THE MCP DOORS ANSWER and nothing else; where a question is
unanswerable over MCP it lands in `out/readback.json` → `door_findings`, exactly
as the issue's method rule requires — never worked around.

What is read:
  (a) the population: `graph_overview`, `list_knowledge` (claims, sources)
  (b) the TYPE axis: a full scan per declared kind. `search_knowledge`'s
      full-scan mode is `query is None and kind is not None`
      (`tortoise/sdk.py:47104`), `kind_field` = `objectKind` / `subjectKind`
      (`tortoise/sdk.py:47100`). ⚠️ MEASURED: the scan only matches the
      NAMESPACED spelling (`product-strategy:product` → 2 rows; bare `product`
      → 0 rows, no error). Both spellings are recorded, so that silent zero is
      evidence rather than a probe bug.
  (c) the EDGE axis, per declared structure element — walked from the resolved
      entity id, NOT from a name: `explore_connections(query=<name>)` resolves a
      name to a **Point**, and the Object's own edges are then two hops away and
      invisible. `explore_connections(node_id=<id>)` is the entity-centric BFS
      (every edge type; `_relationship` = the traversed edge type; NO direction —
      `tortoise/navigation.py:172-179` UNIONs `-[r]->` with `<-r-`).
      `expand_relationships` DOES report direction, but only for the EPISTEMIC
      predicates (`tortoise/entity_reads.py:551-559`), so it is asked too and its
      silence about structural edges is measured, not assumed.
  (d) retrieval by structure, through the search doors — assertion 5
  (e) `check_structure`, the product's own integrity view
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

WT = Path(__file__).resolve().parents[3]
GRAPH = "xv14691_business_structure"

CORPUS = json.loads((HERE / "corpus" / "structure-corpus.json").read_text())
DECL = CORPUS["_declared_structure"]

#: The candidate kind vocabulary, spelled as the packs DECLARE it
#: (`packs/<ns>/manifest.yaml` → `objectKinds:`), because that is the spelling the
#: graph stores and the full scan matches. ⛔ No shipped pack declares a
#: `component` kind — that absence is itself an assertion-1 finding, so every kind
#: a component could plausibly land in is scanned and the finding is measured.
OBJECT_KINDS = (
    ("product-strategy", "product"), ("product-strategy", "feature"),
    ("product-strategy", "customer"), ("product-strategy", "competitor"),
    ("product-strategy", "customerSegment"), ("product-strategy", "market"),
    ("product-strategy", "requirement"),
    ("product-strategy", "architecture"),
    ("product-strategy", "customerProblem"),
    ("dev", "epic"), ("dev", "issue"), ("dev", "code"), ("dev", "api"),
    ("dev", "database"), ("dev", "software"), ("dev", "infrastructure"),
    ("dev", "indicator"), ("dev", "theme"), ("dev", "pullRequest"),
    ("dev", "bug"), ("dev", "incident"), ("dev", "architectureDoc"),
    ("marketing", "campaign"), ("marketing", "content"),
    ("marketing", "channel"), ("marketing", "audience"),
    ("pm", "issue"), ("pm", "sprint"), ("pm", "kanbanBoard"), ("pm", "card"),
    ("agent-ops", "rule"),
    ("core", "project"), ("core", "workItem"), ("core", "problem"),
    ("core", "tag"), ("core", "user"), ("core", "skill"), ("core", "tool"),
    ("core", "agent"), ("core", "workflow"), ("core", "agreement"),
    ("core", "standard"), ("core", "other"), ("core", "strategy"),
    ("core", "plan"), ("core", "goal"), ("core", "target"),
)
SUBJECT_KINDS = (
    ("core", "organization"), ("core", "team"), ("core", "role"),
    ("core", "legalPerson"), ("core", "naturalPerson"), ("core", "other"),
)

#: Bare-vs-namespaced: the same two kinds under both spellings, so the silent zero
#: is recorded as a measured door behaviour rather than described in prose.
BARE_PROBES = (("object", "product"), ("subject", "team"))

#: One probe per retrieval-by-structure question in the issue. `expect` names the
#: corpus fact a correct graph must answer with; `members` is the exact word set
#: the scorer checks for, so the verdict is derived and not eyeballed.
STRUCTURE_PROBES = [
    ("features-of-product",
     "What features belong to Lumen Core?",
     ["Grid Map", "Outage Alerts", "Load Forecast"]),
    ("who-is-on-team",
     "Who is on the Platform team?",
     ["Dana Whitlock", "Tomas Ferreira"]),
    ("depends-on-component",
     "What depends on the fact-store?",
     ["event-bus", "signal-engine"]),
    ("product-of-customer",
     "Which product does Northwind Logistics run?",
     ["Lumen Core"]),
    ("what-team-builds",
     "Which team builds the signal-engine?",
     ["Platform team"]),
]

#: Keys this producer writes unconditionally. `stamp_provenance.py` reads this to
#: tell a producer-written artifact from a hand-carried one.
WRITTEN_KEYS = (
    "graph_name", "db_uri", "ok", "tool_surface", "layers", "object_kinds",
    "subject_kinds", "bare_kind_probes", "elements", "edge_endpoints",
    "link_witness", "retrieval", "structure_check", "door_findings",
    "unresolved_elements", "provenance",
)

ROW_KEYS = ("result", "results", "relationships", "nodes", "items", "connected")


def rows(reply, keys=ROW_KEYS):
    """Normalise an MCP reply to a list of rows (three shapes, one unwrap)."""
    if isinstance(reply, str):
        try:
            return rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return rows(reply[0], keys)
        return [row for row in reply if isinstance(row, dict)]
    return []


def connected_rows(reply):
    """`entityProfile`'s reply → one row per neighbour, layer-tagged."""
    out: list[dict] = []
    if not isinstance(reply, dict):
        return out
    for layer, lst in (reply.get("connected") or {}).items():
        for node in lst or []:
            if isinstance(node, dict):
                out.append({**node, "_layer": layer})
    return out


def _norm(text) -> str:
    return " ".join(str(text or "").lower().split())


def _provenance() -> dict:
    body = Path(__file__).read_bytes()
    return {
        "probe_path": str(Path(__file__).resolve().relative_to(WT)),
        "probe_sha256": hashlib.sha256(body).hexdigest(),
        "graph": GRAPH,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


async def _call(client, tool, args):
    """Call one MCP tool; a refusal is DATA (recorded), never raised away."""
    try:
        res = await client.call_tool(tool, args)
        out = getattr(res, "structured_content", None)
        if out is None:
            out = getattr(res, "data", None)
        if out is None:
            out = [getattr(c, "text", str(c))
                   for c in (getattr(res, "content", None) or [])]
        return {"tool": tool, "args": args, "ok": True, "reply": out}
    except Exception as exc:
        return {"tool": tool, "args": args, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}


ALL_ELEMENTS = ([("product", n) for n in DECL["products"]]
                + [("feature", n) for n in DECL["features"]]
                + [("team", n) for n in DECL["teams"]]
                + [("role", n) for n in DECL["roles"]]
                + [("person", n) for n in DECL["people"]]
                + [("component", n) for n in DECL["components"]]
                + [("customer", n) for n in DECL["customers"]]
                + [("customerSegment", n) for n in DECL["customerSegments"]]
                + [("ownedThing", n) for n, _o in DECL["ownership"]])


async def run() -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH

    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    report: dict = {"graph_name": GRAPH,
                    "db_uri": env["TORTOISE_DB_URI"].split("@")[-1],
                    "provenance": _provenance(),
                    "door_findings": []}

    async with Client(transport, timeout=300) as client:
        tools = {t.name: t for t in await client.list_tools()}
        (OUT / "tool-surface.json").write_text(json.dumps(
            {n: {"description": (t.description or "")[:400],
                 "required": (t.inputSchema or {}).get("required", []),
                 "params": list((t.inputSchema or {}).get("properties") or {})}
             for n, t in sorted(tools.items())}, indent=2) + "\n")
        report["tool_surface"] = {"count": len(tools), "names": sorted(tools)}
        print(f"=== MCP tool surface: {len(tools)} tools ===")

        # ── (a) the population ──────────────────────────────────────────
        report["layers"] = {}
        for tool, args in [
            ("tortoise_graph_overview", {}),
            ("tortoise_list_knowledge", {"kind": "claim", "limit": 200}),
            ("tortoise_list_knowledge", {"kind": "source", "limit": 50}),
        ]:
            key = f"{tool}:{json.dumps(args, sort_keys=True)}"
            r = await _call(client, tool, args)
            report["layers"][key] = r
            print(f"\n=== layer read: {key} ===")
            print(json.dumps(r.get("reply", r), indent=1, default=str)[:3000])

        # ── (b) the TYPE axis: one full scan per declared kind ──────────
        report["object_kinds"] = {}
        resolved: dict[str, dict] = {}
        for ns, kind in OBJECT_KINDS:
            qualified = f"{ns}:{kind}"
            r = await _call(client, "tortoise_search_knowledge",
                            {"kind": qualified, "entity_type": "object",
                             "limit": 100})
            report["object_kinds"][qualified] = r
            got = rows(r.get("reply"))
            print(f"=== object scan kind={qualified!r}: {len(got)} row(s) "
                  f"ok={r['ok']} ===")
            for row in got:
                nm = row.get("content") or row.get("name")
                if nm:
                    resolved.setdefault(_norm(nm), {
                        "id": row.get("id"), "name": nm,
                        "kind": qualified, "layer": "object",
                        "row": row})
            if got:
                print(json.dumps([{k: r_.get(k) for k in
                                   ("id", "content", "point_kind", "status")}
                                  for r_ in got], indent=1, default=str)[:1800])

        report["subject_kinds"] = {}
        for ns, kind in SUBJECT_KINDS:
            qualified = f"{ns}:{kind}"
            r = await _call(client, "tortoise_search_knowledge",
                            {"kind": qualified, "entity_type": "subject",
                             "limit": 100})
            report["subject_kinds"][qualified] = r
            got = rows(r.get("reply"))
            print(f"=== subject scan kind={qualified!r}: {len(got)} row(s) "
                  f"ok={r['ok']} ===")
            for row in got:
                nm = row.get("content") or row.get("name")
                if nm:
                    resolved.setdefault(_norm(nm), {
                        "id": row.get("id"), "name": nm,
                        "kind": qualified, "layer": "subject",
                        "row": row})
            if got:
                print(json.dumps([{k: r_.get(k) for k in
                                   ("id", "content", "point_kind", "status")}
                                  for r_ in got], indent=1, default=str)[:1200])

        # ── (b2) bare vs namespaced spelling, measured ──────────────────
        report["bare_kind_probes"] = {}
        for layer, bare in BARE_PROBES:
            r = await _call(client, "tortoise_search_knowledge",
                            {"kind": bare, "entity_type": layer, "limit": 100})
            report["bare_kind_probes"][f"{layer}:{bare}"] = r
            print(f"=== BARE kind probe {layer}:{bare!r}: "
                  f"{len(rows(r.get('reply')))} row(s) ok={r['ok']} ===")

        # ── (c) the EDGE axis, per declared structure element ───────────
        report["elements"] = []
        report["resolved"] = {k: {"id": v["id"], "kind": v["kind"],
                                  "layer": v["layer"], "name": v["name"]}
                              for k, v in resolved.items()}
        for element_kind, name in ALL_ELEMENTS:
            hit = resolved.get(_norm(name))
            if hit is None:
                # `explore_connections(query=…)` resolves a name to a POINT (it
                # answered "Lumen Core" with a statement about it), so a name that
                # no scan produced is walked that way and recorded as unresolved.
                walk = await _call(client, "tortoise_explore_connections",
                                   {"query": name, "hops": 1})
                report["elements"].append({
                    "element_kind": element_kind, "name": name,
                    "root_id": None, "root_kind": None,
                    "resolution": "unresolved (query walk only)",
                    "walk": walk,
                    "neighbours": connected_rows(walk.get("reply")),
                    "edges": sorted({str(n.get("_relationship"))
                                     for n in connected_rows(walk.get("reply"))}),
                    "expand": {"skipped": "no id"},
                })
                print(f"\n=== element [{element_kind}] {name!r} — UNRESOLVED ===")
                continue
            walk = await _call(client, "tortoise_explore_connections",
                               {"node_id": hit["id"], "hops": 1})
            nb = connected_rows(walk.get("reply"))
            entry = {
                "element_kind": element_kind, "name": name,
                "root_id": hit["id"], "root_kind": hit["kind"],
                "root_layer": hit["layer"],
                "resolution": "typed full scan",
                "walk": walk,
                "neighbours": nb,
                "edges": sorted({str(n.get("_relationship")) for n in nb}),
                "expand": await _call(client, "tortoise_expand_relationships",
                                      {"point_id": hit["id"]}),
            }
            report["elements"].append(entry)
            print(f"\n=== element [{element_kind}] {name!r} "
                  f"({hit['kind']}) — {len(nb)} neighbour(s), "
                  f"edges={entry['edges']} ===")
            print(json.dumps(
                [{k: n.get(k) for k in ("_layer", "_relationship", "name",
                                        "content", "id")} for n in nb],
                indent=1, default=str)[:2200])

        # ── (c2) resolve every edge endpoint the WRITE door reported ────
        # `relation_write.edges` carries ids only (`src_name`/`dst_name` were null
        # on every row of the 2026-11-26 receipt), so a caller cannot say WHICH
        # edge a row is without a second read. `tortoise_get_entity` is that read.
        receipt = json.loads((OUT / "ingest-receipt.json").read_text())
        edge_ids: list[str] = []
        for doc in receipt.get("documents") or []:
            rw = ((doc.get("result") or {}).get("relation_write") or {})
            for edge in rw.get("edges") or []:
                for key in ("src_id", "dst_id"):
                    val = edge.get(key)
                    if isinstance(val, str) and val not in edge_ids:
                        edge_ids.append(val)
        report["edge_endpoints"] = {}
        for eid in edge_ids:
            r = await _call(client, "tortoise_get_entity", {"id": eid})
            report["edge_endpoints"][eid] = r
            props = r.get("reply") if isinstance(r.get("reply"), dict) else {}
            label = (props.get("content") or props.get("name")
                     or props.get("objectKind") or props.get("subjectKind") or "")
            print(f"=== endpoint {eid[:20]}: {str(label)[:60]!r} "
                  f"kind={props.get('objectKind') or props.get('subjectKind') or ''} "
                  f"ok={r['ok']} ===")

        # ── (d) retrieval by structure (assertion 5) ────────────────────
        report["retrieval"] = []
        for label, q, expect in STRUCTURE_PROBES:
            r = await _call(client, "tortoise_search", {"query": q, "limit": 8})
            rk = await _call(client, "tortoise_search_knowledge",
                             {"query": q, "limit": 8})
            walk = await _call(client, "tortoise_explore_connections",
                               {"query": q, "hops": 1})
            report["retrieval"].append({
                "element": label, "query": q, "expected": expect,
                "search": r, "search_knowledge": rk, "walk": walk})
            print(f"\n=== retrieval [{label}] {q!r} ===")
            print(json.dumps(r.get("reply", r), indent=1, default=str)[:3000])

        # ── (d2) the "does this entity have any links at all" witness ────
        # Two doors answer that question and they DISAGREE. `expand_relationships`
        # is the one a caller reaches for, and it returns `[]` for a node whose
        # structural edges exist — the edges are IMPL/NAND-only by construction
        # (entity_reads.py:551-559), so a team with 6 `produces` edges reads as
        # having none. `graph_overview(section="topics", entity_id=…)` is the
        # other, and it answers "Entity … not found" for the same node. Both are
        # recorded verbatim, per node, so the asymmetry is measured from THIS
        # bundle rather than argued.
        report["link_witness"] = {}
        for _element_kind, name in (("product", "Lumen Core"),
                                   ("person", "Amara Osei"),
                                   ("person", "Dana Whitlock"),
                                   ("person", "Tomas Ferreira"),
                                   ("person", "Jonas Bauer"),
                                   ("team", "Platform team"),
                                   ("component", "event-bus")):
            hit = resolved.get(_norm(name))
            if not hit:
                continue
            eid = hit["id"]
            expand_r = await _call(client, "tortoise_expand_relationships",
                                   {"point_id": eid})
            topics_r = await _call(client, "tortoise_graph_overview",
                                   {"section": "topics", "entity_id": eid})
            walk_r = await _call(client, "tortoise_explore_connections",
                                 {"node_id": eid, "hops": 1})
            # The OWNERSHIP door: `type="governance"` dispatches to
            # `tortoise_get_governance` → `sdk._get_owned_entities`, which reads
            # `MATCH (s:Subject)<-[:ownedBy]-(e)` (sdk.py:62739-62758) — the
            # DECLARED orientation. If the writer points the other way, this door
            # answers `[]` on a graph that holds ownership rows, which is the one
            # read that can witness the inversion directly.
            governance_r = await _call(client, "tortoise_get_entity",
                                       {"id": eid, "type": "governance"})
            report["link_witness"][name] = {
                "id": eid, "kind": hit["kind"],
                "expand_relationships": expand_r,
                "graph_overview_topics": topics_r,
                "governance_owned_by": governance_r,
                "explore_neighbour_count": len(
                    connected_rows(walk_r.get("reply"))),
            }
            print(f"\n=== link witness {name}: expand="
                  f"{len(rows(expand_r.get('reply')))} row(s), topics="
                  f"{json.dumps(topics_r.get('reply'), default=str)[:100]}, "
                  f"governance(owned)={json.dumps(governance_r.get('reply'), default=str)[:100]}, "
                  f"explore neighbours={report['link_witness'][name]['explore_neighbour_count']} ===")

        # ── (e) the product's own integrity view ────────────────────────
        report["structure_check"] = await _call(
            client, "tortoise_check_structure", {})
        print("\n=== check_structure ===")
        print(json.dumps(report["structure_check"].get("reply"),
                         indent=1, default=str)[:2000])

    # ── (f) the door findings T6 must state, measured not assumed ──────
    structural_edges = {"ownedBy", "hasPart", "dependsOn", "memberOf",
                        "holdsRole", "manages", "managedBy", "contains"}
    seen_edges = {e for entry in report["elements"]
                  for e in entry.get("edges") or []}
    if structural_edges & seen_edges:
        report["door_findings"].append({
            "id": "D1",
            "question": "is this structural edge oriented src→dst?",
            "answer_over_mcp": "NO — the edge and its type are visible, its "
                               "direction is not",
            "evidence": {
                "structural_edges_seen": sorted(structural_edges & seen_edges),
                "explore_connections": "UNIONs -[r]-> with <-r- and keeps type(r) "
                                       "only (tortoise/navigation.py:172-179)",
                "expand_relationships": "reports direction, but only for "
                                        "IMPL|NAND|hasPart (tortoise/entity_reads.py:551-559)",
                "the_read_that_can": "entity_reads.entity_relations behind "
                                     "GET /v1/entities/{id}/relations — no MCP/SDK door",
            },
        })

    unresolved = [f"{e['element_kind']}:{e['name']}" for e in report["elements"]
                  if not e.get("root_id")]
    report["unresolved_elements"] = unresolved
    if unresolved:
        report["door_findings"].append({
            "id": "D2",
            "question": "can a declared structure element be resolved by NAME "
                        "through the MCP doors?",
            "answer_over_mcp": f"NO for {len(unresolved)} of "
                               f"{len(report['elements'])}",
            "unresolved": unresolved,
        })

    bare_hits = {k: len(rows(v.get("reply")))
                 for k, v in report["bare_kind_probes"].items()}
    ns_hits = {}
    for qualified, r in {**report["object_kinds"],
                         **report["subject_kinds"]}.items():
        ns_hits[qualified] = len(rows(r.get("reply")))
    silent = [k for k, n in bare_hits.items()
              if n == 0 and ns_hits.get(
                  (f"product-strategy:{k.split(':')[1]}"
                   if k.startswith("object:")
                   else f"core:{k.split(':')[1]}"), 0) > 0]
    if silent:
        report["door_findings"].append({
            "id": "D3",
            "question": "does the full-scan door accept the BARE kind the packs "
                        "declare?",
            "answer_over_mcp": "NO — a bare kind returns an EMPTY list with no "
                               "error while the namespaced spelling returns rows",
            "evidence": {"bare_returned": bare_hits,
                         "namespaced_returned": {k: v for k, v in ns_hits.items()
                                                 if v},
                         "silent_zero_for": silent},
        })

    silent = {name: w["explore_neighbour_count"]
              for name, w in (report.get("link_witness") or {}).items()
              if len(rows(w["expand_relationships"].get("reply"))) == 0
              and w["explore_neighbour_count"] > 0}
    if silent:
        report["door_findings"].append({
            "id": "D4",
            "question": "does `expand_relationships` report the STRUCTURAL edges "
                        "of an entity, the way it reports the epistemic ones?",
            "answer_over_mcp": "NO — it returns [] for a node that has edges, so "
                               "a structurally-connected entity reads as isolated",
            "evidence": {"nodes_read_as_isolated_by_expand": silent,
                         "same_nodes_seen_by_explore_connections": {
                             k: v for k, v in silent.items()},
                         "code_path": "tortoise/entity_reads.py:551-559 — the "
                                      "read covers IMPL|NAND|hasPart only"},
        })

    code = 0 if all(r["ok"] for r in report["layers"].values()) else 1
    report["ok"] = code == 0
    (OUT / "readback.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'readback.json'}")
    if code:
        print("!! readback NOT OK: a layer read errored — see out/readback.json")
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
