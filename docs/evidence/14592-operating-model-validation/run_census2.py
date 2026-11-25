#!/usr/bin/env python
"""#14592 Phase D — step 4: the claim / confidence / structure reads, MCP ONLY.

This is the PRODUCER for `out/census2.json`. It exists because the first cut of
this bundle committed that artifact without the code that made it — the report
shipped `run_ingest.py` / `run_readback.py` / `run_census.py` as its
"reproducible" path while three of the report's load-bearing evidence files
(`census.json`'s decision arm, `census2.json`, `census3.json`) were produced by ad
hoc code that was never committed. A reader could not regenerate them and could
not tell which call each number came from.

Three calls, recorded verbatim with their arguments:

  * `tortoise_list_knowledge(kind="claim")` — the claim door, the population the
    report's G5 statement about `list_knowledge` is measured against;
  * `tortoise_check_confidence(claim_id="", include_provenance=True)` — the
    unfiltered confidence read G9 compares its `topic=` probes to;
  * `tortoise_check_structure()` — the structural validator's verdict on a graph
    holding two mutually exclusive live decisions (G1/G5).

Like its siblings, the run carries a VERDICT: `ok` in the artifact and a non-zero
exit when a call refused or answered nothing, so a receipt whose claim door,
confidence read or structure check failed cannot be committed as a successful
run. An empty `tortoise_check_structure` answer is the validator reporting
nothing wrong — that is a RESULT, and `verdict()` treats the envelope, not the
row count, as the signal.

Run it in the SAME sitting as `run_ingest.py`: the graph name is this bundle's
own (`xv14592_operating_model`), and the committed receipts are the durable
evidence — a lane container that has been torn down cannot be re-read.

    ./docs/evidence/14592-operating-model-validation/run.sh \\
        docs/evidence/14592-operating-model-validation/run_census2.py
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

CALLS = [
    ("claims", "tortoise_list_knowledge", {"kind": "claim", "limit": 200}),
    ("conf_one", "tortoise_check_confidence",
     {"claim_id": "", "include_provenance": True}),
    ("structure", "tortoise_check_structure", {}),
]

#: Keys this producer writes unconditionally. `stamp_provenance.py` reads this to
#: tell a producer-written artifact from a hand-carried one.
WRITTEN_KEYS = ("graph", "provenance", "ok", "claims", "conf_one", "structure")


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


def verdict(out: dict) -> int:
    """0 only when every call SUCCEEDED and carried a reply envelope.

    This is the exit code and the `ok` field the artifact carries — the key
    `WRITTEN_KEYS` has been promising since the producer was committed. The
    previous revision declared `ok`, never wrote it, and returned 0
    unconditionally, so a receipt whose claim door, confidence read and structure
    check ALL refused could still be committed as a successful run: the exact
    fail-open shape the other three producers were changed to close.

    The signal is the ENVELOPE, never the row count: `tortoise_check_structure`
    answers `{"result": []}` when the graph has no structural violation, and an
    empty reply there is a result — not a failed read.
    """
    for key, _tool, _args in CALLS:
        receipt = out.get(key) or {}
        if receipt.get("ok") is not True:
            return 1
        reply = receipt.get("reply")
        if not isinstance(reply, (dict, list)) or not reply:
            return 1
    return 0


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

    out: dict = {"graph": GRAPH, "provenance": _provenance()}
    async with Client(transport, timeout=300) as client:
        for key, tool, args in CALLS:
            out[key] = await _call(client, tool, args)
            print(f"\n=== {tool} {json.dumps(args)} ===")
            print(json.dumps(out[key], indent=1, default=str)[:4000])

    code = verdict(out)
    out["ok"] = code == 0
    (OUT / "census2.json").write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'census2.json'}")
    if code:
        print("!! census2 NOT OK: a call refused or answered nothing — this is "
              "not a successful run")
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
