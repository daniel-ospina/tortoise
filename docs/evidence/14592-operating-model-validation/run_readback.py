#!/usr/bin/env python
"""#14592 Phase D — step 2: read the fresh graph back, MCP ONLY.

METHOD RULE: this file talks to the **real Tortoise MCP server** over stdio —
the same `python -m tortoise.mcp_server` a customer's agent connects to. It does
not import `TortoiseSDK`, does not build Cypher, and does not open the record
store: every answer recorded below is whatever a tool in `TOOL_REGISTRY`
returned.

The owner was explicit — *"search is using our Tortoise MCP. if you created some
custom script outside the MCP/SDK then that's just stupid"* — so the search half
of this validation is answered by the `tortoise_search` MCP tool and nothing else.

Tool arguments are taken from the server's OWN `inputSchema` (dumped to
`out/tool-surface.json`), not guessed: an earlier revision guessed and the
resulting `Unknown tool` / `unexpected_keyword_argument` errors were this file's
bug, not the product's — they are NOT recorded as findings.
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
GRAPH = "xv14592_operating_model"

#: One probe per element of the operating model the owner asked about. `expect`
#: names the corpus fact a correct graph must be able to answer with.
#: The corpus decision this probe must land on — the extracted SENTENCE, not a
#: substring. A raw episodic turn also carries the word "Decision"
#: (`[assistant] Decision: we revert to NordicFreight…`), so neither a substring
#: scan nor a `startswith` preference is a selection rule: both were measured
#: `False` on every row this corpus produced, which left the choice to whatever
#: the search listed first. In the committed run it settled on `doc-05_t5` — an
#: episodic turn id — and `expand`/`explore` then answered `[]` for a graph that
#: holds 8 IMPL rows on the real decision point. `pick_decision_point` matches
#: this sentence by identity and `decision_probe_findings` is the guard.
DECISION_CLAIM = "We choose ColdPath Logistics as our delivery partner"

#: Keys this producer writes unconditionally. `stamp_provenance.py` reads this to
#: tell a producer-written artifact from a hand-carried one.
WRITTEN_KEYS = (
    "graph_name", "db_uri", "provenance", "ok", "tool_surface", "layers",
    "search", "entities", "decision_point", "expand", "explore",
    "decision_probe_findings", "confidence", "recall",
)

PROBES = [
    ("decision", "Which delivery partner did we choose, and why did we choose them?",
     "ColdPath Logistics — 9pm cutoff meets the morning restaurant slots and it "
     "holds a validated cold-chain certificate (doc-01)"),
    ("constraint", "What must the cold chain stay below, and what does the auditor require?",
     "below 4 degrees Celsius at all times; a retained temperature log for every "
     "delivery (doc-02)"),
    ("goal", "What is our goal for restaurant accounts, and what limits it?",
     "40 restaurant accounts by December; delivery capacity / morning slots limit "
     "it (doc-03)"),
    ("role", "Who owns the cold-chain temperature log?",
     "Marta Reyes (doc-04)"),
    ("contradiction", "Did ColdPath fail its cold-chain audit, and what did we do about it?",
     "yes — ColdPath failed the audit; we reverted to NordicFreight (doc-05, "
     "contradicting the doc-01 decision)"),
    ("naming", "What companies and people are involved?",
     "NordicFreight, ColdPath Logistics, Marta Reyes, Dev Patel, Priya Nair"),
]


def reply_rows(reply, keys=("result", "results", "relationships", "nodes", "items")):
    """Normalise an MCP reply to a list of rows.

    The server answers in three shapes for the same family of call — a dict with
    a row list under one key, a list of row dicts, or a list holding ONE JSON
    string (fastmcp's text content). This unwraps all three, and returns `[]`
    for anything else, so a caller cannot mistake "no rows" for "no answer"
    without the guard below.
    """
    if isinstance(reply, str):
        try:
            return reply_rows(json.loads(reply), keys)
        except (ValueError, TypeError):
            return []
    if isinstance(reply, dict):
        for key in keys:
            if isinstance(reply.get(key), list):
                return reply[key]
        return []
    if isinstance(reply, list):
        if len(reply) == 1 and isinstance(reply[0], str):
            return reply_rows(reply[0], keys)
        return [row for row in reply if isinstance(row, dict)]
    return []


def _norm(text: object) -> str:
    """Whitespace- and case-normalised content, for identity comparison."""
    return " ".join(str(text or "").lower().split())


def pick_decision_point(rows, claim: str = DECISION_CLAIM):
    """The EXTRACTED decision statement — by identity, never by search order.

    Returns ``None`` (a refusal) rather than a near-miss. The previous revision
    ranked a `startswith` preference ahead of `contains`; measured across every
    row this corpus produced, that term was **False for all of them** (the
    extracted statement *contains* the marker, it does not begin with it), so
    the tie fell through to the search's own order — the defect that selected
    `doc-05_t5`. Identity against the known claim sentence is the discriminator
    the corpus provides, and it is the only one used here.
    """
    target = _norm(claim)
    for row in rows or []:
        if isinstance(row, dict) and _norm(row.get("content")) == target:
            return row
    return None


def decision_probe_findings(decision_point, expand, explore, surfaced_by_probe=None):
    """What is WRONG with this relation read — empty list means it read something.

    `expand_relationships` answering `[]` is a failed read, not a finding about
    the graph: it is exactly how the committed run recorded "the decision has no
    relations" when the id it asked about (`doc-05_t5`) was an episodic turn and
    never a Point.
    """
    issues: list[str] = []
    if surfaced_by_probe is False:
        issues.append(
            "the decision probe's own hits did not surface the decision statement "
            "(the targeted follow-up search did — record both reads)")
    if not decision_point:
        issues.append("no decision point was selected — expand/explore never ran")
    if not reply_rows((expand or {}).get("reply")):
        issues.append(f"expand_relationships({decision_point or ''}) returned no relations")
    if not reply_rows((explore or {}).get("reply"),
                      keys=("result", "relationships", "nodes")):
        issues.append(
            f"explore_connections({decision_point or ''}) returned no connected nodes")
    return issues


def _provenance() -> dict:
    body = Path(__file__).read_bytes()
    return {
        "probe_path": str(Path(__file__).resolve().relative_to(WT)),
        "probe_sha256": hashlib.sha256(body).hexdigest(),
        "graph": GRAPH,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def verdict(report: dict) -> int:
    """0 only when the guard found nothing wrong with the relation read.

    This is the exit code `run()` returns and the `ok` field the artifact
    carries. The previous revision PRINTED the findings and returned 0
    unconditionally, so an empty relation read was recorded as a successful one
    — making the failure *visible* in the artifact, which is real value, but not
    *impossible*, which is what the commit message claimed.
    """
    return 1 if report.get("decision_probe_findings") else 0


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


async def run() -> int:
    from fastmcp import Client
    from fastmcp.client.transports import PythonStdioTransport

    env = dict(os.environ)
    env["PYTHONPATH"] = str(WT)
    # `python -m tortoise.mcp_server` takes its graph from TORTOISE_DB_URI.
    base = env["TORTOISE_DB_URI"]
    env["TORTOISE_DB_URI"] = base.rsplit("/", 1)[0] + "/" + GRAPH

    transport = PythonStdioTransport(
        "tortoise/mcp_server.py", python_cmd=sys.executable, env=env, cwd=str(WT))

    report: dict = {"graph_name": GRAPH,
                    "db_uri": env["TORTOISE_DB_URI"].split("@")[-1],
                    "provenance": _provenance()}

    async with Client(transport, timeout=300) as client:
        tools = {t.name: t for t in await client.list_tools()}
        (OUT / "tool-surface.json").write_text(json.dumps(
            {n: {"description": (t.description or "")[:400],
                 "required": (t.inputSchema or {}).get("required", []),
                 "params": list((t.inputSchema or {}).get("properties") or {})}
             for n, t in sorted(tools.items())}, indent=2) + "\n")
        report["tool_surface"] = {"count": len(tools), "names": sorted(tools)}
        print(f"=== MCP tool surface: {len(tools)} tools ===")

        # ── (a) the five ontology layers, via the product's own reads ────
        report["layers"] = {}
        for tool, args in [
            ("tortoise_graph_overview", {}),
            ("tortoise_check_structure", {}),
            ("tortoise_manage_sources", {"action": "list"}),
        ]:
            r = await _call(client, tool, args)
            report["layers"][tool] = r
            print(f"\n=== layer read: {tool} ===")
            print(json.dumps(r, indent=1, default=str)[:4000])

        # ── (b) "is search retrieving the proper things" ─────────────────
        report["search"] = []
        for label, q, expect in PROBES:
            r = await _call(client, "tortoise_search", {"query": q, "limit": 8})
            row: dict = {**r, "element": label, "query": q, "expected": expect}
            row["search_knowledge"] = await _call(
                client, "tortoise_search_knowledge", {"query": q, "limit": 8})
            row["entry_points"] = await _call(
                client, "tortoise_suggest_entry_points", {"query": q, "limit": 5})
            report["search"].append(row)
            print(f"\n=== search [{label}] {q!r} ===")
            print(json.dumps(row.get("reply", row), indent=1, default=str)[:4000])

        # ── (c) entity reads (top-level named things) ────────────────────
        report["entities"] = []
        for typ, limit in [("object", 40), ("subject", 40), ("source", 20),
                           ("event", 20), ("point", 60)]:
            r = await _call(client, "tortoise_list_knowledge",
                            {"kind": typ, "limit": limit})
            r["kind"] = typ
            report["entities"].append(r)
            print(f"\n=== list_knowledge kind={typ} ===")
            print(json.dumps(r.get("reply", r), indent=1, default=str)[:3000])

        # ── (d) relations: expand the decision point in full ─────────────
        # The decision probe's OWN hits are the first source. If they do not
        # surface the decision statement — §4's result for this run — the driver
        # asks for that sentence directly and records BOTH reads; it never
        # falls back to `graph_overview`'s `recent` list (the previous revision's
        # second arm) nor to a pasted id.
        dec = pick_decision_point(reply_rows(
            (report["search"][0] or {}).get("reply")))
        surfaced_by_probe = dec is not None
        if dec is None:
            report["decision_search"] = await _call(
                client, "tortoise_search",
                {"query": "we choose ColdPath Logistics as our delivery partner",
                 "limit": 8})
            dec = pick_decision_point(
                reply_rows(report["decision_search"].get("reply")))
        dec_id = (dec or {}).get("id") or (dec or {}).get("point_id")
        report["decision_point"] = dec_id
        report["decision_point_kind"] = (
            (dec or {}).get("pointKind") or (dec or {}).get("point_kind"))
        report["expand"] = await _call(
            client, "tortoise_expand_relationships", {"point_id": dec_id or ""})
        print("\n=== expand_relationships (the decision point) ===")
        print(json.dumps(report["expand"], indent=1, default=str)[:5000])

        report["explore"] = await _call(
            client, "tortoise_explore_connections",
            {"node_id": dec_id or "", "hops": 2, "include_content": True}) \
            if dec_id else {"skipped": "no decision point id"}
        print("\n=== explore_connections (decision, 2 hops) ===")
        print(json.dumps(report["explore"], indent=1, default=str)[:5000])

        # The guard, recorded IN the artifact: an empty relation read must never
        # again be written down as a successful one.
        report["decision_probe_findings"] = decision_probe_findings(
            dec_id, report["expand"], report["explore"],
            surfaced_by_probe=surfaced_by_probe)
        for issue in report["decision_probe_findings"]:
            print(f"!! DECISION PROBE INCOMPLETE: {issue}")

        # ── (e) confidence / EP per contested probe ──────────────────────
        report["confidence"] = []
        for label, q, _e in PROBES:
            r = await _call(client, "tortoise_check_confidence",
                            {"topic": q, "limit": 6})
            r["element"] = label
            report["confidence"].append(r)
            print(f"\n=== check_confidence [{label}] ===")
            print(json.dumps(r.get("reply", r), indent=1, default=str)[:2500])

        # ── (f) recall:state — what the graph *believes*, with EP ────────
        report["recall"] = []
        for label, q, _e in PROBES:
            r = await _call(client, "tortoise_recall",
                            {"query": q, "mode": "state", "limit": 6})
            r["element"] = label
            report["recall"].append(r)
            print(f"\n=== recall:state [{label}] ===")
            print(json.dumps(r.get("reply", r), indent=1, default=str)[:3000])

    findings = report["decision_probe_findings"]
    code = verdict(report)
    report["ok"] = code == 0
    (OUT / "readback.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n")
    print(f"\nwrote {OUT / 'readback.json'}")
    if code:
        print(f"!! readback NOT OK: {len(findings)} decision-probe finding(s) — "
              "the relation read failed, so this is not a successful run")
    return code


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
