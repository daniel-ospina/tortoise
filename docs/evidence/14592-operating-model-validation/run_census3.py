#!/usr/bin/env python
"""#14592 Phase D — step 5: the relation expansions + source list, MCP ONLY.

This is the PRODUCER for `out/census3.json` — the artifact that carries §5's
headline evidence ("the decision's reasoning is captured correctly") and the
edge evidence for G1 (no cross-document edge reaches doc-05). It was committed
without a producer: the report's stated repro path named only
`run_ingest.py` / `run_readback.py` / `run_census.py`, none of which writes this
file, and the committed artifact carried a HARDCODED point id
(`pt_f0a2b965…`) that no committed code derived.

Two changes make it reproducible:

  * every point id is DISCOVERED by a `tortoise_search` call here, by content —
    never pasted in — so the same script works on a re-run whose ids differ;
  * the artifact self-describes: a `provenance` block names this file and its
    sha256, so a reader can tell which code produced the numbers.

Run it in the SAME sitting as `run_ingest.py` (the graph is this bundle's own
`xv14592_operating_model`; a torn-down lane container cannot be re-read):

    ./docs/evidence/14592-operating-model-validation/run.sh \\
        docs/evidence/14592-operating-model-validation/run_census3.py
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
WT = Path(__file__).resolve().parents[3]
GRAPH = "xv14592_operating_model"

#: (artifact key, search query, marker the content must carry, expansion args
#: builder). The marker is what identifies the point — never its id.
DECISION = ("expand_doc01_decision",
            "we choose ColdPath Logistics as our delivery partner",
            "choose coldpath")
CLAIM = ("expand_cert_claim",
         "ColdPath Logistics holds a validated cold-chain certificate",
         "validated cold-chain certificate")


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


def _rows(reply, keys=("result", "results", "relationships", "nodes", "items")):
    """Unwrap the three reply shapes the server answers with (dict / rows / JSON text)."""
    if isinstance(reply, str):
        try:
            return _rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return _rows(reply[0], keys)
        return [row for row in reply if isinstance(row, dict)]
    return []


def pick_point(rows, marker):
    """The `statement` Point the marker names — never a raw turn, never a near-miss.

    Ranked, so the answer does not depend on the search's order: a statement
    whose content STARTS WITH the marker wins over one that merely contains it
    (doc-05's "Two of ColdPath's vehicles could not show a validated cold-chain
    certificate…" also `contains` the certificate marker), and a `statement`
    always wins over a raw turn.
    """
    best: dict | None = None
    best_rank = -1
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        content = str(row.get("content") or "").lower().strip()
        if marker not in content:
            continue
        kind = str(row.get("pointKind") or row.get("point_kind") or "").lower()
        rank = (2 if content.startswith(marker) else 0) + (1 if kind == "statement" else 0)
        if rank > best_rank:
            best, best_rank = row, rank
    return best


def _provenance() -> dict:
    body = Path(__file__).read_bytes()
    return {
        "probe_path": str(Path(__file__).resolve().relative_to(WT)),
        "probe_sha256": hashlib.sha256(body).hexdigest(),
        "graph": GRAPH,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


async def run() -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH
    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    out: dict = {"graph": GRAPH, "provenance": _provenance(), "resolved": {}}
    async with Client(transport, timeout=300) as client:
        ids: dict = {}
        for key, query, marker in (DECISION, CLAIM):
            r = await _call(client, "tortoise_search", {"query": query, "limit": 8})
            out[f"{key}_search"] = r
            point = pick_point(_rows(r.get("reply")), marker)
            pid = (point or {}).get("id") or (point or {}).get("point_id")
            out["resolved"][key] = pid
            print(f"\n=== resolved {key}: {pid} — {str((point or {}).get('content'))[:90]}")
            if not pid:
                print(f"!! {key} NOT RESOLVED — its expansion cannot run")
                continue
            ids[key] = pid
            out[key] = await _call(
                client, "tortoise_expand_relationships", {"point_id": pid})
            print(f"\n=== expand_relationships({key}) ===")
            print(json.dumps(out[key], indent=1, default=str)[:4000])

        if "expand_doc01_decision" in ids:
            pid = ids["expand_doc01_decision"]
            out["explore_doc01_decision"] = await _call(
                client, "tortoise_explore_connections",
                {"node_id": pid, "hops": 3, "include_content": True})
            print("\n=== explore_connections(decision, 3 hops) ===")
            print(json.dumps(out["explore_doc01_decision"], indent=1,
                             default=str)[:4000])

        out["sources"] = await _call(
            client, "tortoise_list_knowledge", {"kind": "source", "limit": 20})
        print("\n=== list_knowledge kind=source ===")
        print(json.dumps(out["sources"], indent=1, default=str)[:2000])

    (OUT / "census3.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'census3.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
