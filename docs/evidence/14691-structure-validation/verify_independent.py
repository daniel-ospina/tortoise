#!/usr/bin/env python
"""#14691 T6 — the SECOND measurement: an independent read-only probe.

⛔ THIS SCRIPT IS NOT THE PRODUCER OF `out/t6-independent-verification.json`.
That file's own `measured_by` field states where it came from — "second lane,
independent probe (`/tmp/t6_probe2.py` + `/tmp/t6_probe4.py`)" — two scripts the
peer lane ran from scratch and did not commit. The report shapes are disjoint
beyond the `graph` key (this script emits
`graph, tools, tool_count, probes{taxonomy,retrieval,list_by_kind},
subgraph_stats, nodes, edges, resolved_ids, claimed_edges, governance`;
the artifact carries
`graph, measured_by, declared, A1, A2, A2_edge_row_counts_from_receipt, A3, A5,
witness_governance_door_returns_empty, witness_neighbourhood_doors,
tool_surface`), so a run of this script can never regenerate that artifact and
must not be read as regenerating it. `REPORT.md` §8 records the same split.

What it IS: a RE-RUNNABLE independent read of the same graph, over the product's
own MCP door only, kept so the second measurement's shape can be re-measured
without waiting for a peer lane. It writes nothing to the graph and nothing
inside this bundle: its report lands on stdout-adjacent scratch (`T6_PROBE_OUT`,
default `/tmp/t6-probe2.json`) and is a measurement in its OWN format.

The worktree it probes is resolved from THIS file (`parents[3]`), exactly as
`run_readback.py` does — an earlier revision hard-coded
`/home/cmux/tortoise/.worktrees/t6-structure`, which made the script unrunnable on
any other checkout (or after this lane's worktree was reaped) while its docstring
claimed the second measurement was reproducible. `TORTOISE_DB_URI` is supplied by
the caller exactly as for the other phases; `run.sh` supplies it.

Revision 2 (depth 2):

Rev-1 measured at `depth=1` from name seeds, which starts at a POINT and can
only reach the point's `about*` neighbours — so product→feature `hasPart` and
`ownedBy` edges (OBJECT→OBJECT / OBJECT→SUBJECT) were invisible even though the
ingest receipt records them as written. Rev-2 expands depth=2 and resolves every
endpoint id the receipt names via `tortoise_get_entity`, so the direction of each
structural edge is read from the graph rather than inferred from the receipt.

Writes nothing: no graph write, no file inside the repo (a peer lane owns that).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
EVID = WT / "docs/evidence/14691-structure-validation"
GRAPH = "xv14691_business_structure"
#: Scratch destination, OVERRIDABLE: the committed artifact was produced by a
#: peer lane's `/tmp` probes, not by this script, so this path must never read as
#: "where the bundle's evidence lives" (`T6_PROBE_OUT` names it explicitly).
OUT = Path(os.environ.get("T6_PROBE_OUT", "/tmp/t6-probe2.json"))

PRODUCTS = ["Lumen Core", "Lumen Field"]
FEATURES = ["Grid Map", "Outage Alerts", "Load Forecast", "Crew Dispatch",
            "Asset Register"]
TEAMS = ["Platform team", "Product team", "Customer team"]
ROLES = ["Staff Engineer", "Site Reliability Engineer", "Product Manager",
         "Account Executive"]
PEOPLE = ["Dana Whitlock", "Tomas Ferreira", "Amara Osei", "Jonas Bauer"]
COMPONENTS = ["ingest-gateway", "event-bus", "fact-store", "signal-engine",
              "web-dashboard", "auth-service", "identity-db"]
CUSTOMERS = ["Northwind Logistics", "Helio Health"]
SEGMENT = ["mid-market utilities"]

ALL_NAMES = (PRODUCTS + FEATURES + TEAMS + ROLES + PEOPLE + COMPONENTS
             + CUSTOMERS + SEGMENT)

QUESTIONS = [
    ("features-of-product", "What features belong to Lumen Core?"),
    ("who-on-team", "Who is on the Platform team?"),
    ("depends-on", "What depends on the fact-store?"),
    ("who-owns", "Who owns Lumen Core?"),
]


async def _call(client, tool, args):
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


def _norm(s):
    return " ".join(str(s or "").lower().split())


def as_obj(reply):
    if isinstance(reply, str):
        try:
            return json.loads(reply)
        except (ValueError, TypeError):
            return reply
    if isinstance(reply, list) and len(reply) == 1 and isinstance(reply[0], str):
        try:
            return json.loads(reply[0])
        except (ValueError, TypeError):
            return reply
    return reply


async def run():
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH
    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env,
        cwd=str(WT))

    report = {"graph": GRAPH, "probes": {}}
    async with Client(transport, timeout=600) as client:
        report["tools"] = sorted(t.name for t in await client.list_tools())
        report["tool_count"] = len(report["tools"])
        print(f"tools={report['tool_count']} "
              f"recall_listed={'tortoise_recall' in report['tools']} "
              f"governance_listed={'tortoise_get_governance' in report['tools']}")

        report["probes"]["taxonomy"] = await _call(
            client, "tortoise_graph_overview", {})

        # (a) depth-2 subgraph from every declared name; union nodes + edges
        nodes, edges, replies = {}, {}, {}
        for name in ALL_NAMES:
            r = await _call(client, "tortoise_recall",
                            {"query": name, "mode": "subgraph", "seed": name,
                             "depth": 2, "max_nodes": 400})
            body = as_obj(r.get("reply"))
            replies[name] = r if not isinstance(body, dict) else {"ok": True,
                                                                 "stats": body.get("stats")}
            if not isinstance(body, dict):
                continue
            for n in body.get("nodes") or []:
                nodes[n.get("id")] = n
            for e in body.get("edges") or []:
                edges[(e.get("source"), e.get("type"), e.get("target"))] = e
        report["subgraph_stats"] = replies
        report["nodes"] = nodes
        report["edges"] = [
            {"source": s, "type": t, "target": d,
             "source_name": (nodes.get(s) or {}).get("content"),
             "source_type": (nodes.get(s) or {}).get("type"),
             "target_name": (nodes.get(d) or {}).get("content"),
             "target_type": (nodes.get(d) or {}).get("type")}
            for (s, t, d) in edges
        ]
        print(f"nodes={len(nodes)} edges={len(edges)}")

        # (b) resolve every endpoint id the INGEST RECEIPT claims it wrote
        receipt = json.loads((EVID / "out/ingest-receipt.json").read_text())
        claimed = []
        for row in receipt["documents"]:
            for e in ((row.get("result") or {}).get("relation_write")
                      or {}).get("edges") or []:
                claimed.append({"doc": row["doc_id"],
                                "predicate": e.get("predicate"),
                                "stated": e.get("stated_predicate"),
                                "declared_by": e.get("declared_by"),
                                "src_id": e.get("src_id"),
                                "dst_id": e.get("dst_id")})
        ids = sorted({c["src_id"] for c in claimed} | {c["dst_id"] for c in claimed})
        resolved = {}
        for nid in ids:
            r = await _call(client, "tortoise_get_entity", {"id": nid})
            body = as_obj(r.get("reply"))
            if isinstance(body, dict):
                ent = body.get("entity") if isinstance(body.get("entity"), dict) else body
                resolved[nid] = {"name": ent.get("name") or ent.get("content"),
                                 "type": ent.get("type") or ent.get("label"),
                                 "kind": ent.get("kind")}
            else:
                resolved[nid] = {"error": r.get("error") or "unparsed"}
        report["resolved_ids"] = resolved
        for c in claimed:
            c["src"] = (resolved.get(c["src_id"]) or {}).get("name")
            c["dst"] = (resolved.get(c["dst_id"]) or {}).get("name")
            c["src_kind"] = (resolved.get(c["src_id"]) or {}).get("kind")
            c["dst_kind"] = (resolved.get(c["dst_id"]) or {}).get("kind")
        report["claimed_edges"] = claimed

        # (c) the product's own ownership reader per PERSON SUBJECT
        people_ids = {}
        for nid, n in nodes.items():
            if n.get("type") == "subject" and n.get("kind") == "core:naturalPerson":
                people_ids[n.get("content") or nid] = nid
        gov = {}
        for nm, nid in people_ids.items():
            gov[nm] = {"id": nid,
                       "reply": await _call(client, "tortoise_get_governance",
                                            {"subject_id": nid})}
        # also the team subjects, to see what the reader does with a non-person
        for nid, n in nodes.items():
            if n.get("type") == "subject" and n.get("kind") == "core:team":
                nm = n.get("content")
                gov[f"TEAM::{nm}"] = {
                    "id": nid,
                    "reply": await _call(client, "tortoise_get_governance",
                                         {"subject_id": nid})}
        report["governance"] = gov

        # (d) retrieval by structure
        ret = {}
        for key, q in QUESTIONS:
            ret[key] = {
                "question": q,
                "search": await _call(client, "tortoise_search",
                                      {"query": q, "limit": 8}),
                "recall_state": await _call(client, "tortoise_recall",
                                            {"query": q, "mode": "state",
                                             "limit": 8}),
            }
        report["probes"]["retrieval"] = ret

        # (e) the enumeration door, per type
        report["probes"]["list_by_kind"] = {
            kind: await _call(client, "tortoise_list_knowledge",
                              {"kind": kind, "limit": 100})
            for kind in ("object", "subject", "claim", "topic")
        }

    OUT.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
