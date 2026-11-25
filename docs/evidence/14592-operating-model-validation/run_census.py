#!/usr/bin/env python
"""#14592 Phase D — step 3: the operating-model census, MCP ONLY.

Step 2 answered "does search find the corpus facts". This step answers the
structural half the brief asks for:

  * which Object / Subject / Event / Source nodes exist, and does each carry a
    corpus fact?
  * which relation classes were written (IMPL / NAND / performs / owns /
    extractedFrom), and were any invented?
  * is EP present and sane per object?
  * is the doc-05 contradiction represented as a real epistemic conflict?

Every answer comes from a `tortoise_*` MCP tool. Nothing here imports the SDK,
builds Cypher, or touches the record store.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
WT = Path(__file__).resolve().parents[3]
GRAPH = "xv14592_operating_model"


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


def _rows(reply):
    """Normalise a tool reply to a list of dict rows."""
    if isinstance(reply, dict):
        for k in ("result", "results", "nodes", "items", "points", "data"):
            if isinstance(reply.get(k), list):
                return reply[k]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            try:
                v = json.loads(reply[0])
                return v if isinstance(v, list) else []
            except Exception:
                return []
        return [r for r in reply if isinstance(r, dict)]
    return []


def pick_point(rows, marker):
    """The `statement` Point the marker names — never a raw turn, never a near-miss.

    Ranked, so the answer does not depend on the search's order: a statement
    whose content STARTS WITH the marker wins over one that merely contains it,
    and a `statement` always wins over a raw turn (a turn's `Decision:` prefix is
    the transcript's, not the extractor's).
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


async def _pick(client, out, key, query, marker):
    """Search by content and pick the Point; `None` is recorded, never guessed.

    `limit=8`, not 5: the committed run's decision probe did not surface the
    decision inside its first five hits, so its expand arm recorded
    `decision_point_id: null` and never ran — a failed read that the artifact
    presented as a graph fact.
    """
    r = await _call(client, "tortoise_search", {"query": query, "limit": 8})
    out[f"{key}_search"] = r
    point = pick_point(_rows(r.get("reply")), marker)
    pid = (point or {}).get("id") or (point or {}).get("point_id")
    out[f"{key}_point_id"] = pid
    print(f"\n=== {key} point: {pid} — {str((point or {}).get('content'))[:90]}")
    return pid


def relation_rows(reply, keys=("result", "relationships", "nodes")):
    """Rows from an expansion reply, in the three shapes the server uses."""
    if isinstance(reply, str):
        try:
            return relation_rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for k in keys:
            if isinstance(reply.get(k), list):
                return reply[k]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return relation_rows(reply[0], keys)
        return [row for row in reply if isinstance(row, dict)]
    return []


async def run() -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH
    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    out: dict = {"graph": GRAPH}
    async with Client(transport, timeout=300) as client:
        # ── 1. every Object / Subject / Event, full-scan ────────────────
        for typ in ("object", "subject", "event", "source"):
            r = await _call(client, "tortoise_search",
                            {"entity_type": typ, "limit": 100})
            out[f"fullscan_{typ}"] = r
            rows = _rows(r.get("reply"))
            print(f"\n=== full-scan {typ}: {len(rows)} rows ===")
            for row in rows[:60]:
                print(f"  {row.get('id','')[:14]:<14} {row.get('point_kind',''):<24} "
                      f"{str(row.get('content',''))[:110]}")

        # ── 2. the operator census: IMPL / NAND / others ────────────────
        r = await _call(client, "tortoise_query",
                        {"kind": "operator", "limit": 200})
        out["query_operators"] = r
        print(f"\n=== tortoise_query kind=operator ===")
        print(json.dumps(r, indent=1, default=str)[:1500])

        # ── 3. per-object EP + degree, via recall (graph beliefs) ───────
        # recall:state on the operating-model vocabulary returns objects with
        # EP, degree and their rank — the "is EP sane per object" read.
        for label, q in [
            ("people", "Marta Reyes Dev Patel Priya Nair who owns what"),
            ("vendors", "NordicFreight ColdPath Logistics delivery partner"),
            ("constraints", "cold chain below four degrees temperature log licence"),
            ("goal", "forty restaurant accounts by December growth goal"),
            ("decision", "we choose ColdPath Logistics as our delivery partner"),
            ("reversal", "revert to NordicFreight ColdPath failed the audit"),
        ]:
            r = await _call(client, "tortoise_recall",
                            {"query": q, "mode": "state", "limit": 5})
            out[f"recall_{label}"] = r
            print(f"\n=== recall:state [{label}] ===")
            for row in _rows(r.get("reply")):
                ep = row.get("ep") or {}
                rr = row.get("recall_ranking") or {}
                print(f"  {str(row.get('content',''))[:95]}")
                print(f"     kind={row.get('point_kind')} conf={ep.get('confidence_mean')} "
                      f"ep_state={ep.get('ep_state')} contested={row.get('contested')} "
                      f"degree={rr.get('degree')} subject={(row.get('subject') or {}).get('name')}")

        # ── 4. the contradiction: is doc-05 wired against doc-01? ───────
        # Search both sides, then expand the doc-01 decision point's edges.
        # Every id is DISCOVERED by content and a failed pick is recorded, so a
        # missing id can no longer be written down as a successful read.
        dec_id = await _pick(client, out, "decision",
                             "we choose ColdPath Logistics as our delivery partner",
                             "choose coldpath")

        rev_id = await _pick(client, out, "reversal",
                             "we revert to NordicFreight and stop using ColdPath",
                             "we revert to nordicfreight")

        out["relation_read_findings"] = []
        if not dec_id:
            out["relation_read_findings"].append(
                "no decision point was selected — expand/explore never ran")

        if dec_id:
            out["expand_decision"] = await _call(
                client, "tortoise_expand_relationships", {"point_id": dec_id})
            print("\n=== expand_relationships(decision) ===")
            print(json.dumps(out["expand_decision"].get("reply"),
                             indent=1, default=str)[:4000])
            out["explore_decision"] = await _call(
                client, "tortoise_explore_connections",
                {"node_id": dec_id, "hops": 2, "include_content": True})
            print("\n=== explore_connections(decision, 2 hops) ===")
            print(json.dumps(out["explore_decision"].get("reply"),
                             indent=1, default=str)[:4000])
            if not relation_rows(out["expand_decision"].get("reply")):
                out["relation_read_findings"].append(
                    f"expand_relationships({dec_id}) returned no relations")
            if not relation_rows(out["explore_decision"].get("reply")):
                out["relation_read_findings"].append(
                    f"explore_connections({dec_id}) returned no connected nodes")

        if rev_id:
            out["expand_reversal"] = await _call(
                client, "tortoise_expand_relationships", {"point_id": rev_id})
            print("\n=== expand_relationships(reversal) ===")
            print(json.dumps(out["expand_reversal"].get("reply"),
                             indent=1, default=str)[:3000])
            if not relation_rows(out["expand_reversal"].get("reply")):
                out["relation_read_findings"].append(
                    f"expand_relationships({rev_id}) returned no relations")

        for issue in out["relation_read_findings"]:
            print(f"!! RELATION READ INCOMPLETE: {issue}")

        # ── 5. provenance: can the product trace a claim to its source? ──
        out["provenance"] = await _call(
            client, "tortoise_check_confidence",
            {"topic": "cold chain constraint", "include_provenance": True, "limit": 5})
        print("\n=== check_confidence(include_provenance) ===")
        print(json.dumps(out["provenance"].get("reply"),
                         indent=1, default=str)[:3000])

    (OUT / "census.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'census.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
