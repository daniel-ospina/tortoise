#!/usr/bin/env python3
"""#9664 / #9340 step-11 ugly-case probe — the WRITE half.

WHY THIS IS IN THE TREE (and not in /tmp)
-----------------------------------------
The measurement this script produces is cited by `verdict-ledger.yml` rows U1/U2/U4
(they cite `../step11-write-half-trace.txt`, which is the transcript THIS script
emits — run it and the blocks match line for line, request echoes included).
An evidence artifact whose only reproduction path is a file on the authoring box is
not evidence — a reviewer's clone cannot run it, and `tools/capstone_ledger.py`
refuses a `cmd:` entry naming a path outside the tree. So the script lives beside
the trace it produces (`../step11-write-half-trace.txt`).

WHAT IT DOES
------------
1. `tortoise_list_graphs` — what the org can actually reach.
2. NEGATIVE CONTROL: `tortoise_create_point` naming an explicit NON-DEFAULT graph
   that does not exist. Expected: refused BY OUR RESOLVER (`tortoise/mcp_auth.py`),
   naming the graphs that ARE reachable — never a silent write into `default`.
3. POSITIVE CONTROL: write a point into the graph that DOES exist and read it back,
   so "the probe string is absent" cannot be explained by "search returns nothing on
   a fresh org".
4. RE-CHECK the negative probe string is absent — the fail-open half.

The probe strings are unique on purpose: a silent fallback write is only detectable
if the content it would have written is searchable.

AUTH
----
A free org minted by the product's own zero-email path, `uv run tortoise signup`
(#663) — the headless-agent path. It writes `~/.tortoise/credentials.json`; no
secret is stored in this file. The served MCP surface is `/mcp`, the same mount a
hosted connector uses; only the VENUE differs from the 2026-10-26 run, whose writes
OpenAI stopped before any Tortoise code ran (#10084).

USAGE
-----
    uv run tortoise signup                            # once per box (2/IP/24h)
    uv run python docs/evidence/9664-capstone/probes/u1_probe.py

Read-only with respect to every shared graph: the only write it performs is into
the caller's own `default`, and it deletes nothing.
"""
from __future__ import annotations

import json
import os
import sys

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

NEG_GRAPH = "capstone-9664-nod"
NEG_TEXT = "capstone-9664 U1 probe: explicit non-default graph (must not exist)"
POS_TEXT = "capstone-9664 positive control: this string MUST be findable"


def _creds() -> tuple[str, str]:
    path = os.path.expanduser("~/.tortoise/credentials.json")
    if not os.path.exists(path):
        sys.exit(f"no credential at {path} — run `uv run tortoise signup` first")
    with open(path) as fh:
        cred = json.load(fh)
    base = (cred.get("api_url") or "https://api.premiselabs.co").rstrip("/")
    return cred["api_key"], base + "/mcp"


async def _call(session: ClientSession, name: str, args: dict) -> tuple[str, str]:
    # ECHO THE REQUEST: the recorded transcript in ../step11-write-half-trace.txt
    # carries these lines, so a re-run must emit them or the trace stops being a
    # transcript and becomes narration (review round 3).
    print(f"{name} {json.dumps(args)}")
    result = await session.call_tool(name, args)
    text = "\n".join(getattr(c, "text", "") for c in result.content)
    return ("ERROR" if result.isError else "OK"), text


async def main() -> int:
    key, url = _creds()
    async with streamablehttp_client(
        url, headers={"Authorization": f"Bearer {key}"}
    ) as conn, ClientSession(conn[0], conn[1]) as session:
        await session.initialize()

        print("== 1. tortoise_list_graphs (what this org can reach) ==")
        status, text = await _call(session, "tortoise_list_graphs", {})
        print(status, text[:700], "\n")

        print(f"== 2. NEGATIVE CONTROL — write with explicit non-default graph {NEG_GRAPH!r} ==")
        status, neg = await _call(session, "tortoise_create_point",
                                  {"kind": "statement", "content": NEG_TEXT, "graph": NEG_GRAPH})
        print(status, neg[:900], "\n")

        print("== 3. POSITIVE CONTROL — write into 'default', then read it back ==")
        status, pos = await _call(session, "tortoise_create_point",
                                  {"kind": "statement", "content": POS_TEXT})
        print(status, pos[:300], "\n")
        status_pos, found = await _call(session, "tortoise_search",
                                        {"query": "positive control", "limit": 5})
        print(status_pos, found[:700], "\n")

        print("== 4. FAIL-OPEN HALF — the negative probe string must be absent ==")
        status_neg, absent = await _call(session, "tortoise_search",
                                         {"query": "capstone-9664 U1 probe", "limit": 5})
        print(status_neg, repr(absent[:400]))

    # The verdict is mechanical so a re-run cannot be read optimistically, and it
    # consults EVERY control — including the positive one, whose only job is to
    # make "absent" mean something. Without it, a broken search on a fresh org
    # reports REFUSED-AND-ABSENT for a fail-open half nobody measured (caught by
    # review round 2).
    negative_refused = neg.startswith("No graph") and NEG_GRAPH in neg
    positive_write_ok = pos.startswith("{")           # create_point returns the point JSON
    positive_found = status_pos == "OK" and POS_TEXT[:40] in found
    negative_absent = (
        status_neg == "OK"
        and NEG_TEXT[:40] not in absent
        # the control point proves the query space is live, so an empty-looking
        # result set is a real absence rather than a dead index
        and POS_TEXT[:40] in absent
    )
    ok = negative_refused and positive_write_ok and positive_found and negative_absent
    print(f"\ncontrols: refused={negative_refused} pos_write={positive_write_ok} "
          f"pos_found={positive_found} neg_absent={negative_absent}")
    print("PROBE-RESULT:", "REFUSED-AND-ABSENT" if ok else "UNEXPECTED")
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(anyio.run(main))
