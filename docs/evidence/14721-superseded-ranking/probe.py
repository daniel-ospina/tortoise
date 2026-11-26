#!/usr/bin/env python
"""#14721 — the before/after measurement of the contradiction probe.

METHOD RULE (inherited from #14592/#14606, reused not rebuilt): every read here
talks to the **real Tortoise MCP server** over stdio — `python -m tortoise.mcp_server`
— and every write in `run.sh` goes through `TortoiseSDK`. Nothing here builds
Cypher, opens the record store, or imports a private search function.

Usage (see run.sh for the full recipe):

    probe.py --label before            # rank order only, read-only
    probe.py --label after             # same probes, post-fix read path
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
WT = Path(__file__).resolve().parents[3]
GRAPH = "xv14592_operating_model"

#: The corpus's contradiction question, verbatim from the #14606 report's §4 probe
#: table. Its answer is doc-05's: ColdPath failed the audit and we reverted.
CURRENCY_Q = "Did ColdPath fail its cold-chain audit, and what did we do about it?"

#: The history-shaped form of the same question: the caller is asking what the
#: record USED to say and what replaced it. A correct product answers with the
#: superseded claim, LABELLED — never silently with only the current one.
HISTORY_Q = "What did we originally decide about our delivery partner, and what superseded it?"

#: The corpus fact each probe must land on.
CURRENT_EXPECT = ("we reverted to NordicFreight", "doc-05")
STALE_EXPECT = ("validated cold-chain certificate", "doc-01")


def _row(reply: dict) -> list[dict]:
    for k in ("results", "result", "rows", "items"):
        v = reply.get(k) if isinstance(reply, dict) else None
        if isinstance(v, list):
            return v
    return []


def _brief(row: dict) -> dict:
    hits = row.get("hits") or row.get("results") or row.get("reply") or {}
    if isinstance(hits, dict):
        hits = _row(hits)
    out = []
    for i, h in enumerate(hits or [], 1):
        if not isinstance(h, dict):
            continue
        out.append({
            "rank": i,
            "id": h.get("id"),
            "content": (h.get("content") or "")[:160],
            "status": h.get("status"),
            "superseded_by": h.get("superseded_by"),
            "superseded_at": h.get("superseded_at") or h.get("expired_at"),
            "end_state": h.get("end_state"),
            "scores": h.get("scores"),
        })
    return {"n": len(out), "rank_order": out}


async def run(label: str) -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH

    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    report: dict = {"label": label, "graph": GRAPH,
                    "db_uri": env["TORTOISE_DB_URI"].split("@")[-1]}
    async with Client(transport, timeout=300) as client:
        async def call(tool: str, args: dict) -> dict:
            try:
                res = await client.call_tool(tool, args)
                payload = getattr(res, "data", None)
                if payload is None:
                    payload = getattr(res, "content", None)
                return {"tool": tool, "args": args, "reply": payload}
            except Exception as exc:  # noqa: BLE001
                return {"tool": tool, "args": args,
                        "error": f"{type(exc).__name__}: {exc}"}

        report["currency"] = await call(
            "tortoise_search", {"query": CURRENCY_Q, "limit": 8})
        report["history"] = await call(
            "tortoise_search", {"query": HISTORY_Q, "limit": 8})
        # The product's own "what ended, and how" read — the labelled half.
        report["ended"] = await call("tortoise_ended", {"limit": 20})

    report["currency_rank"] = _brief(report["currency"])
    report["history_rank"] = _brief(report["history"])

    def landed(rows: list[dict], needle: str) -> dict:
        for r in rows:
            if needle in (r.get("content") or "").lower():
                return {"rank": r["rank"], "content": r["content"],
                        "status": r.get("status"),
                        "superseded_by": r.get("superseded_by")}
        return {"rank": None}

    report["verdict"] = {
        "current_in_currency": landed(report["currency_rank"]["rank_order"],
                                      CURRENT_EXPECT[0]),
        "stale_in_currency": landed(report["currency_rank"]["rank_order"],
                                    STALE_EXPECT[0]),
        "stale_in_history": landed(report["history_rank"]["rank_order"],
                                   STALE_EXPECT[0]),
        "current_in_history": landed(report["history_rank"]["rank_order"],
                                     CURRENT_EXPECT[0]),
    }
    (OUT / f"{label}.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report["currency_rank"], indent=1)[:2500])
    print("\n--- history ---")
    print(json.dumps(report["history_rank"], indent=1)[:2500])
    print("\n--- verdict ---")
    print(json.dumps(report["verdict"], indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    a = ap.parse_args()
    return asyncio.run(run(a.label))


if __name__ == "__main__":
    raise SystemExit(main())
