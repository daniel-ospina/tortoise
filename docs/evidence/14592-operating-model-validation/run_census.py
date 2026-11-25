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

#: Keys the producer writes unconditionally. `stamp_provenance.py` reads this to
#: tell a producer-written artifact from a hand-carried one.
WRITTEN_KEYS = ("graph", "provenance", "decision_search", "reversal_search")


def _provenance() -> dict:
    body = Path(__file__).read_bytes()
    return {
        "probe_path": str(Path(__file__).resolve().relative_to(WT)),
        "probe_sha256": hashlib.sha256(body).hexdigest(),
        "graph": GRAPH,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


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


async def run() -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH
    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    out: dict = {"graph": GRAPH, "provenance": _provenance()}
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
        print("\n=== tortoise_query kind=operator ===")
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
        r = await _call(client, "tortoise_search",
                        {"query": "we choose ColdPath Logistics as our delivery partner",
                         "limit": 5})
        out["decision_search"] = r
        dec = next((x for x in _rows(r.get("reply"))
                    if "choose ColdPath" in str(x.get("content", ""))), None)
        dec_id = (dec or {}).get("id")
        out["decision_point_id"] = dec_id
        print(f"\n=== decision point: {dec_id} — {str((dec or {}).get('content'))[:90]}")

        r2 = await _call(client, "tortoise_search",
                         {"query": "we revert to NordicFreight and stop using ColdPath",
                          "limit": 5})
        out["reversal_search"] = r2
        rev = next((x for x in _rows(r2.get("reply"))
                    if "revert" in str(x.get("content", "")).lower()), None)
        rev_id = (rev or {}).get("id")
        out["reversal_point_id"] = rev_id
        print(f"=== reversal point: {rev_id} — {str((rev or {}).get('content'))[:90]}")

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

        if rev_id:
            out["expand_reversal"] = await _call(
                client, "tortoise_expand_relationships", {"point_id": rev_id})
            print("\n=== expand_relationships(reversal) ===")
            print(json.dumps(out["expand_reversal"].get("reply"),
                             indent=1, default=str)[:3000])

        # ── 5. provenance: can the product trace a claim to its source? ──
        # ⛔ NOT the `provenance` artifact key. This is a CALL RECEIPT for a
        # `check_confidence(include_provenance=True)` read, and naming it
        # `provenance` made `census.json` look like it self-described its
        # producing code when it did not — there was no provenance block in the
        # artifact at all. The receipt keeps its own name; the block lives under
        # `provenance` above, written by `_provenance()`.
        out["confidence_provenance_read"] = await _call(
            client, "tortoise_check_confidence",
            {"topic": "cold chain constraint", "include_provenance": True, "limit": 5})
        print("\n=== check_confidence(include_provenance) ===")
        print(json.dumps(out["confidence_provenance_read"].get("reply"),
                         indent=1, default=str)[:3000])

    code = 0 if out.get("decision_point_id") else 1
    out["ok"] = code == 0
    (OUT / "census.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'census.json'}")
    if code:
        print("!! census NOT OK: the decision search surfaced no row whose content "
              "IS the decision, so no relation read was attempted")
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
